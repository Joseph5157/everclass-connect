import hashlib
import hmac
import json
from datetime import timedelta

import httpx
from cryptography.fernet import Fernet
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from whatsapp import events, phone, services, worker
from whatsapp.graph import GraphAPIError, GraphClient
from whatsapp.models import (
    ApiKey,
    Contact,
    InboundMessage,
    Message,
    MessageTemplate,
    Organization,
    WebhookEvent,
    WhatsAppAccount,
)

KEY = Fernet.generate_key().decode()
SECRET = "test-app-secret"


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def status_payload(wamid, status, phone_number_id="PNID", errors=None):
    value = {"metadata": {"phone_number_id": phone_number_id},
             "statuses": [{"id": wamid, "status": status, "timestamp": "1764140000",
                           **({"errors": errors} if errors else {})}]}
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": value}]}]}


def inbound_payload(text, wamid="wamid.in1", wa_id="919876543210"):
    value = {"metadata": {"phone_number_id": "PNID"},
             "contacts": [{"wa_id": wa_id, "profile": {"name": "Ravi"}}],
             "messages": [{"from": wa_id, "id": wamid, "timestamp": str(int(timezone.now().timestamp())),
                           "type": "text", "text": {"body": text}}]}
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": value}]}]}


class FakeClient:
    """Stands in for GraphClient in worker tests."""

    def __init__(self, error=None):
        self.error, self.calls = error, []

    def __call__(self, account):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def send_template(self, *args):
        self.calls.append(("template", args))
        if self.error:
            raise self.error
        return {"messages": [{"id": f"wamid.out{len(self.calls)}"}]}

    def send_text(self, *args):
        self.calls.append(("text", args))
        return {"messages": [{"id": "wamid.text"}]}


@override_settings(FIELD_ENCRYPTION_KEY=KEY, META_APP_SECRET=SECRET, META_WEBHOOK_VERIFY_TOKEN="verify-me",
                   MESSAGE_MAX_ATTEMPTS=3)
class ConnectTestCase(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Everclass", slug="everclass")
        self.account = WhatsAppAccount.objects.create(
            organization=self.org, waba_id="WABA", phone_number_id="PNID", access_token="secret-token"
        )
        self.template = MessageTemplate.objects.create(
            account=self.account, name="absence_alert", language="en", status="APPROVED", meta_template_id="77"
        )


class TokenAndPhoneTests(ConnectTestCase):
    def test_token_is_encrypted_at_rest(self):
        with connection.cursor() as cur:
            cur.execute("SELECT access_token FROM whatsapp_whatsappaccount WHERE id=%s", [self.account.pk])
            stored = cur.fetchone()[0]
        self.assertNotIn("secret-token", stored)
        self.assertEqual(WhatsAppAccount.objects.get(pk=self.account.pk).access_token, "secret-token")

    def test_phone_normalization(self):
        self.assertEqual(phone.normalize("98765 43210"), "919876543210")
        self.assertEqual(phone.normalize("+91-98765-43210"), "919876543210")
        self.assertEqual(phone.normalize("09876543210"), "919876543210")
        with self.assertRaises(phone.InvalidPhoneNumber):
            phone.normalize("123")


class WebhookViewTests(ConnectTestCase):
    def test_verification_handshake(self):
        url = reverse("meta-webhook")
        ok = self.client.get(url, {"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"})
        self.assertEqual((ok.status_code, ok.content), (200, b"42"))
        bad = self.client.get(url, {"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"})
        self.assertEqual(bad.status_code, 403)

    def test_signed_post_is_stored_unsigned_rejected(self):
        body = json.dumps(status_payload("wamid.x", "sent")).encode()
        url = reverse("meta-webhook")
        bad = self.client.post(url, body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256="sha256=00")
        self.assertEqual(bad.status_code, 403)
        ok = self.client.post(url, body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=sign(body))
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(WebhookEvent.objects.count(), 1)


class QueueTests(ConnectTestCase):
    def test_idempotency_key_dedupes(self):
        first, created1 = services.queue_template(self.account, "9876543210", "absence_alert", "en", ["Ravi"], "k1")
        second, created2 = services.queue_template(self.account, "9876543210", "absence_alert", "en", ["Ravi"], "k1")
        self.assertEqual((first.pk, created1, created2), (second.pk, True, False))

    def test_rejects_opted_out_unapproved_and_closed_window(self):
        Contact.objects.create(account=self.account, wa_id="919999999999", opted_out=True)
        with self.assertRaises(services.QueueError):
            services.queue_template(self.account, "9999999999", "absence_alert", "en")
        self.template.status = "PENDING"
        self.template.save()
        with self.assertRaises(services.QueueError):
            services.queue_template(self.account, "9876543210", "absence_alert", "en")
        with self.assertRaises(services.QueueError):
            services.queue_text(self.account, "9876543210", "hello")


class WorkerTests(ConnectTestCase):
    def test_sends_and_records_wamid(self):
        msg, _ = services.queue_template(self.account, "9876543210", "absence_alert", "en", ["Ravi", "26 Nov"], "k")
        fake = FakeClient()
        worker.run_once(client_factory=fake)
        msg.refresh_from_db()
        self.assertEqual((msg.status, msg.wamid), (Message.Status.ACCEPTED, "wamid.out1"))
        self.assertEqual(fake.calls[0][1], ("PNID", "919876543210", "absence_alert", "en", ["Ravi", "26 Nov"]))

    def test_retryable_error_backs_off_then_fails(self):
        msg, _ = services.queue_template(self.account, "9876543210", "absence_alert", "en", [], "k")
        fake = FakeClient(GraphAPIError("Rate limit", code="130429", retryable=True))
        worker.run_once(client_factory=fake)
        msg.refresh_from_db()
        self.assertEqual((msg.status, msg.attempts), (Message.Status.QUEUED, 1))
        self.assertGreater(msg.next_attempt_at, timezone.now())
        for _ in range(2):
            Message.objects.filter(pk=msg.pk).update(next_attempt_at=timezone.now() - timedelta(seconds=1))
            worker.run_once(client_factory=fake)
        msg.refresh_from_db()
        self.assertEqual((msg.status, msg.attempts), (Message.Status.FAILED, 3))

    def test_permanent_error_fails_immediately(self):
        msg, _ = services.queue_template(self.account, "9876543210", "absence_alert", "en", [], "k")
        worker.run_once(client_factory=FakeClient(GraphAPIError("Bad number", code="131026")))
        msg.refresh_from_db()
        self.assertEqual((msg.status, msg.error_code), (Message.Status.FAILED, "131026"))


class EventTests(ConnectTestCase):
    def _process(self, payload):
        event = WebhookEvent.objects.create(payload=payload)
        worker.process_webhook_events(10)
        event.refresh_from_db()
        self.assertIsNotNone(event.processed_at, event.error)

    def _accepted_message(self):
        msg, _ = services.queue_template(self.account, "9876543210", "absence_alert", "en", [], "k")
        Message.objects.filter(pk=msg.pk).update(status=Message.Status.ACCEPTED, wamid="wamid.1")
        return msg

    def test_status_progression_never_goes_backwards(self):
        msg = self._accepted_message()
        self._process(status_payload("wamid.1", "read"))
        self._process(status_payload("wamid.1", "delivered"))  # arrives late
        msg.refresh_from_db()
        self.assertEqual(msg.status, Message.Status.READ)
        self.assertIsNotNone(msg.delivered_at)

    def test_failed_status_records_error(self):
        msg = self._accepted_message()
        self._process(status_payload("wamid.1", "failed",
                                     errors=[{"code": 131047, "title": "Re-engagement message"}]))
        msg.refresh_from_db()
        self.assertEqual((msg.status, msg.error_code), (Message.Status.FAILED, "131047"))

    def test_inbound_opens_window_and_stop_opts_out(self):
        self._process(inbound_payload("Hello sir"))
        contact = Contact.objects.get(wa_id="919876543210")
        self.assertTrue(contact.service_window_open)
        self.assertEqual(contact.profile_name, "Ravi")
        self._process(inbound_payload(" STOP ", wamid="wamid.in2"))
        contact.refresh_from_db()
        self.assertTrue(contact.opted_out)
        self.assertEqual(InboundMessage.objects.count(), 2)
        self._process(inbound_payload("START", wamid="wamid.in3"))
        contact.refresh_from_db()
        self.assertFalse(contact.opted_out)

    def test_template_status_update(self):
        events.process_event(WebhookEvent(payload={
            "object": "whatsapp_business_account",
            "entry": [{"id": "WABA", "changes": [{"field": "message_template_status_update", "value": {
                "event": "REJECTED", "message_template_id": 77, "message_template_name": "absence_alert",
                "message_template_language": "en", "reason": "INVALID_FORMAT"}}]}],
        }))
        self.template.refresh_from_db()
        self.assertEqual((self.template.status, self.template.rejected_reason), ("REJECTED", "INVALID_FORMAT"))


class ApiTests(ConnectTestCase):
    def setUp(self):
        super().setUp()
        _, self.raw_key = ApiKey.issue(self.org, "erp")

    def test_create_and_get_message(self):
        body = {"to": "9876543210", "idempotency_key": "absence:1:2:2026-11-26",
                "template": {"name": "absence_alert", "language": "en", "body_params": ["Ravi", "26 Nov"]}}
        auth = {"HTTP_AUTHORIZATION": f"Bearer {self.raw_key}"}
        url = reverse("api-create-message")
        created = self.client.post(url, json.dumps(body), content_type="application/json", **auth)
        self.assertEqual(created.status_code, 201)
        again = self.client.post(url, json.dumps(body), content_type="application/json", **auth)
        self.assertEqual((again.status_code, again.json()["id"]), (200, created.json()["id"]))
        got = self.client.get(reverse("api-get-message", args=[created.json()["id"]]), **auth)
        self.assertEqual(got.json()["status"], "queued")

    def test_rejects_bad_key(self):
        res = self.client.post(reverse("api-create-message"), "{}", content_type="application/json",
                               HTTP_AUTHORIZATION="Bearer ecx_nope_nope")
        self.assertEqual(res.status_code, 401)


class GraphClientTests(TestCase):
    def test_send_template_payload_and_error_mapping(self):
        seen = {}

        def handler(request):
            seen["url"], seen["body"] = str(request.url), json.loads(request.content)
            seen["auth"] = request.headers["authorization"]
            return httpx.Response(200, json={"messages": [{"id": "wamid.ok"}]})

        client = GraphClient("tok", version="v25.0", base_url="https://graph.test",
                             transport=httpx.MockTransport(handler))
        client.send_template("PNID", "919876543210", "absence_alert", "en", ["Ravi"])
        self.assertEqual(seen["url"], "https://graph.test/v25.0/PNID/messages")
        self.assertEqual(seen["auth"], "Bearer tok")
        self.assertEqual(seen["body"]["template"]["components"][0]["parameters"], [{"type": "text", "text": "Ravi"}])

        def rate_limited(request):
            return httpx.Response(400, json={"error": {"code": 130429, "message": "Rate limit hit"}})

        client = GraphClient("tok", base_url="https://graph.test", transport=httpx.MockTransport(rate_limited))
        with self.assertRaises(GraphAPIError) as ctx:
            client.send_text("PNID", "91", "hi")
        self.assertTrue(ctx.exception.retryable)
        self.assertEqual(ctx.exception.code, "130429")

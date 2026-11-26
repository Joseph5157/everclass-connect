import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import phone, services
from .models import ApiKey, Message, WebhookEvent, WhatsAppAccount

logger = logging.getLogger(__name__)


# --- Meta webhook ------------------------------------------------------------------------

def valid_signature(body: bytes, header: str) -> bool:
    secret = settings.META_APP_SECRET
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


@csrf_exempt
@require_http_methods(["GET", "POST"])
def meta_webhook(request):
    if request.method == "GET":  # one-time verification when you save the callback URL in Meta
        token = settings.META_WEBHOOK_VERIFY_TOKEN
        if (
            token
            and request.GET.get("hub.mode") == "subscribe"
            and hmac.compare_digest(request.GET.get("hub.verify_token", ""), token)
        ):
            return HttpResponse(request.GET.get("hub.challenge", ""), content_type="text/plain")
        return HttpResponseForbidden("Verification failed")

    if not valid_signature(request.body, request.headers.get("X-Hub-Signature-256", "")):
        logger.warning("Rejected webhook with bad signature")
        return HttpResponseForbidden("Bad signature")
    try:
        payload = json.loads(request.body)
    except ValueError:
        return HttpResponse(status=400)
    # Store and answer fast; the worker does the processing.
    WebhookEvent.objects.create(payload=payload)
    return HttpResponse("OK")


# --- Internal API for other Everclass apps ---------------------------------------------

def _authenticate(request):
    header = request.headers.get("Authorization", "")
    return ApiKey.authenticate(header.removeprefix("Bearer ").strip()) if header.startswith("Bearer ") else None


def _message_json(message):
    return {
        "id": message.pk,
        "to": message.contact.wa_id,
        "kind": message.kind,
        "status": message.status,
        "wamid": message.wamid,
        "idempotency_key": message.idempotency_key,
        "error_code": message.error_code,
        "error_message": message.error_message,
        "created_at": message.created_at.isoformat(),
        "sent_at": message.sent_at and message.sent_at.isoformat(),
        "delivered_at": message.delivered_at and message.delivered_at.isoformat(),
        "read_at": message.read_at and message.read_at.isoformat(),
        "failed_at": message.failed_at and message.failed_at.isoformat(),
    }


@csrf_exempt
@require_POST
def api_create_message(request):
    """POST /api/v1/messages
    {"phone_number_id": optional, "to": "9876543210", "idempotency_key": "...",
     "template": {"name": "absence_alert", "language": "en", "body_params": ["Ravi", "26 Nov"]}}
    or {"to": ..., "text": "..."} (only inside the 24-hour window).
    """
    key = _authenticate(request)
    if key is None:
        return JsonResponse({"error": "Invalid API key"}, status=401)
    try:
        data = json.loads(request.body)
    except ValueError:
        return JsonResponse({"error": "Body must be JSON"}, status=400)

    accounts = WhatsAppAccount.objects.filter(organization=key.organization, status=WhatsAppAccount.Status.ACTIVE)
    if data.get("phone_number_id"):
        accounts = accounts.filter(phone_number_id=data["phone_number_id"])
    account = accounts.first() if accounts.count() == 1 else None
    if account is None:
        return JsonResponse({"error": "Specify a valid phone_number_id for this organization"}, status=400)

    try:
        if template := data.get("template"):
            message, created = services.queue_template(
                account, data.get("to"), template.get("name"), template.get("language", "en"),
                template.get("body_params"), data.get("idempotency_key"),
            )
        elif data.get("text"):
            message, created = services.queue_text(account, data.get("to"), data["text"], data.get("idempotency_key"))
        else:
            return JsonResponse({"error": "Send either template or text"}, status=400)
    except (services.QueueError, phone.InvalidPhoneNumber) as exc:
        return JsonResponse({"error": str(exc)}, status=422)

    return JsonResponse(_message_json(message), status=201 if created else 200)


@require_GET
def api_get_message(request, pk):
    key = _authenticate(request)
    if key is None:
        return JsonResponse({"error": "Invalid API key"}, status=401)
    message = (
        Message.objects.select_related("contact").filter(pk=pk, account__organization=key.organization).first()
    )
    if message is None:
        return JsonResponse({"error": "Not found"}, status=404)
    return JsonResponse(_message_json(message))

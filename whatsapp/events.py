"""Turn stored webhook events into status updates, inbound messages, opt-outs and template changes."""

import logging
from datetime import UTC, datetime

from django.db import transaction
from django.utils import timezone

from .models import Contact, InboundMessage, Message, MessageTemplate, WebhookEvent, WhatsAppAccount

logger = logging.getLogger(__name__)

OPT_OUT_WORDS = {"stop", "unsubscribe", "stop all"}
OPT_IN_WORDS = {"start", "subscribe"}


def _ts(value):
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError):
        return timezone.now()


def process_event(event: WebhookEvent):
    payload = event.payload or {}
    if payload.get("object") != "whatsapp_business_account":
        return
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            field, value = change.get("field"), change.get("value", {}) or {}
            if field == "messages":
                _handle_messages_change(value)
            elif field == "message_template_status_update":
                _handle_template_update(entry.get("id"), value)
            else:
                logger.info("Ignoring webhook field %s", field)


def _handle_messages_change(value):
    phone_number_id = value.get("metadata", {}).get("phone_number_id")
    account = WhatsAppAccount.objects.filter(phone_number_id=phone_number_id).first()
    if account is None:
        logger.warning("Webhook for unknown phone_number_id %s", phone_number_id)
        return
    WhatsAppAccount.objects.filter(pk=account.pk).update(last_webhook_at=timezone.now())

    for status in value.get("statuses", []):
        _apply_status(account, status)

    names = {c.get("wa_id"): c.get("profile", {}).get("name", "") for c in value.get("contacts", [])}
    for message in value.get("messages", []):
        _store_inbound(account, message, names.get(message.get("from"), ""))


def _apply_status(account, status):
    wamid, new = status.get("id"), status.get("status")
    when = _ts(status.get("timestamp"))
    with transaction.atomic():
        msg = Message.objects.select_for_update().filter(account=account, wamid=wamid).first()
        if msg is None:
            logger.info("Status %s for unknown wamid %s", new, wamid)
            return
        if new == "failed":
            error = (status.get("errors") or [{}])[0]
            msg.status = Message.Status.FAILED
            msg.failed_at = when
            msg.error_code = str(error.get("code", ""))
            msg.error_message = (
                error.get("error_data", {}).get("details") or error.get("message") or error.get("title", "")
            )
        elif new in Message.STATUS_RANK:
            if Message.STATUS_RANK.get(msg.status, 0) >= Message.STATUS_RANK[new]:
                return  # late or duplicate webhook; never move backwards
            msg.status = new
            setattr(msg, f"{new}_at", when)
            if new == "read" and not msg.delivered_at:
                msg.delivered_at = when
        else:
            return
        msg.save()


def _store_inbound(account, message, profile_name):
    wa_id = message.get("from")
    received = _ts(message.get("timestamp"))
    contact, _ = Contact.objects.get_or_create(account=account, wa_id=wa_id)
    if profile_name:
        contact.profile_name = profile_name
    if not contact.last_inbound_at or received > contact.last_inbound_at:
        contact.last_inbound_at = received

    kind = message.get("type", "unknown")
    text = (
        message.get("text", {}).get("body")
        or message.get("button", {}).get("text")
        or message.get("interactive", {}).get("button_reply", {}).get("title")
        or ""
    )
    word = text.strip().lower()
    if word in OPT_OUT_WORDS and not contact.opted_out:
        contact.opted_out, contact.opted_out_at = True, timezone.now()
    elif word in OPT_IN_WORDS and contact.opted_out:
        contact.opted_out, contact.opted_out_at = False, None
    contact.save()

    InboundMessage.objects.get_or_create(
        wamid=message.get("id"),
        defaults={"account": account, "contact": contact, "type": kind, "text": text,
                  "payload": message, "received_at": received},
    )


def _handle_template_update(waba_id, value):
    filters = {"account__waba_id": waba_id}
    if value.get("message_template_id"):
        filters["meta_template_id"] = str(value["message_template_id"])
    else:
        filters.update(name=value.get("message_template_name"),
                       language=value.get("message_template_language"))
    updated = MessageTemplate.objects.filter(**filters).update(
        status=value.get("event", "UNKNOWN"),
        rejected_reason=(value.get("reason") or "")[:255] if value.get("reason") != "NONE" else "",
    )
    if not updated:
        logger.info("Template update for unknown template %s; run sync_templates", value)

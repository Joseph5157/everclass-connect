"""Queueing messages. Callers never talk to Meta directly: they add rows to the outbox."""

from django.db import IntegrityError, transaction
from django.utils import timezone

from . import phone
from .models import Contact, Message, MessageTemplate


class QueueError(Exception):
    """The message cannot be queued (opted out, template not approved, window closed...)."""


def get_contact(account, raw_phone):
    wa_id = phone.normalize(raw_phone)
    contact, _ = Contact.objects.get_or_create(account=account, wa_id=wa_id)
    return contact


def _existing(account, key):
    return Message.objects.filter(account=account, idempotency_key=key).first()


def queue_template(account, to, template_name, language="en", body_params=None, idempotency_key=None):
    """Queue an approved template. Same idempotency_key twice returns the first message."""
    key = idempotency_key or f"tpl:{template_name}:{phone.normalize(to)}:{timezone.now().timestamp()}"
    if existing := _existing(account, key):
        return existing, False

    contact = get_contact(account, to)
    if contact.opted_out:
        raise QueueError("Recipient has opted out.")
    template = MessageTemplate.objects.filter(account=account, name=template_name, language=language).first()
    if template is None:
        raise QueueError(f"Unknown template {template_name!r} ({language}). Run sync_templates.")
    if not template.is_sendable:
        raise QueueError(f"Template {template_name!r} is {template.status}, not APPROVED.")

    return _create(account=account, contact=contact, kind=Message.Kind.TEMPLATE, template=template,
                   body_params=[str(p) for p in (body_params or [])], idempotency_key=key)


def queue_text(account, to, text, idempotency_key=None):
    """Free-form text is only allowed inside the 24-hour customer service window."""
    key = idempotency_key or f"txt:{phone.normalize(to)}:{timezone.now().timestamp()}"
    if existing := _existing(account, key):
        return existing, False

    contact = get_contact(account, to)
    if contact.opted_out:
        raise QueueError("Recipient has opted out.")
    if not contact.service_window_open:
        raise QueueError("24-hour window is closed; send an approved template instead.")
    return _create(account=account, contact=contact, kind=Message.Kind.TEXT, text=text, idempotency_key=key)


def _create(**fields):
    try:
        with transaction.atomic():
            return Message.objects.create(**fields), True
    except IntegrityError:  # a concurrent call queued the same key first
        return _existing(fields["account"], fields["idempotency_key"]), False

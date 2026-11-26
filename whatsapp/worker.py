"""Background work using Postgres as the queue (SELECT ... FOR UPDATE SKIP LOCKED). No Redis/Celery."""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from . import events
from .graph import GraphAPIError, GraphClient
from .models import Message, WebhookEvent, WhatsAppAccount

logger = logging.getLogger(__name__)

STALE_SENDING_AFTER = timedelta(minutes=5)


def _lock(queryset):
    if connection.features.has_select_for_update_skip_locked:
        return queryset.select_for_update(skip_locked=True)
    return queryset  # SQLite (local dev/tests): single worker, no row locks


def backoff(attempt):
    """30s, 1m, 2m, 4m, 8m ... capped at 1 hour."""
    return timedelta(seconds=min(30 * 2 ** max(attempt - 1, 0), 3600))


def claim_messages(batch_size):
    now = timezone.now()
    # Rows stuck in SENDING (worker crashed mid-send) go back to the queue.
    Message.objects.filter(status=Message.Status.SENDING, next_attempt_at__lt=now - STALE_SENDING_AFTER).update(
        status=Message.Status.QUEUED
    )
    with transaction.atomic():
        rows = list(
            _lock(
                Message.objects.filter(status=Message.Status.QUEUED, next_attempt_at__lte=now)
                .order_by("next_attempt_at", "id")
            )[:batch_size]
        )
        ids = [m.pk for m in rows]
        Message.objects.filter(pk__in=ids).update(status=Message.Status.SENDING, next_attempt_at=now)
    return list(
        Message.objects.filter(pk__in=ids).select_related("account", "contact", "template")
    )


def send_one(message, client_factory=GraphClient.for_account):
    account = message.account
    if account.status != WhatsAppAccount.Status.ACTIVE:
        return _skip(message, "Account is disconnected.")
    if message.contact.opted_out:
        return _skip(message, "Recipient opted out before sending.")

    message.attempts += 1
    try:
        with client_factory(account) as client:
            if message.kind == Message.Kind.TEMPLATE:
                result = client.send_template(
                    account.phone_number_id, message.contact.wa_id,
                    message.template.name, message.template.language, message.body_params,
                )
            else:
                result = client.send_text(account.phone_number_id, message.contact.wa_id, message.text)
    except GraphAPIError as exc:
        message.error_code, message.error_message = exc.code or str(exc.status or ""), str(exc)[:2000]
        if exc.retryable and message.attempts < settings.MESSAGE_MAX_ATTEMPTS:
            message.status = Message.Status.QUEUED
            message.next_attempt_at = timezone.now() + backoff(message.attempts)
            logger.warning("Retrying message %s in %s: %s", message.pk, backoff(message.attempts), exc)
        else:
            message.status, message.failed_at = Message.Status.FAILED, timezone.now()
            logger.error("Message %s failed: %s", message.pk, exc)
        message.save()
        return message

    message.wamid = (result.get("messages") or [{}])[0].get("id", "")
    message.status = Message.Status.ACCEPTED
    message.error_code = message.error_message = ""
    message.save()
    return message


def _skip(message, reason):
    message.status, message.error_message = Message.Status.SKIPPED, reason
    message.save()
    return message


def process_webhook_events(batch_size):
    with transaction.atomic():
        events_batch = list(
            _lock(WebhookEvent.objects.filter(processed_at__isnull=True, attempts__lt=5).order_by("id"))[
                :batch_size
            ]
        )
        for event in events_batch:
            event.attempts += 1
            try:
                with transaction.atomic():
                    events.process_event(event)
                event.processed_at, event.error = timezone.now(), ""
            except Exception as exc:  # keep the raw event for replay; log and move on
                logger.exception("Webhook event %s failed", event.pk)
                event.error = repr(exc)[:2000]
            event.save(update_fields=["attempts", "processed_at", "error"])
    return len(events_batch)


def run_once(batch_size=None, client_factory=GraphClient.for_account):
    batch_size = batch_size or settings.WORKER_BATCH_SIZE
    handled = process_webhook_events(batch_size)
    messages = claim_messages(batch_size)
    for message in messages:
        send_one(message, client_factory=client_factory)
    return handled + len(messages)

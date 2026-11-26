import hashlib
import secrets
from datetime import timedelta

from django.db import models
from django.utils import timezone

from .fields import EncryptedTextField

CUSTOMER_SERVICE_WINDOW = timedelta(hours=24)


class Organization(models.Model):
    """A tenant. Stage 1-2: just Everclass. Stage 3: one per school."""

    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class WhatsAppAccount(models.Model):
    """One business phone number on Cloud API (WABA ID + phone number ID + token)."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DISCONNECTED = "disconnected", "Disconnected"

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="accounts")
    waba_id = models.CharField("WABA ID", max_length=64)
    phone_number_id = models.CharField(max_length=64, unique=True)
    display_phone_number = models.CharField(max_length=32, blank=True)
    access_token = EncryptedTextField(help_text="System user or business token. Stored encrypted.")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    last_webhook_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.organization} · {self.display_phone_number or self.phone_number_id}"


class MessageTemplate(models.Model):
    class Category(models.TextChoices):
        UTILITY = "UTILITY", "Utility"
        MARKETING = "MARKETING", "Marketing"
        AUTHENTICATION = "AUTHENTICATION", "Authentication"

    account = models.ForeignKey(WhatsAppAccount, on_delete=models.CASCADE, related_name="templates")
    meta_template_id = models.CharField(max_length=64, blank=True)
    name = models.CharField(max_length=512)
    language = models.CharField(max_length=16, default="en")
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.UTILITY)
    status = models.CharField(max_length=32, default="PENDING", help_text="As reported by Meta.")
    components = models.JSONField(default=list, blank=True)
    rejected_reason = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["account", "name", "language"], name="uniq_template_per_lang")
        ]

    def __str__(self):
        return f"{self.name} ({self.language}) [{self.status}]"

    @property
    def is_sendable(self):
        return self.status == "APPROVED"


class Contact(models.Model):
    account = models.ForeignKey(WhatsAppAccount, on_delete=models.CASCADE, related_name="contacts")
    wa_id = models.CharField("WhatsApp ID", max_length=20)
    profile_name = models.CharField(max_length=200, blank=True)
    opted_out = models.BooleanField(default=False)
    opted_out_at = models.DateTimeField(null=True, blank=True)
    last_inbound_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["account", "wa_id"], name="uniq_contact")]

    def __str__(self):
        return self.wa_id

    @property
    def service_window_open(self):
        return bool(
            self.last_inbound_at and timezone.now() - self.last_inbound_at < CUSTOMER_SERVICE_WINDOW
        )


class Message(models.Model):
    """Outbox row. The worker sends QUEUED rows; webhooks move them along."""

    class Kind(models.TextChoices):
        TEMPLATE = "template", "Template"
        TEXT = "text", "Text"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENDING = "sending", "Sending"
        ACCEPTED = "accepted", "Accepted by Meta"
        SENT = "sent", "Sent"
        DELIVERED = "delivered", "Delivered"
        READ = "read", "Read"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    STATUS_RANK = {"accepted": 1, "sent": 2, "delivered": 3, "read": 4}

    account = models.ForeignKey(WhatsAppAccount, on_delete=models.CASCADE, related_name="messages")
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="messages")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    template = models.ForeignKey(MessageTemplate, null=True, blank=True, on_delete=models.PROTECT)
    body_params = models.JSONField(default=list, blank=True)
    text = models.TextField(blank=True)
    idempotency_key = models.CharField(max_length=200)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED, db_index=True)
    wamid = models.CharField(max_length=128, blank=True, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    error_code = models.CharField(max_length=32, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["account", "idempotency_key"], name="uniq_idempotency_key")
        ]
        indexes = [models.Index(fields=["status", "next_attempt_at"])]

    def __str__(self):
        return f"{self.kind} to {self.contact} [{self.status}]"


class InboundMessage(models.Model):
    account = models.ForeignKey(WhatsAppAccount, on_delete=models.CASCADE, related_name="inbound")
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="inbound")
    wamid = models.CharField(max_length=128, unique=True)
    type = models.CharField(max_length=32)
    text = models.TextField(blank=True)
    payload = models.JSONField()
    received_at = models.DateTimeField()

    def __str__(self):
        return f"{self.type} from {self.contact}"


class WebhookEvent(models.Model):
    """Raw webhook body, stored before processing so nothing is lost and it can be replayed."""

    payload = models.JSONField()
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    error = models.TextField(blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)

    def __str__(self):
        return f"Webhook #{self.pk} ({'processed' if self.processed_at else 'pending'})"


class ApiKey(models.Model):
    """Key for other Everclass apps (ERP, leads app) to call Connect's internal API."""

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="api_keys")
    name = models.CharField(max_length=100)
    prefix = models.CharField(max_length=12, unique=True)
    key_hash = models.CharField(max_length=64)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.name} ({self.prefix}…)"

    @staticmethod
    def hash(raw):
        return hashlib.sha256(raw.encode()).hexdigest()

    @classmethod
    def issue(cls, organization, name):
        """Create a key and return (api_key, raw_key). The raw key is shown once only."""
        prefix = secrets.token_hex(4)
        raw = f"ecx_{prefix}_{secrets.token_urlsafe(32)}"
        key = cls.objects.create(organization=organization, name=name, prefix=prefix, key_hash=cls.hash(raw))
        return key, raw

    @classmethod
    def authenticate(cls, raw):
        try:
            _, prefix, _ = raw.split("_", 2)
        except (AttributeError, ValueError):
            return None
        key = cls.objects.filter(prefix=prefix, active=True).select_related("organization").first()
        if key and secrets.compare_digest(key.key_hash, cls.hash(raw)):
            cls.objects.filter(pk=key.pk).update(last_used_at=timezone.now())
            return key
        return None

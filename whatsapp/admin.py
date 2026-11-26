from django import forms
from django.contrib import admin

from .models import (
    ApiKey,
    Contact,
    InboundMessage,
    Message,
    MessageTemplate,
    Organization,
    WebhookEvent,
    WhatsAppAccount,
)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "created_at")
    prepopulated_fields = {"slug": ("name",)}


class AccountForm(forms.ModelForm):
    access_token = forms.CharField(
        widget=forms.PasswordInput(render_value=False), required=False,
        help_text="Leave blank to keep the current token.",
    )

    class Meta:
        model = WhatsAppAccount
        fields = ["organization", "waba_id", "phone_number_id", "display_phone_number", "access_token", "status"]

    def clean_access_token(self):
        token = self.cleaned_data.get("access_token")
        if not token and self.instance.pk:
            return self.instance.access_token
        if not token:
            raise forms.ValidationError("Token is required for a new account.")
        return token


@admin.register(WhatsAppAccount)
class WhatsAppAccountAdmin(admin.ModelAdmin):
    form = AccountForm
    list_display = ("organization", "display_phone_number", "phone_number_id", "waba_id", "status", "last_webhook_at")
    list_filter = ("status", "organization")


@admin.register(MessageTemplate)
class MessageTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "language", "category", "status", "account", "updated_at")
    list_filter = ("status", "category", "account")
    search_fields = ("name",)


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("wa_id", "profile_name", "account", "opted_out", "last_inbound_at")
    list_filter = ("opted_out", "account")
    search_fields = ("wa_id", "profile_name")


@admin.action(description="Retry selected failed messages now")
def retry_messages(modeladmin, request, queryset):
    from django.utils import timezone

    queryset.filter(status=Message.Status.FAILED).update(
        status=Message.Status.QUEUED, next_attempt_at=timezone.now(), attempts=0, error_code="", error_message=""
    )


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "contact", "kind", "template", "status", "attempts", "error_code", "created_at")
    list_filter = ("status", "kind", "account")
    search_fields = ("contact__wa_id", "wamid", "idempotency_key")
    readonly_fields = ("wamid", "sent_at", "delivered_at", "read_at", "failed_at", "created_at")
    actions = [retry_messages]


@admin.register(InboundMessage)
class InboundMessageAdmin(admin.ModelAdmin):
    list_display = ("contact", "type", "text", "received_at", "account")
    list_filter = ("type", "account")
    search_fields = ("contact__wa_id", "text")


@admin.action(description="Reprocess selected webhook events")
def reprocess_events(modeladmin, request, queryset):
    queryset.update(processed_at=None, attempts=0, error="")


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("id", "received_at", "processed_at", "attempts", "error")
    list_filter = (("processed_at", admin.EmptyFieldListFilter),)
    actions = [reprocess_events]


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = ("name", "prefix", "organization", "active", "last_used_at")
    readonly_fields = ("prefix", "key_hash", "last_used_at")

    def has_add_permission(self, request):
        return False  # use: python manage.py create_api_key

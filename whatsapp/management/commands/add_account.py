import os
from getpass import getpass

from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify

from whatsapp.graph import GraphAPIError, GraphClient
from whatsapp.models import Organization, WhatsAppAccount


class Command(BaseCommand):
    help = "Add or update a WhatsApp number. The token is read from an env var or a hidden prompt, never argv."

    def add_arguments(self, parser):
        parser.add_argument("--org", required=True, help='Organization name, e.g. "Everclass"')
        parser.add_argument("--waba-id", required=True)
        parser.add_argument("--phone-number-id", required=True)
        parser.add_argument("--token-env", help="Name of an env var holding the token (else you are prompted).")
        parser.add_argument("--subscribe", action="store_true", help="Also subscribe the app to this WABA's webhooks.")

    def handle(self, *args, org, waba_id, phone_number_id, token_env=None, subscribe=False, **options):
        token = os.environ.get(token_env, "") if token_env else getpass("Access token: ")
        if not token:
            raise CommandError("No access token given.")

        with GraphClient(token) as client:
            try:
                info = client.get_phone_number(phone_number_id)
                if subscribe:
                    client.subscribe_app(waba_id)
            except GraphAPIError as exc:
                raise CommandError(f"Meta rejected the details: {exc}") from exc

        organization, _ = Organization.objects.get_or_create(slug=slugify(org), defaults={"name": org})
        account, created = WhatsAppAccount.objects.update_or_create(
            phone_number_id=phone_number_id,
            defaults={
                "organization": organization,
                "waba_id": waba_id,
                "display_phone_number": info.get("display_phone_number", ""),
                "access_token": token,
                "status": WhatsAppAccount.Status.ACTIVE,
            },
        )
        self.stdout.write(self.style.SUCCESS(
            f"{'Added' if created else 'Updated'} {account}: {info.get('verified_name', '')}, "
            f"quality {info.get('quality_rating', '?')}, limit {info.get('messaging_limit_tier', '?')}"
        ))

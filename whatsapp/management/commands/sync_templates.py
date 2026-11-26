from django.core.management.base import BaseCommand, CommandError

from whatsapp.graph import GraphAPIError, GraphClient
from whatsapp.models import MessageTemplate, WhatsAppAccount


class Command(BaseCommand):
    help = "Pull message templates (and their approval status) from Meta for every active account."

    def handle(self, *args, **options):
        accounts = WhatsAppAccount.objects.filter(status=WhatsAppAccount.Status.ACTIVE)
        if not accounts:
            raise CommandError("No active accounts. Run add_account first.")
        for account in accounts:
            try:
                with GraphClient.for_account(account) as client:
                    remote = client.list_templates(account.waba_id)
            except GraphAPIError as exc:
                self.stderr.write(f"{account}: {exc}")
                continue
            for t in remote:
                MessageTemplate.objects.update_or_create(
                    account=account, name=t["name"], language=t.get("language", "en"),
                    defaults={
                        "meta_template_id": str(t.get("id", "")),
                        "category": t.get("category", "UTILITY"),
                        "status": t.get("status", "UNKNOWN"),
                        "components": t.get("components", []),
                        "rejected_reason": (t.get("rejected_reason") or "")[:255]
                        if t.get("rejected_reason") != "NONE" else "",
                    },
                )
            self.stdout.write(self.style.SUCCESS(f"{account}: {len(remote)} template(s) synced"))

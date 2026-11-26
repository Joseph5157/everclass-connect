from django.core.management.base import BaseCommand, CommandError

from whatsapp import services
from whatsapp.models import WhatsAppAccount


class Command(BaseCommand):
    help = "Queue a template message (default: Meta's hello_world) for the worker to send."

    def add_arguments(self, parser):
        parser.add_argument("to", help="Recipient phone, e.g. 9876543210 or +919876543210")
        parser.add_argument("--phone-number-id", help="Sender (needed if you have several accounts)")
        parser.add_argument("--template", default="hello_world")
        parser.add_argument("--language", default="en_US")
        parser.add_argument("--param", action="append", default=[], help="Body parameter, repeatable")
        parser.add_argument("--text", help="Send free text instead (only inside the 24-hour window)")

    def handle(self, *args, to, phone_number_id=None, template, language, param, text=None, **options):
        accounts = WhatsAppAccount.objects.filter(status=WhatsAppAccount.Status.ACTIVE)
        if phone_number_id:
            accounts = accounts.filter(phone_number_id=phone_number_id)
        if accounts.count() != 1:
            raise CommandError("Pick exactly one active account with --phone-number-id.")
        account = accounts.get()
        try:
            if text:
                message, _ = services.queue_text(account, to, text)
            else:
                message, _ = services.queue_template(account, to, template, language, param)
        except services.QueueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Queued message #{message.pk}. The worker will send it."))

from django.core.management.base import BaseCommand, CommandError

from whatsapp.models import ApiKey, Organization


class Command(BaseCommand):
    help = "Issue an API key for an internal app (ERP, leads app). The key is printed once."

    def add_arguments(self, parser):
        parser.add_argument("--org", required=True, help="Organization slug, e.g. everclass")
        parser.add_argument("--name", required=True, help='Who uses it, e.g. "school-erp-staging"')

    def handle(self, *args, org, name, **options):
        organization = Organization.objects.filter(slug=org).first()
        if organization is None:
            raise CommandError(f"No organization with slug {org!r}.")
        _, raw = ApiKey.issue(organization, name)
        self.stdout.write(self.style.SUCCESS("API key (store it now, it is not shown again):"))
        self.stdout.write(raw)

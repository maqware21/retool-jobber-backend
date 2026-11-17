from django.core.management.base import BaseCommand

from apps.billing.services.grandfather import grandfather_missing_tenants


class Command(BaseCommand):
    help = (
        'Create an exempt Subscription for every tenant that has none. '
        'Idempotent; run it again immediately before enabling enforcement.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report the tenants that would be exempted without writing.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        tenant_ids = grandfather_missing_tenants(dry_run=dry_run)
        verb = 'Would exempt' if dry_run else 'Exempted'
        self.stdout.write(f"{verb} {len(tenant_ids)} tenant(s).")
        if tenant_ids:
            self.stdout.write(f"Tenant ids: {', '.join(str(i) for i in tenant_ids)}")

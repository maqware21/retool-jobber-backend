from django.core.management.base import BaseCommand

from apps.billing.models import Subscription
from apps.billing.services.notifications import send_welcome_email
from apps.users.models import User


class Command(BaseCommand):
    help = (
        'Send the welcome email again for subscriptions where it was never recorded as sent. '
        'Skips exempt rows, rows with no Stripe subscription, and users who already have a '
        'usable password. Safe to run repeatedly.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='List what would be sent without sending or writing anything.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        rows = (
            Subscription.objects
            .filter(
                welcome_email_sent_at__isnull=True,
                billing_exempt=False,
                stripe_subscription_id__isnull=False,
            )
            .order_by('pk')
        )

        sent = failed = skipped = 0
        for row in rows:
            user = (
                User.objects.filter(tenant_id=row.tenant_id, is_active=True)
                .order_by('pk')
                .first()
            )
            if user is None or user.has_usable_password():
                skipped += 1
                continue

            if dry_run:
                self.stdout.write(f"Would send: subscription id={row.pk}, user id={user.pk}")
                sent += 1
            elif send_welcome_email(user.pk):
                sent += 1
            else:
                failed += 1

        verb = 'Would send' if dry_run else 'Sent'
        self.stdout.write(f"{verb} {sent}, failed {failed}, skipped {skipped}.")

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.billing.models import Subscription
from apps.tenants.models import Tenant


def run(*args):
    out = StringIO()
    call_command('grandfather_missing_tenants', *args, stdout=out)
    return out.getvalue()


class GrandfatherMissingTenantsTests(TestCase):
    def test_exempts_tenants_without_a_subscription(self):
        tenants = [Tenant.objects.create() for _ in range(3)]
        run()
        for tenant in tenants:
            self.assertTrue(Subscription.objects.get(tenant=tenant).billing_exempt)

    def test_is_idempotent(self):
        Tenant.objects.create()
        Tenant.objects.create()
        run()
        first = list(Subscription.objects.order_by('pk').values_list('pk', 'created_at'))
        output = run()
        second = list(Subscription.objects.order_by('pk').values_list('pk', 'created_at'))
        self.assertEqual(first, second)
        self.assertEqual(len(second), 2)
        self.assertIn('Exempted 0 tenant(s).', output)

    def test_leaves_existing_rows_untouched(self):
        paying = Tenant.objects.create()
        Subscription.objects.create(tenant=paying, status='active', stripe_customer_id='cus_1')
        run()
        row = Subscription.objects.get(tenant=paying)
        self.assertFalse(row.billing_exempt)
        self.assertEqual(row.status, 'active')

    def test_includes_soft_deleted_tenants(self):
        tenant = Tenant.objects.create()
        tenant.delete()
        run()
        self.assertTrue(Subscription.objects.filter(tenant_id=tenant.pk, billing_exempt=True).exists())

    def test_dry_run_writes_nothing_and_reports(self):
        tenant = Tenant.objects.create()
        output = run('--dry-run')
        self.assertEqual(Subscription.objects.count(), 0)
        self.assertIn('Would exempt 1 tenant(s).', output)
        self.assertIn(str(tenant.pk), output)

    def test_no_tenants_is_fine(self):
        self.assertIn('Exempted 0 tenant(s).', run())

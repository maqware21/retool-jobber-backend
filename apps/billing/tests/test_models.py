from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone
from safedelete.config import HARD_DELETE

from apps.billing.models import StripeEvent, Subscription
from apps.tenants.models import Tenant


class SubscriptionConstraintTests(TestCase):
    def test_defaults(self):
        sub = Subscription.objects.create(tenant=Tenant.objects.create())
        self.assertFalse(sub.billing_exempt)
        self.assertEqual(sub.status, '')
        self.assertEqual(sub.billing_email, '')
        self.assertIsNone(sub.stripe_customer_id)
        self.assertFalse(sub.cancel_at_period_end)
        self.assertFalse(sub.needs_review)
        self.assertTrue(sub.is_active)

    def test_one_subscription_per_tenant(self):
        tenant = Tenant.objects.create()
        Subscription.objects.create(tenant=tenant)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Subscription.objects.create(tenant=tenant)

    def test_several_rows_may_have_null_stripe_ids(self):
        for _ in range(3):
            Subscription.objects.create(tenant=Tenant.objects.create())
        self.assertEqual(Subscription.objects.filter(stripe_customer_id__isnull=True).count(), 3)

    def test_stripe_ids_are_unique(self):
        for field in ('stripe_customer_id', 'stripe_subscription_id', 'stripe_checkout_session_id'):
            with self.subTest(field=field):
                Subscription.objects.create(tenant=Tenant.objects.create(), **{field: f'{field}_x'})
                with self.assertRaises(IntegrityError), transaction.atomic():
                    Subscription.objects.create(tenant=Tenant.objects.create(), **{field: f'{field}_x'})

    def test_tenant_reverse_accessor(self):
        tenant = Tenant.objects.create()
        sub = Subscription.objects.create(tenant=tenant)
        self.assertEqual(tenant.subscription, sub)

    def test_hard_deleting_a_tenant_is_protected(self):
        tenant = Tenant.objects.create()
        Subscription.objects.create(tenant=tenant)
        with self.assertRaises(ProtectedError):
            tenant.delete(force_policy=HARD_DELETE)
        self.assertEqual(Subscription.objects.count(), 1)

    def test_soft_deleting_a_tenant_is_also_protected(self):
        # Tenant's soft-delete cascade honours PROTECT, so a tenant with a
        # Subscription cannot be deleted until that row is removed first.
        tenant = Tenant.objects.create()
        Subscription.objects.create(tenant=tenant, billing_email='a@example.com')
        with self.assertRaises(ProtectedError):
            tenant.delete()
        self.assertTrue(Tenant.objects.filter(pk=tenant.pk).exists())
        self.assertEqual(Subscription.objects.filter(billing_email='a@example.com').count(), 1)

    def test_a_tenant_can_be_soft_deleted_once_its_subscription_is_removed(self):
        tenant = Tenant.objects.create()
        Subscription.objects.create(tenant=tenant).delete()
        tenant.delete()
        self.assertFalse(Tenant.objects.filter(pk=tenant.pk).exists())
        self.assertTrue(Tenant.all_objects.filter(pk=tenant.pk).exists())


class StripeEventConstraintTests(TestCase):
    def test_event_id_is_unique(self):
        StripeEvent.objects.create(event_id='evt_1', event_type='x', stripe_created=timezone.now())
        with self.assertRaises(IntegrityError), transaction.atomic():
            StripeEvent.objects.create(event_id='evt_1', event_type='y', stripe_created=timezone.now())

    def test_received_at_is_set_and_processed_at_starts_empty(self):
        event = StripeEvent.objects.create(event_id='evt_2', event_type='x', stripe_created=timezone.now())
        self.assertIsNotNone(event.received_at)
        self.assertIsNone(event.processed_at)
        self.assertEqual(event.note, '')

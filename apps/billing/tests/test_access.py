from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.billing.models import Subscription
from apps.billing.services.access import has_access, lock_reason

STATUSES = [
    'trialing', 'active', 'past_due', 'canceled', 'unpaid',
    'incomplete', 'incomplete_expired', 'paused', '',
]
GRANTING = {'trialing', 'active'}
EXPECTED_REASON = {
    'past_due': 'past_due',
    'unpaid': 'unpaid',
    'canceled': 'canceled',
    'incomplete_expired': 'canceled',
    'incomplete': 'no_subscription',
    'paused': 'no_subscription',
    '': 'no_subscription',
}


def sub(status='', exempt=False, **extra):
    return SimpleNamespace(status=status, billing_exempt=exempt, **extra)


class HasAccessTruthTableTests(SimpleTestCase):
    def test_every_status_when_not_exempt(self):
        for status in STATUSES:
            with self.subTest(status=status):
                self.assertEqual(has_access(sub(status)), status in GRANTING)

    def test_exempt_has_access_for_every_status(self):
        for status in STATUSES:
            with self.subTest(status=status):
                self.assertTrue(has_access(sub(status, exempt=True)))

    def test_missing_subscription_has_no_access(self):
        self.assertFalse(has_access(None))

    def test_unknown_status_has_no_access(self):
        self.assertFalse(has_access(sub('something_new')))

    def test_cancel_at_period_end_does_not_lock_an_active_subscription(self):
        self.assertTrue(has_access(sub('active', cancel_at_period_end=True)))

    def test_status_match_is_exact(self):
        self.assertFalse(has_access(sub('Active')))

    def test_model_property_delegates(self):
        self.assertTrue(Subscription(status='trialing').has_access)
        self.assertFalse(Subscription(status='past_due').has_access)
        self.assertTrue(Subscription(billing_exempt=True).has_access)


class LockReasonTests(SimpleTestCase):
    def test_reason_for_every_status_when_not_exempt(self):
        for status in STATUSES:
            with self.subTest(status=status):
                expected = None if status in GRANTING else EXPECTED_REASON[status]
                self.assertEqual(lock_reason(sub(status)), expected)

    def test_exempt_is_never_locked(self):
        for status in STATUSES:
            with self.subTest(status=status):
                self.assertIsNone(lock_reason(sub(status, exempt=True)))

    def test_missing_subscription(self):
        self.assertEqual(lock_reason(None), 'no_subscription')

    def test_unknown_status_falls_back(self):
        self.assertEqual(lock_reason(sub('something_new')), 'no_subscription')

    def test_no_tenant_is_always_no_subscription(self):
        self.assertEqual(lock_reason(None, has_tenant=False), 'no_subscription')
        self.assertEqual(lock_reason(sub('active', exempt=True), has_tenant=False), 'no_subscription')

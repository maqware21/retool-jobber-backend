from datetime import datetime, timezone
from unittest import mock

import stripe
from django.test import TestCase, override_settings

from apps.billing.models import Subscription
from apps.billing.services.stripe_client import StripeNotConfigured
from apps.billing.services.sync import refresh_from_stripe
from apps.tenants.models import Tenant

PRICE = 'price_test_ours'
TRIAL_END = 1_800_000_000
PERIOD_END = 1_801_000_000
CANCELED_AT = 1_802_000_000


def stripe_subscription(**over):
    data = {
        'id': 'sub_1',
        'status': 'active',
        'customer': 'cus_9',
        'trial_end': TRIAL_END,
        'cancel_at_period_end': True,
        'canceled_at': CANCELED_AT,
        'items': {'data': [{'price': {'id': PRICE}, 'current_period_end': PERIOD_END}]},
    }
    data.update(over)
    return data


def utc(seconds):
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


@override_settings(STRIPE_PRICE_ID=PRICE)
class RefreshFromStripeTests(TestCase):
    def setUp(self):
        self.row = Subscription.objects.create(
            tenant=Tenant.objects.create(), stripe_subscription_id='sub_1',
        )
        patcher = mock.patch('apps.billing.services.sync.get_client')
        self.get_client = patcher.start()
        self.addCleanup(patcher.stop)
        self.retrieve = self.get_client.return_value.v1.subscriptions.retrieve

    def test_writes_the_whole_state(self):
        self.retrieve.return_value = stripe_subscription()

        result = refresh_from_stripe('sub_1')

        self.retrieve.assert_called_once_with('sub_1')
        self.row.refresh_from_db()
        self.assertEqual(result, self.row)
        self.assertEqual(self.row.status, 'active')
        self.assertEqual(self.row.trial_end, utc(TRIAL_END))
        self.assertEqual(self.row.current_period_end, utc(PERIOD_END))
        self.assertTrue(self.row.cancel_at_period_end)
        self.assertEqual(self.row.canceled_at, utc(CANCELED_AT))
        self.assertEqual(self.row.stripe_customer_id, 'cus_9')

    def test_accepts_a_real_stripe_object_which_has_no_get(self):
        self.retrieve.return_value = stripe.Subscription.construct_from(
            stripe_subscription(), 'test-key-not-real',
        )
        self.assertFalse(hasattr(self.retrieve.return_value, 'get'))

        refresh_from_stripe('sub_1')

        self.row.refresh_from_db()
        self.assertEqual(self.row.status, 'active')
        self.assertEqual(self.row.current_period_end, utc(PERIOD_END))
        self.assertEqual(self.row.stripe_customer_id, 'cus_9')

    def test_overwrites_earlier_values_with_what_stripe_reports_now(self):
        self.row.status = 'trialing'
        self.row.trial_end = utc(TRIAL_END)
        self.row.cancel_at_period_end = True
        self.row.canceled_at = utc(CANCELED_AT)
        self.row.save()
        self.retrieve.return_value = stripe_subscription(
            status='past_due', trial_end=None, cancel_at_period_end=False, canceled_at=None,
        )

        refresh_from_stripe('sub_1')

        self.row.refresh_from_db()
        self.assertEqual(self.row.status, 'past_due')
        self.assertIsNone(self.row.trial_end)
        self.assertFalse(self.row.cancel_at_period_end)
        self.assertIsNone(self.row.canceled_at)

    def test_reads_period_end_from_the_item_on_our_price(self):
        self.retrieve.return_value = stripe_subscription(items={'data': [
            {'price': {'id': 'price_other'}, 'current_period_end': 1},
            {'price': {'id': PRICE}, 'current_period_end': PERIOD_END},
        ]})

        refresh_from_stripe('sub_1')

        self.row.refresh_from_db()
        self.assertEqual(self.row.current_period_end, utc(PERIOD_END))

    def test_accepts_an_expanded_customer(self):
        self.retrieve.return_value = stripe_subscription(customer={'id': 'cus_expanded'})

        refresh_from_stripe('sub_1')

        self.row.refresh_from_db()
        self.assertEqual(self.row.stripe_customer_id, 'cus_expanded')

    def test_a_wrong_price_changes_nothing_and_warns(self):
        self.row.status = 'trialing'
        self.row.save()
        self.retrieve.return_value = stripe_subscription(
            status='canceled',
            items={'data': [{'price': {'id': 'price_other'}, 'current_period_end': PERIOD_END}]},
        )

        with self.assertLogs('apps.billing.services.sync', level='WARNING') as logs:
            result = refresh_from_stripe('sub_1')

        self.assertIsNone(result)
        self.assertIn('sub_1', logs.output[0])
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, 'trialing')
        self.assertIsNone(self.row.current_period_end)

    def test_an_unknown_subscription_changes_nothing_and_does_not_call_stripe(self):
        with self.assertLogs('apps.billing.services.sync', level='WARNING') as logs:
            result = refresh_from_stripe('sub_unknown')

        self.assertIsNone(result)
        self.assertIn('sub_unknown', logs.output[0])
        self.retrieve.assert_not_called()
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, '')

    def test_a_stripe_error_propagates_and_leaves_the_row_alone(self):
        self.retrieve.side_effect = stripe.APIConnectionError('network down')

        with self.assertRaises(stripe.StripeError):
            refresh_from_stripe('sub_1')

        self.row.refresh_from_db()
        self.assertEqual(self.row.status, '')

    @override_settings(STRIPE_PRICE_ID='')
    def test_an_empty_price_setting_raises_and_changes_nothing(self):
        self.retrieve.return_value = stripe_subscription()

        with self.assertRaises(StripeNotConfigured):
            refresh_from_stripe('sub_1')

        self.retrieve.assert_not_called()
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, '')

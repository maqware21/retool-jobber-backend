import hashlib
import hmac
import json
import logging
import time
from copy import deepcopy
from unittest import mock

import stripe
from django.db import IntegrityError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.billing.models import StripeEvent, Subscription
from apps.tenants.models import Tenant
from apps.users.models import User

SECRET = 'whsec_test_not_real'
PRICE = 'price_test_ours'
PERIOD_END = 1_801_000_000
EMAIL = 'secret.person@example.com'


def sign(payload, secret=SECRET):
    timestamp = int(time.time())
    digest = hmac.new(secret.encode(), f'{timestamp}.'.encode() + payload, hashlib.sha256).hexdigest()
    return f't={timestamp},v1={digest}'


def make_event(event_id, event_type, obj):
    return {
        'id': event_id,
        'object': 'event',
        'type': event_type,
        'created': 1_700_000_000,
        'data': {'object': obj},
    }


def checkout_session(**over):
    data = {
        'id': 'cs_1',
        'object': 'checkout.session',
        'mode': 'subscription',
        'status': 'complete',
        'payment_status': 'no_payment_required',
        'customer': 'cus_1',
        'subscription': 'sub_1',
        'customer_details': {'email': EMAIL, 'name': 'Ada Lovelace'},
        'metadata': {},
    }
    data.update(over)
    return data


def completed_event(event_id='evt_1', **over):
    return make_event(event_id, 'checkout.session.completed', checkout_session(**over))


STRIPE_SUBSCRIPTION = {
    'id': 'sub_1',
    'status': 'active',
    'customer': 'cus_1',
    'trial_end': None,
    'cancel_at_period_end': False,
    'canceled_at': None,
    'items': {'data': [{'price': {'id': PRICE}, 'current_period_end': PERIOD_END}]},
}


def stripe_subscription(**over):
    data = deepcopy(STRIPE_SUBSCRIPTION)
    data.update(over)
    return data


@override_settings(
    STRIPE_WEBHOOK_SECRET=SECRET,
    STRIPE_PRICE_ID=PRICE,
    STRIPE_SECRET_KEY='test-key-not-real',
)
class WebhookTestCase(TestCase):
    url = '/v1/billing/webhook/'

    def setUp(self):
        patcher = mock.patch('apps.billing.services.sync.get_client')
        get_client = patcher.start()
        self.addCleanup(patcher.stop)
        self.retrieve = get_client.return_value.v1.subscriptions.retrieve
        self.retrieve.return_value = stripe_subscription()

    def post_event(self, event, client=None):
        body = json.dumps(event).encode()
        return (client or self.client).post(
            self.url, data=body, content_type='application/json',
            HTTP_STRIPE_SIGNATURE=sign(body),
        )

    def counts(self):
        return (Tenant.objects.count(), User.objects.count(), Subscription.objects.count())


class RequestHandlingTests(WebhookTestCase):
    def test_only_post_is_allowed(self):
        for method in ('get', 'put', 'delete', 'options', 'patch'):
            with self.subTest(method=method):
                self.assertEqual(getattr(self.client, method)(self.url).status_code, 405)

    def test_a_missing_signature_header_is_400(self):
        response = self.client.post(self.url, data=b'{}', content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_an_empty_signature_header_is_400(self):
        response = self.client.post(
            self.url, data=b'{}', content_type='application/json', HTTP_STRIPE_SIGNATURE='  ',
        )
        self.assertEqual(response.status_code, 400)

    def test_a_signature_made_with_another_secret_is_400(self):
        body = json.dumps(completed_event()).encode()
        response = self.client.post(
            self.url, data=body, content_type='application/json',
            HTTP_STRIPE_SIGNATURE=sign(body, secret='whsec_some_other_secret'),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_a_tampered_body_is_400(self):
        body = json.dumps(completed_event()).encode()
        header = sign(body)
        response = self.client.post(
            self.url, data=body.replace(b'cs_1', b'cs_2'), content_type='application/json',
            HTTP_STRIPE_SIGNATURE=header,
        )
        self.assertEqual(response.status_code, 400)

    def test_a_garbage_signature_is_400(self):
        response = self.client.post(
            self.url, data=b'{}', content_type='application/json', HTTP_STRIPE_SIGNATURE='nonsense',
        )
        self.assertEqual(response.status_code, 400)

    def test_a_signed_body_that_is_not_json_is_400(self):
        body = b'this is not json'
        response = self.client.post(
            self.url, data=body, content_type='application/json', HTTP_STRIPE_SIGNATURE=sign(body),
        )
        self.assertEqual(response.status_code, 400)

    def test_a_signed_event_without_an_id_is_400(self):
        event = completed_event()
        del event['id']
        self.assertEqual(self.post_event(event).status_code, 400)
        self.assertEqual(StripeEvent.objects.count(), 0)

    @override_settings(STRIPE_WEBHOOK_SECRET='')
    def test_an_unset_secret_is_500_and_logged(self):
        body = json.dumps(completed_event()).encode()
        with self.assertLogs('apps.billing.api.webhook', level='ERROR'):
            response = self.client.post(
                self.url, data=body, content_type='application/json',
                HTTP_STRIPE_SIGNATURE='t=1,v1=abc',
            )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_csrf_is_not_enforced(self):
        client = Client(enforce_csrf_checks=True)
        response = self.post_event(make_event('evt_csrf', 'ping.unknown', {}), client=client)
        self.assertEqual(response.status_code, 200)

    def test_the_route_is_under_v1_billing(self):
        self.assertEqual(reverse('billing:webhook'), '/v1/billing/webhook/')


class UnknownEventTests(WebhookTestCase):
    def test_an_unknown_type_is_recorded_and_answered_200(self):
        response = self.post_event(make_event('evt_other', 'customer.created', {'id': 'cus_1'}))

        self.assertEqual(response.status_code, 200)
        record = StripeEvent.objects.get(event_id='evt_other')
        self.assertEqual(record.event_type, 'customer.created')
        self.assertIsNotNone(record.processed_at)
        self.assertEqual(record.note, 'no handler for this event type')
        self.retrieve.assert_not_called()


class CheckoutCompletedTests(WebhookTestCase):
    def test_creates_tenant_user_and_subscription_with_a_populated_status(self):
        response = self.post_event(completed_event())

        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email=EMAIL)
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.role, 'customer')
        row = Subscription.objects.get(tenant_id=user.tenant_id)
        self.assertEqual(row.stripe_subscription_id, 'sub_1')
        self.assertEqual(row.status, 'active')
        self.assertIsNotNone(row.current_period_end)
        self.assertTrue(row.has_access)
        self.assertEqual(self.counts(), (1, 1, 1))
        record = StripeEvent.objects.get(event_id='evt_1')
        self.assertIsNotNone(record.processed_at)
        self.assertEqual(record.note, '')
        self.assertEqual(self.retrieve.call_count, 1)

    def test_a_duplicate_delivery_is_applied_once(self):
        first = self.post_event(completed_event())
        second = self.post_event(completed_event())

        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(self.counts(), (1, 1, 1))
        self.assertEqual(StripeEvent.objects.filter(event_id='evt_1').count(), 1)
        self.assertEqual(self.retrieve.call_count, 1)

    def test_a_session_that_is_not_complete_is_ignored(self):
        response = self.post_event(completed_event(status='open'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.counts(), (0, 0, 0))
        self.retrieve.assert_not_called()
        self.assertIsNotNone(StripeEvent.objects.get(event_id='evt_1').processed_at)

    def test_a_refresh_that_returns_none_flags_the_row_and_answers_200(self):
        self.retrieve.return_value = stripe_subscription(
            items={'data': [{'price': {'id': 'price_other'}, 'current_period_end': PERIOD_END}]},
        )

        with self.assertLogs('apps.billing', level='ERROR'):
            response = self.post_event(completed_event())

        self.assertEqual(response.status_code, 200)
        row = Subscription.objects.get(stripe_subscription_id='sub_1')
        self.assertTrue(row.needs_review)
        self.assertIn('could not be refreshed', row.review_note)
        self.assertEqual(row.status, '')
        self.assertIn('could not be refreshed', StripeEvent.objects.get(event_id='evt_1').note)

    def test_a_collision_answers_200_and_keeps_the_note(self):
        tenant = Tenant.objects.create()
        owner = User.create(
            email=EMAIL, first_name='O', password='Passw0rdX', permission='customer',
            check_validation=False,
        )
        owner.tenant = tenant
        owner.save(update_fields=['tenant'])
        row = Subscription.objects.create(tenant=tenant, billing_exempt=True)
        before = self.counts()

        with self.assertLogs('apps.billing', level='ERROR'):
            response = self.post_event(completed_event())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.counts(), before)
        self.assertIn('Nothing was created', StripeEvent.objects.get(event_id='evt_1').note)
        row.refresh_from_db()
        self.assertTrue(row.needs_review)
        self.retrieve.assert_not_called()

    def test_a_deactivated_user_answers_200_with_a_note(self):
        owner = User.create(
            email=EMAIL, first_name='O', password='Passw0rdX', permission='customer',
            check_validation=False,
        )
        owner.is_active = False
        owner.save(update_fields=['is_active'])

        with self.assertLogs('apps.billing', level='ERROR'):
            response = self.post_event(completed_event())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.counts(), (0, 1, 0))
        self.assertIn('deactivated user', StripeEvent.objects.get(event_id='evt_1').note)

    def test_an_unmatched_session_answers_200_with_a_note(self):
        with self.assertLogs('apps.billing', level='ERROR'):
            response = self.post_event(completed_event(customer_details=None))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertEqual(StripeEvent.objects.get(event_id='evt_1').note, 'no email on the session')

    def test_a_redelivered_session_under_a_new_event_refreshes_without_creating(self):
        self.post_event(completed_event('evt_1'))
        self.retrieve.return_value = stripe_subscription(status='past_due')

        response = self.post_event(completed_event('evt_2'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.counts(), (1, 1, 1))
        self.assertEqual(self.retrieve.call_count, 2)
        self.assertEqual(Subscription.objects.get(stripe_subscription_id='sub_1').status, 'past_due')
        self.assertEqual(StripeEvent.objects.count(), 2)

    def test_a_resubscribe_session_updates_the_existing_row(self):
        tenant = Tenant.objects.create()
        row = Subscription.objects.create(
            tenant=tenant, status='canceled', stripe_customer_id='cus_1',
            stripe_subscription_id='sub_old',
        )
        self.retrieve.return_value = stripe_subscription(id='sub_new')

        response = self.post_event(completed_event(
            id='cs_new', subscription='sub_new', customer_details=None,
            metadata={'flow': 'resubscribe', 'tenant_id': str(tenant.pk)},
        ))

        self.assertEqual(response.status_code, 200)
        row.refresh_from_db()
        self.assertEqual(row.stripe_subscription_id, 'sub_new')
        self.assertEqual(row.status, 'active')
        self.assertEqual(self.counts(), (1, 0, 1))


class FailureTests(WebhookTestCase):
    def test_a_handler_failure_is_500_and_leaves_no_event_row(self):
        with mock.patch(
            'apps.billing.api.webhook.provision_from_checkout_session',
            side_effect=RuntimeError('boom'),
        ):
            with self.assertLogs('apps.billing.api.webhook', level='ERROR'):
                response = self.post_event(completed_event())

        self.assertEqual(response.status_code, 500)
        self.assertEqual(StripeEvent.objects.count(), 0)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_a_stripe_error_rolls_back_the_provisioning_too(self):
        self.retrieve.side_effect = stripe.APIConnectionError('network down')

        with self.assertLogs('apps.billing.api.webhook', level='ERROR'):
            response = self.post_event(completed_event())

        self.assertEqual(response.status_code, 500)
        self.assertEqual(StripeEvent.objects.count(), 0)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_a_failed_delivery_can_be_redelivered_and_then_succeeds(self):
        self.retrieve.side_effect = stripe.APIConnectionError('network down')
        with self.assertLogs('apps.billing.api.webhook', level='ERROR'):
            self.assertEqual(self.post_event(completed_event()).status_code, 500)

        self.retrieve.side_effect = None
        self.retrieve.return_value = stripe_subscription()
        self.assertEqual(self.post_event(completed_event()).status_code, 200)

        self.assertEqual(self.counts(), (1, 1, 1))
        self.assertEqual(StripeEvent.objects.count(), 1)

    @override_settings(STRIPE_PRICE_ID='')
    def test_a_missing_price_setting_is_500_and_creates_nothing(self):
        with self.assertLogs('apps.billing.api.webhook', level='ERROR'):
            response = self.post_event(completed_event())

        self.assertEqual(response.status_code, 500)
        self.assertEqual(StripeEvent.objects.count(), 0)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_a_subscription_event_stripe_error_is_500(self):
        Subscription.objects.create(tenant=Tenant.objects.create(), stripe_subscription_id='sub_1')
        self.retrieve.side_effect = stripe.RateLimitError('slow down')

        with self.assertLogs('apps.billing.api.webhook', level='ERROR'):
            response = self.post_event(
                make_event('evt_u', 'customer.subscription.updated', {'id': 'sub_1'}),
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(StripeEvent.objects.count(), 0)


class SubscriptionAndInvoiceEventTests(WebhookTestCase):
    def make_row(self, status='past_due'):
        return Subscription.objects.create(
            tenant=Tenant.objects.create(), stripe_subscription_id='sub_1', status=status,
        )

    def test_a_subscription_event_refreshes_the_row(self):
        row = self.make_row()
        for event_type in (
            'customer.subscription.created',
            'customer.subscription.updated',
            'customer.subscription.deleted',
        ):
            with self.subTest(event_type=event_type):
                row.status = 'past_due'
                row.save()
                response = self.post_event(make_event(f'evt_{event_type}', event_type, {'id': 'sub_1'}))
                self.assertEqual(response.status_code, 200)
                row.refresh_from_db()
                self.assertEqual(row.status, 'active')

    def test_a_subscription_event_before_the_completed_event_is_ignored_with_200(self):
        with self.assertLogs('apps.billing', level='WARNING'):
            early = self.post_event(
                make_event('evt_early', 'customer.subscription.updated', {'id': 'sub_1'}),
            )
        self.assertEqual(early.status_code, 200)
        self.assertEqual(self.counts(), (0, 0, 0))
        self.retrieve.assert_not_called()
        self.assertEqual(
            StripeEvent.objects.get(event_id='evt_early').note, 'no matching subscription row or price',
        )

        late = self.post_event(completed_event('evt_late'))

        self.assertEqual(late.status_code, 200)
        self.assertEqual(self.counts(), (1, 1, 1))
        self.assertEqual(Subscription.objects.get(stripe_subscription_id='sub_1').status, 'active')

    def test_an_invoice_event_with_the_id_in_the_new_location(self):
        row = self.make_row()
        invoice = {'id': 'in_1', 'parent': {'subscription_details': {'subscription': 'sub_1'}}}

        response = self.post_event(make_event('evt_new', 'invoice.paid', invoice))

        self.assertEqual(response.status_code, 200)
        row.refresh_from_db()
        self.assertEqual(row.status, 'active')
        self.retrieve.assert_called_once_with('sub_1')

    def test_an_invoice_event_with_the_id_in_the_old_location(self):
        row = self.make_row()
        for event_type in ('invoice.paid', 'invoice.payment_failed', 'invoice.payment_action_required'):
            with self.subTest(event_type=event_type):
                row.status = 'past_due'
                row.save()
                response = self.post_event(
                    make_event(f'evt_{event_type}', event_type, {'id': 'in_1', 'subscription': 'sub_1'}),
                )
                self.assertEqual(response.status_code, 200)
                row.refresh_from_db()
                self.assertEqual(row.status, 'active')

    def test_an_invoice_event_with_an_expanded_subscription(self):
        row = self.make_row()
        response = self.post_event(
            make_event('evt_exp', 'invoice.paid', {'id': 'in_1', 'subscription': {'id': 'sub_1'}}),
        )
        self.assertEqual(response.status_code, 200)
        row.refresh_from_db()
        self.assertEqual(row.status, 'active')

    def test_an_invoice_without_a_subscription_is_a_warning_and_200(self):
        with self.assertLogs('apps.billing.api.webhook', level='WARNING'):
            response = self.post_event(
                make_event('evt_one_off', 'invoice.paid', {'id': 'in_1', 'subscription': None}),
            )
        self.assertEqual(response.status_code, 200)
        self.retrieve.assert_not_called()
        self.assertEqual(StripeEvent.objects.get(event_id='evt_one_off').note, 'no subscription id on the event')

    def test_a_wrong_price_is_a_warning_and_200(self):
        row = self.make_row(status='trialing')
        self.retrieve.return_value = stripe_subscription(
            status='canceled',
            items={'data': [{'price': {'id': 'price_other'}, 'current_period_end': PERIOD_END}]},
        )
        with self.assertLogs('apps.billing', level='WARNING'):
            response = self.post_event(
                make_event('evt_wp', 'customer.subscription.updated', {'id': 'sub_1'}),
            )
        self.assertEqual(response.status_code, 200)
        row.refresh_from_db()
        self.assertEqual(row.status, 'trialing')


class LoggingTests(WebhookTestCase):
    def collect(self, run):
        records = []

        class Collector(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = Collector(level=logging.DEBUG)
        root = logging.getLogger('apps.billing')
        previous = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            run()
        finally:
            root.removeHandler(handler)
            root.setLevel(previous)
        formatter = logging.Formatter()
        return records, [formatter.format(record) for record in records]

    def test_webhook_lines_carry_the_event_id_and_type_and_no_email(self):
        def run():
            self.post_event(completed_event('evt_log_ok'))
            self.post_event(completed_event('evt_log_dup'))
            self.post_event(completed_event('evt_log_dup'))
            self.post_event(make_event('evt_log_unknown', 'customer.created', {}))
            self.post_event(make_event('evt_log_early', 'customer.subscription.updated', {'id': 'sub_zzz'}))

        records, texts = self.collect(run)

        webhook_records = [r for r in records if r.name == 'apps.billing.api.webhook']
        self.assertTrue(webhook_records)
        for record in webhook_records:
            message = record.getMessage()
            self.assertRegex(message, r'evt_log_\w+', message)
        for text in texts:
            self.assertNotIn(EMAIL, text)
            self.assertNotIn('Ada', text)

    def test_a_collision_log_has_the_event_id_and_no_email(self):
        tenant = Tenant.objects.create()
        owner = User.create(
            email=EMAIL, first_name='O', password='Passw0rdX', permission='customer',
            check_validation=False,
        )
        owner.tenant = tenant
        owner.save(update_fields=['tenant'])
        Subscription.objects.create(tenant=tenant, billing_exempt=True)

        records, texts = self.collect(lambda: self.post_event(completed_event('evt_log_collision')))

        self.assertTrue(any('evt_log_collision' in r.getMessage() for r in records))
        for text in texts:
            self.assertNotIn(EMAIL, text)

    def test_a_database_error_that_quotes_an_email_is_logged_without_it(self):
        error = IntegrityError(f'duplicate key value violates unique constraint DETAIL: Key (email)=({EMAIL}) already exists.')

        def run():
            with mock.patch(
                'apps.billing.api.webhook.provision_from_checkout_session', side_effect=error,
            ):
                response = self.post_event(completed_event('evt_log_db'))
            self.assertEqual(response.status_code, 500)

        records, texts = self.collect(run)

        failure = [r for r in records if 'processing failed' in r.getMessage()]
        self.assertEqual(len(failure), 1)
        self.assertIn('evt_log_db', failure[0].getMessage())
        self.assertIsNotNone(failure[0].exc_info)
        for text in texts:
            self.assertNotIn(EMAIL, text)

    def test_a_non_database_failure_keeps_its_traceback(self):
        def run():
            with mock.patch(
                'apps.billing.api.webhook.provision_from_checkout_session',
                side_effect=RuntimeError('kaboom'),
            ):
                self.post_event(completed_event('evt_log_rt'))

        _, texts = self.collect(run)

        self.assertTrue(any('RuntimeError: kaboom' in text and 'Traceback' in text for text in texts))

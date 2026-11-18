from unittest import mock

from django.contrib.auth.models import Permission
from django.test import TestCase

from apps.billing.models import Subscription
from apps.billing.services.provisioning import (
    ENDED_STATUSES,
    Outcome,
    ProvisioningError,
    provision_from_checkout_session,
)
from apps.tenants.models import Tenant
from apps.users.models import User


def session(**over):
    data = {
        'id': 'cs_1',
        'mode': 'subscription',
        'status': 'complete',
        'payment_status': 'no_payment_required',
        'customer': 'cus_1',
        'subscription': 'sub_1',
        'customer_details': {'email': '  New.Buyer@Example.COM ', 'name': 'Ada Lovelace'},
        'metadata': {},
    }
    data.update(over)
    return data


def make_user(email='owner@example.com', permission='customer', tenant=None):
    user = User.create(
        email=email, first_name='Owner', password='Passw0rdX', permission=permission,
        check_validation=False,
    )
    if tenant is not None:
        user.tenant = tenant
        user.save(update_fields=['tenant'])
    return user


class CountsMixin:
    def counts(self):
        return (Tenant.objects.count(), User.objects.count(), Subscription.objects.count())


class NewAccountTests(CountsMixin, TestCase):
    def test_creates_tenant_user_and_subscription(self):
        with self.captureOnCommitCallbacks(execute=True):
            result = provision_from_checkout_session(session())

        self.assertEqual(result.outcome, Outcome.PROVISIONED)
        user = User.objects.get(email='new.buyer@example.com')
        self.assertEqual(result.user, user)
        self.assertEqual((user.first_name, user.last_name), ('Ada', 'Lovelace'))
        self.assertEqual(user.role, 'customer')
        self.assertIsNotNone(user.tenant_id)
        row = Subscription.objects.get(tenant_id=user.tenant_id)
        self.assertEqual(result.subscription, row)
        self.assertEqual(row.billing_email, 'new.buyer@example.com')
        self.assertEqual(row.stripe_customer_id, 'cus_1')
        self.assertEqual(row.stripe_subscription_id, 'sub_1')
        self.assertEqual(row.stripe_checkout_session_id, 'cs_1')
        self.assertFalse(row.billing_exempt)
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_the_password_is_unusable(self):
        provision_from_checkout_session(session())
        user = User.objects.get(email='new.buyer@example.com')
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.check_password(''))
        self.assertFalse(user.check_password('anything'))

    def test_the_email_is_normalised(self):
        provision_from_checkout_session(session(
            customer_details={'email': '  MiXeD@Example.com  '},
        ))
        self.assertTrue(User.objects.filter(email='mixed@example.com').exists())

    def test_falls_back_to_customer_email_and_to_the_email_for_a_name(self):
        provision_from_checkout_session(session(customer_details=None, customer_email='Solo@Example.com'))
        user = User.objects.get(email='solo@example.com')
        self.assertEqual(user.first_name, 'solo')
        self.assertIsNone(user.last_name)

    def test_accepts_an_expanded_subscription_and_customer(self):
        result = provision_from_checkout_session(session(
            subscription={'id': 'sub_exp'}, customer={'id': 'cus_exp'},
        ))
        self.assertEqual(result.subscription.stripe_subscription_id, 'sub_exp')
        self.assertEqual(result.subscription.stripe_customer_id, 'cus_exp')

    def test_queues_the_welcome_email_only_after_commit(self):
        with mock.patch('apps.billing.services.notifications.queue_welcome_email') as queue:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                result = provision_from_checkout_session(session())
            queue.assert_not_called()
            for callback in callbacks:
                callback()
        queue.assert_called_once_with(result.user)

    def test_rolls_back_everything_when_a_later_step_fails(self):
        with mock.patch('apps.billing.services.notifications.queue_welcome_email') as queue:
            with mock.patch.object(Subscription.objects, 'create', side_effect=RuntimeError('boom')):
                with self.captureOnCommitCallbacks(execute=True):
                    with self.assertRaises(RuntimeError):
                        provision_from_checkout_session(session())
        self.assertEqual(self.counts(), (0, 0, 0))
        queue.assert_not_called()

    def test_a_missing_customer_role_rolls_back(self):
        Permission.objects.filter(codename='customer').delete()
        with self.assertRaises(ProvisioningError):
            provision_from_checkout_session(session())
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_a_failed_user_create_rolls_back(self):
        with mock.patch.object(User, 'create', return_value=None):
            with self.assertRaises(ProvisioningError):
                provision_from_checkout_session(session())
        self.assertEqual(self.counts(), (0, 0, 0))


class IgnoredSessionTests(CountsMixin, TestCase):
    def test_a_payment_mode_session_is_ignored(self):
        result = provision_from_checkout_session(session(mode='payment'))
        self.assertEqual(result.outcome, Outcome.IGNORED)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_an_incomplete_session_is_ignored(self):
        for status in ('open', 'expired', None):
            with self.subTest(status=status):
                result = provision_from_checkout_session(session(status=status))
                self.assertEqual(result.outcome, Outcome.IGNORED)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_payment_status_is_not_consulted(self):
        for payment_status in ('paid', 'no_payment_required', 'unpaid'):
            with self.subTest(payment_status=payment_status):
                Subscription.objects.all().delete()
                User.objects.all().delete()
                Tenant.objects.all().delete()
                result = provision_from_checkout_session(session(payment_status=payment_status))
                self.assertEqual(result.outcome, Outcome.PROVISIONED)

    def test_a_session_with_no_email_creates_nothing(self):
        with self.assertLogs('apps.billing.services.provisioning', level='ERROR'):
            result = provision_from_checkout_session(session(customer_details=None))
        self.assertEqual(result.outcome, Outcome.UNMATCHED)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_a_session_with_no_subscription_creates_nothing(self):
        with self.assertLogs('apps.billing.services.provisioning', level='ERROR'):
            result = provision_from_checkout_session(session(subscription=None))
        self.assertEqual(result.outcome, Outcome.UNMATCHED)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_stripe_ids_held_by_another_row_create_nothing(self):
        Subscription.objects.create(tenant=Tenant.objects.create(), stripe_subscription_id='sub_1')
        with self.assertLogs('apps.billing.services.provisioning', level='ERROR'):
            result = provision_from_checkout_session(session())
        self.assertEqual(result.outcome, Outcome.UNMATCHED)
        self.assertEqual(self.counts(), (1, 0, 1))


class RedeliveryTests(CountsMixin, TestCase):
    def test_the_same_session_twice_changes_nothing_the_second_time(self):
        first = provision_from_checkout_session(session())
        counts = self.counts()
        second = provision_from_checkout_session(session())
        self.assertEqual(second.outcome, Outcome.ALREADY_PROVISIONED)
        self.assertEqual(second.subscription, first.subscription)
        self.assertEqual(self.counts(), counts)


class ExistingUserTests(CountsMixin, TestCase):
    EMAIL = 'new.buyer@example.com'

    def test_a_user_with_no_tenant_gets_a_tenant_and_subscription(self):
        user = make_user(self.EMAIL)
        with mock.patch('apps.billing.services.notifications.queue_welcome_email') as queue:
            with self.captureOnCommitCallbacks(execute=True):
                result = provision_from_checkout_session(session())

        self.assertEqual(result.outcome, Outcome.ATTACHED)
        user.refresh_from_db()
        self.assertIsNotNone(user.tenant_id)
        row = Subscription.objects.get(tenant_id=user.tenant_id)
        self.assertEqual(row.stripe_subscription_id, 'sub_1')
        self.assertEqual(row.billing_email, self.EMAIL)
        self.assertEqual(self.counts(), (1, 1, 1))
        queue.assert_not_called()
        self.assertTrue(user.check_password('Passw0rdX'))

    def test_a_live_subscription_is_a_collision_that_creates_nothing(self):
        for status in ('trialing', 'active', 'past_due', 'unpaid'):
            with self.subTest(status=status):
                Subscription.objects.all().delete()
                User.objects.all().delete()
                Tenant.objects.all().delete()
                tenant = Tenant.objects.create()
                make_user(self.EMAIL, tenant=tenant)
                row = Subscription.objects.create(
                    tenant=tenant, status=status, stripe_subscription_id='sub_live',
                )
                before = self.counts()

                with self.assertLogs('apps.billing.services.provisioning', level='ERROR'):
                    result = provision_from_checkout_session(session())

                self.assertEqual(result.outcome, Outcome.COLLISION)
                self.assertEqual(self.counts(), before)
                row.refresh_from_db()
                self.assertTrue(row.needs_review)
                self.assertIn('cs_1', row.review_note)
                self.assertEqual(row.stripe_subscription_id, 'sub_live')

    def test_an_exempt_tenant_is_a_collision(self):
        tenant = Tenant.objects.create()
        make_user(self.EMAIL, tenant=tenant)
        row = Subscription.objects.create(tenant=tenant, billing_exempt=True)
        before = self.counts()

        with self.assertLogs('apps.billing.services.provisioning', level='ERROR'):
            result = provision_from_checkout_session(session())

        self.assertEqual(result.outcome, Outcome.COLLISION)
        self.assertEqual(self.counts(), before)
        row.refresh_from_db()
        self.assertTrue(row.needs_review)
        self.assertIsNone(row.stripe_subscription_id)

    def test_a_second_collision_keeps_the_first_note(self):
        tenant = Tenant.objects.create()
        make_user(self.EMAIL, tenant=tenant)
        row = Subscription.objects.create(tenant=tenant, billing_exempt=True, review_note='earlier note')

        with self.assertLogs('apps.billing.services.provisioning', level='ERROR'):
            provision_from_checkout_session(session())

        row.refresh_from_db()
        self.assertTrue(row.review_note.startswith('earlier note'))
        self.assertIn('cs_1', row.review_note)

    def test_a_staff_user_is_a_collision_that_creates_nothing(self):
        for role in ('admin',):
            with self.subTest(role=role):
                make_user(self.EMAIL, permission=role)
                before = self.counts()
                with self.assertLogs('apps.billing.services.provisioning', level='ERROR'):
                    result = provision_from_checkout_session(session())
                self.assertEqual(result.outcome, Outcome.COLLISION)
                self.assertIsNone(result.subscription)
                self.assertEqual(self.counts(), before)

    def test_a_superuser_is_a_collision(self):
        user = make_user(self.EMAIL)
        user.is_superuser = True
        user.save(update_fields=['is_superuser'])
        with self.assertLogs('apps.billing.services.provisioning', level='ERROR'):
            result = provision_from_checkout_session(session())
        self.assertEqual(result.outcome, Outcome.COLLISION)
        self.assertEqual(self.counts(), (0, 1, 0))

    def test_a_canceled_tenant_takes_the_new_subscription_on_its_existing_row(self):
        tenant = Tenant.objects.create()
        make_user(self.EMAIL, tenant=tenant)
        row = Subscription.objects.create(
            tenant=tenant, status='canceled', stripe_customer_id='cus_old',
            stripe_subscription_id='sub_old', stripe_checkout_session_id='cs_old',
        )
        before = self.counts()

        result = provision_from_checkout_session(session())

        self.assertEqual(result.outcome, Outcome.REACTIVATED)
        self.assertEqual(self.counts(), before)
        row.refresh_from_db()
        self.assertEqual(row.stripe_subscription_id, 'sub_1')
        self.assertEqual(row.stripe_checkout_session_id, 'cs_1')
        self.assertEqual(row.stripe_customer_id, 'cus_1')
        self.assertEqual(row.billing_email, self.EMAIL)
        self.assertFalse(row.needs_review)

    def test_a_tenant_with_no_row_gets_one(self):
        tenant = Tenant.objects.create()
        make_user(self.EMAIL, tenant=tenant)

        result = provision_from_checkout_session(session())

        self.assertEqual(result.outcome, Outcome.REACTIVATED)
        self.assertEqual(Subscription.objects.get(tenant=tenant).stripe_subscription_id, 'sub_1')
        self.assertEqual(Tenant.objects.count(), 1)


    def reset(self):
        Subscription.objects.all().delete()
        User.objects.all().delete()
        Tenant.objects.all().delete()

    def test_a_mixed_case_existing_email_matches(self):
        user = User.objects.create(email='New.Buyer@Example.com', first_name='M', password='x')
        user.user_permissions.add(Permission.objects.get(codename='customer'))

        result = provision_from_checkout_session(session())

        self.assertEqual(result.outcome, Outcome.ATTACHED)
        self.assertEqual(result.user, user)
        self.assertEqual(User.objects.count(), 1)

    def test_users_are_not_soft_deleted(self):
        # User does not inherit SafeDeleteModel and has no `deleted` field,
        # so a removed user cannot hide behind a default manager.
        self.assertNotIn('deleted', [f.name for f in User._meta.get_fields()])
        self.assertTrue(User._meta.get_field('email').unique)

    def test_a_deactivated_user_is_still_found_and_nothing_collides_on_create(self):
        user = make_user(self.EMAIL)
        user.is_active = False
        user.save(update_fields=['is_active'])

        result = provision_from_checkout_session(session())

        self.assertEqual(result.outcome, Outcome.ATTACHED)
        self.assertEqual(User.objects.count(), 1)

    def test_only_an_ended_status_may_take_a_new_subscription(self):
        for status in sorted(ENDED_STATUSES):
            with self.subTest(status=status):
                self.reset()
                tenant = Tenant.objects.create()
                make_user(self.EMAIL, tenant=tenant)
                row = Subscription.objects.create(
                    tenant=tenant, status=status, stripe_subscription_id='sub_old',
                    stripe_checkout_session_id='cs_old',
                )

                result = provision_from_checkout_session(session())

                self.assertEqual(result.outcome, Outcome.REACTIVATED)
                row.refresh_from_db()
                self.assertEqual(row.stripe_subscription_id, 'sub_1')
                self.assertFalse(row.needs_review)

    def test_any_other_status_with_a_subscription_id_is_a_collision(self):
        statuses = [
            'trialing', 'active', 'past_due', 'unpaid', 'incomplete', 'paused',
            '', 'a_status_stripe_adds_later',
        ]
        for status in statuses:
            with self.subTest(status=status):
                self.reset()
                tenant = Tenant.objects.create()
                make_user(self.EMAIL, tenant=tenant)
                row = Subscription.objects.create(
                    tenant=tenant, status=status, stripe_subscription_id='sub_billing',
                )
                before = self.counts()

                with self.assertLogs('apps.billing.services.provisioning', level='ERROR'):
                    result = provision_from_checkout_session(session())

                self.assertEqual(result.outcome, Outcome.COLLISION)
                self.assertEqual(self.counts(), before)
                row.refresh_from_db()
                self.assertTrue(row.needs_review)
                self.assertEqual(row.stripe_subscription_id, 'sub_billing')
                self.assertNotEqual(row.stripe_checkout_session_id, 'cs_1')

    def test_a_row_that_never_held_a_subscription_id_takes_the_new_one(self):
        tenant = Tenant.objects.create()
        make_user(self.EMAIL, tenant=tenant)
        row = Subscription.objects.create(tenant=tenant)

        result = provision_from_checkout_session(session())

        self.assertEqual(result.outcome, Outcome.REACTIVATED)
        row.refresh_from_db()
        self.assertEqual(row.stripe_subscription_id, 'sub_1')
        self.assertFalse(row.needs_review)

    def test_an_exempt_row_is_a_collision_whatever_its_status(self):
        for status in ('', 'canceled'):
            with self.subTest(status=status):
                self.reset()
                tenant = Tenant.objects.create()
                make_user(self.EMAIL, tenant=tenant)
                row = Subscription.objects.create(tenant=tenant, billing_exempt=True, status=status)

                with self.assertLogs('apps.billing.services.provisioning', level='ERROR'):
                    result = provision_from_checkout_session(session())

                self.assertEqual(result.outcome, Outcome.COLLISION)
                row.refresh_from_db()
                self.assertTrue(row.needs_review)
                self.assertIsNone(row.stripe_subscription_id)


class ResubscribeTests(CountsMixin, TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create()
        make_user('owner@example.com', tenant=self.tenant)
        self.row = Subscription.objects.create(
            tenant=self.tenant, status='canceled', stripe_customer_id='cus_1',
            stripe_subscription_id='sub_old', stripe_checkout_session_id='cs_old',
            billing_email='owner@example.com',
        )

    def resubscribe_session(self, **over):
        data = {
            'id': 'cs_new', 'subscription': 'sub_new', 'customer': 'cus_1',
            'customer_details': None,
            'metadata': {'flow': 'resubscribe', 'tenant_id': str(self.tenant.pk)},
        }
        data.update(over)
        return session(**data)

    def test_updates_the_existing_row_and_creates_nothing(self):
        before = self.counts()
        with self.captureOnCommitCallbacks(execute=True):
            result = provision_from_checkout_session(self.resubscribe_session())

        self.assertEqual(result.outcome, Outcome.RESUBSCRIBED)
        self.assertEqual(self.counts(), before)
        self.row.refresh_from_db()
        self.assertEqual(self.row.stripe_subscription_id, 'sub_new')
        self.assertEqual(self.row.stripe_checkout_session_id, 'cs_new')
        self.assertEqual(self.row.stripe_customer_id, 'cus_1')
        self.assertEqual(result.subscription, self.row)

    def test_is_never_treated_as_a_collision_even_for_a_live_row(self):
        self.row.status = 'active'
        self.row.save()
        result = provision_from_checkout_session(self.resubscribe_session())
        self.assertEqual(result.outcome, Outcome.RESUBSCRIBED)
        self.row.refresh_from_db()
        self.assertFalse(self.row.needs_review)

    def test_an_email_on_the_session_does_not_create_a_user(self):
        before = self.counts()
        provision_from_checkout_session(self.resubscribe_session(
            customer_details={'email': 'someone.else@example.com'},
        ))
        self.assertEqual(self.counts(), before)

    def test_an_unknown_tenant_creates_nothing(self):
        before = self.counts()
        with self.assertLogs('apps.billing.services.provisioning', level='ERROR'):
            result = provision_from_checkout_session(self.resubscribe_session(
                metadata={'flow': 'resubscribe', 'tenant_id': '999999'},
            ))
        self.assertEqual(result.outcome, Outcome.UNMATCHED)
        self.assertEqual(self.counts(), before)

    def test_a_bad_tenant_id_creates_nothing(self):
        for bad in (None, '', 'abc'):
            with self.subTest(tenant_id=bad):
                before = self.counts()
                with self.assertLogs('apps.billing.services.provisioning', level='ERROR'):
                    result = provision_from_checkout_session(self.resubscribe_session(
                        metadata={'flow': 'resubscribe', 'tenant_id': bad},
                    ))
                self.assertEqual(result.outcome, Outcome.UNMATCHED)
                self.assertEqual(self.counts(), before)

    def test_ids_held_by_another_row_change_nothing(self):
        Subscription.objects.create(tenant=Tenant.objects.create(), stripe_subscription_id='sub_new')
        with self.assertLogs('apps.billing.services.provisioning', level='ERROR'):
            result = provision_from_checkout_session(self.resubscribe_session())
        self.assertEqual(result.outcome, Outcome.UNMATCHED)
        self.row.refresh_from_db()
        self.assertEqual(self.row.stripe_subscription_id, 'sub_old')

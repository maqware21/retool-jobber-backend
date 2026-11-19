import io
import logging
import re
import smtplib
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.billing.models import Subscription
from apps.billing.services import notifications
from apps.billing.services.provisioning import Outcome, provision_from_checkout_session
from apps.billing.tests.support import SyncBackgroundMixin
from apps.tenants.models import Tenant
from apps.users.models import User

EMAIL = 'new.customer@example.com'


def checkout_session(**over):
    data = {
        'id': 'cs_1', 'mode': 'subscription', 'status': 'complete',
        'customer': 'cus_1', 'subscription': 'sub_1',
        'customer_details': {'email': EMAIL, 'name': 'Ada Lovelace'}, 'metadata': {},
    }
    data.update(over)
    return data


def make_customer(email=EMAIL, password=None, tenant=True, **row_fields):
    user = User.create(
        email=email, first_name='Ada', password=password or 'x', permission='customer',
        check_validation=False,
    )
    if password is None:
        user.set_unusable_password()
    row = None
    if tenant:
        user.tenant = Tenant.objects.create()
        row = Subscription.objects.create(tenant=user.tenant, **row_fields)
    user.save()
    return user, row


@override_settings(APP_BASE_URL='https://app.example.test')
class WelcomeEmailAfterCommitTests(SyncBackgroundMixin, TestCase):
    def setUp(self):
        mail.outbox = []

    def test_it_is_sent_only_after_commit_with_a_working_link(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            result = provision_from_checkout_session(checkout_session())
        self.assertEqual(result.outcome, Outcome.PROVISIONED)
        self.assertEqual(len(mail.outbox), 0)
        self.assertIsNone(Subscription.objects.get().welcome_email_sent_at)

        for callback in callbacks:
            callback()

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [EMAIL])
        self.assertIn('Welcome', message.subject)
        link = re.search(r'https://\S+', message.body).group(0)
        parsed = urlparse(link)
        self.assertEqual(parsed.path, '/reset-password')
        self.assertTrue(link.startswith('https://app.example.test/reset-password?'))
        query = parse_qs(parsed.query)
        user = User.objects.get(email=EMAIL)
        self.assertTrue(default_token_generator.check_token(user, query['token'][0]))
        self.assertIn('Ada', message.body)
        self.assertEqual(message.alternatives[0][1], 'text/html')
        self.assertIn(link.replace('&', '&amp;'), message.alternatives[0][0])

    def test_success_sets_welcome_email_sent_at(self):
        with self.captureOnCommitCallbacks(execute=True):
            provision_from_checkout_session(checkout_session())
        self.assertIsNotNone(Subscription.objects.get().welcome_email_sent_at)

    def test_nothing_is_sent_when_the_transaction_rolls_back(self):
        with mock.patch.object(Subscription.objects, 'create', side_effect=RuntimeError('boom')):
            with self.captureOnCommitCallbacks(execute=True):
                with self.assertRaises(RuntimeError):
                    provision_from_checkout_session(checkout_session())
        self.assertEqual(len(mail.outbox), 0)

    def test_queue_hands_the_work_to_the_background_pool(self):
        user, _ = make_customer()
        with mock.patch('apps.billing.services.notifications.background_tasks.submit') as submit:
            notifications.queue_welcome_email(user)
        submit.assert_called_once_with(notifications.send_welcome_email, user.pk)

    def test_a_send_failure_logs_the_user_id_and_leaves_the_timestamp_null(self):
        user, row = make_customer()
        error = smtplib.SMTPRecipientsRefused({EMAIL: (550, b'mailbox unavailable')})

        with mock.patch('django.core.mail.message.EmailMultiAlternatives.send', side_effect=error):
            with self.assertLogs('apps.billing.services.notifications', level='ERROR') as logs:
                sent = notifications.send_welcome_email(user.pk)

        self.assertFalse(sent)
        row.refresh_from_db()
        self.assertIsNone(row.welcome_email_sent_at)
        text = '\n'.join(logs.output)
        self.assertIn(f'user id={user.pk}', text)
        self.assertIn('SMTPRecipientsRefused', text)
        self.assertNotIn(EMAIL, text)

    def test_an_inactive_or_missing_user_sends_nothing(self):
        user, row = make_customer()
        user.is_active = False
        user.save(update_fields=['is_active'])
        with self.assertLogs('apps.billing.services.notifications', level='ERROR'):
            self.assertFalse(notifications.send_welcome_email(user.pk))
        with self.assertLogs('apps.billing.services.notifications', level='ERROR'):
            self.assertFalse(notifications.send_welcome_email(999999))
        self.assertEqual(len(mail.outbox), 0)

    def test_the_provisioning_flow_logs_no_address_even_when_sending_fails(self):
        error = smtplib.SMTPRecipientsRefused({EMAIL: (550, b'mailbox unavailable')})
        lines = []

        class Collector(logging.Handler):
            def emit(self, record):
                lines.append(logging.Formatter().format(record))

        handler = Collector(level=logging.DEBUG)
        root = logging.getLogger()
        previous = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            with mock.patch('django.core.mail.message.EmailMultiAlternatives.send', side_effect=error):
                with self.captureOnCommitCallbacks(execute=True):
                    provision_from_checkout_session(checkout_session())
        finally:
            root.removeHandler(handler)
            root.setLevel(previous)

        self.assertTrue(lines)
        for line in lines:
            self.assertNotIn(EMAIL, line)
        self.assertIsNone(Subscription.objects.get().welcome_email_sent_at)


class ResendWelcomeEmailsCommandTests(SyncBackgroundMixin, TestCase):
    def setUp(self):
        mail.outbox = []

    def run_command(self, *args):
        out = io.StringIO()
        call_command('resend_welcome_emails', *args, stdout=out)
        return out.getvalue()

    def make_pending(self, email='pending@example.com'):
        return make_customer(email=email, stripe_subscription_id=f'sub_{email}')

    def test_it_sends_for_pending_rows_and_stamps_them(self):
        _, row = self.make_pending()

        output = self.run_command()

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['pending@example.com'])
        row.refresh_from_db()
        self.assertIsNotNone(row.welcome_email_sent_at)
        self.assertIn('Sent 1, failed 0, skipped 0.', output)

    def test_a_second_run_sends_nothing(self):
        self.make_pending()
        self.run_command()
        mail.outbox = []

        output = self.run_command()

        self.assertEqual(len(mail.outbox), 0)
        self.assertIn('Sent 0, failed 0, skipped 0.', output)

    def test_dry_run_sends_nothing_writes_nothing_and_shows_no_address(self):
        user, row = self.make_pending()

        output = self.run_command('--dry-run')

        self.assertEqual(len(mail.outbox), 0)
        row.refresh_from_db()
        self.assertIsNone(row.welcome_email_sent_at)
        self.assertIn('Would send 1, failed 0, skipped 0.', output)
        self.assertIn(f'user id={user.pk}', output)
        self.assertNotIn('pending@example.com', output)

    def test_exempt_rows_are_skipped(self):
        _, row = make_customer(billing_exempt=True)
        output = self.run_command()
        self.assertEqual(len(mail.outbox), 0)
        self.assertIsNone(Subscription.objects.get(pk=row.pk).welcome_email_sent_at)
        self.assertIn('Sent 0, failed 0, skipped 0.', output)

    def test_rows_already_sent_are_skipped(self):
        from django.utils import timezone
        make_customer(stripe_subscription_id='sub_x', welcome_email_sent_at=timezone.now())
        self.run_command()
        self.assertEqual(len(mail.outbox), 0)

    def test_rows_with_no_stripe_subscription_are_skipped(self):
        make_customer()
        self.run_command()
        self.assertEqual(len(mail.outbox), 0)

    def test_users_who_already_have_a_password_are_skipped(self):
        make_customer(password='Passw0rdX', stripe_subscription_id='sub_x')
        output = self.run_command()
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn('skipped 1', output)

    def test_a_tenant_with_no_active_user_is_skipped(self):
        user, _ = self.make_pending()
        user.is_active = False
        user.save(update_fields=['is_active'])
        output = self.run_command()
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn('skipped 1', output)

    def test_a_failure_is_counted_and_leaves_the_row_pending(self):
        _, row = self.make_pending()
        error = smtplib.SMTPRecipientsRefused({'pending@example.com': (550, b'no')})

        with mock.patch('django.core.mail.message.EmailMultiAlternatives.send', side_effect=error):
            with self.assertLogs('apps.billing.services.notifications', level='ERROR'):
                output = self.run_command()

        self.assertIn('Sent 0, failed 1, skipped 0.', output)
        row.refresh_from_db()
        self.assertIsNone(row.welcome_email_sent_at)

    def test_it_continues_after_a_failure(self):
        self.make_pending('first@example.com')
        _, second = self.make_pending('second@example.com')
        error = smtplib.SMTPRecipientsRefused({'first@example.com': (550, b'no')})
        real_send = mail.EmailMultiAlternatives.send

        def send(message, *args, **kwargs):
            if message.to == ['first@example.com']:
                raise error
            return real_send(message, *args, **kwargs)

        with mock.patch('django.core.mail.message.EmailMultiAlternatives.send', autospec=True, side_effect=send):
            with self.assertLogs('apps.billing.services.notifications', level='ERROR'):
                output = self.run_command()

        self.assertIn('Sent 1, failed 1, skipped 0.', output)
        second.refresh_from_db()
        self.assertIsNotNone(second.welcome_email_sent_at)

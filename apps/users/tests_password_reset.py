import logging
import re
import threading
from urllib.parse import parse_qs, urlparse

from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.billing.tests.support import SyncBackgroundMixin
from apps.users.models import User
from helpers import background_tasks

STRONG = 'Str0ngPassw0rd'
EMAIL = 'ada.lovelace@example.com'


def make_user(email=EMAIL, password='OldPassw0rd', is_active=True, **extra):
    user = User.create(
        email=email, first_name='Ada', password=password or 'x', permission='customer',
        check_validation=False,
    )
    if password is None:
        user.set_unusable_password()
    if not is_active:
        user.is_active = False
    user.save()
    return user


def make_link_parts(user):
    return (
        urlsafe_base64_encode(force_bytes(user.pk)),
        default_token_generator.make_token(user),
    )


@override_settings(APP_BASE_URL='https://app.example.test')
class ForgotPasswordTests(SyncBackgroundMixin, TestCase):
    url = '/v1/forgot-password/'

    def setUp(self):
        cache.clear()
        mail.outbox = []

    def post(self, email, **extra):
        return self.client.post(self.url, {'email': email}, content_type='application/json', **extra)

    def test_the_response_is_identical_for_unknown_inactive_and_valid_emails(self):
        make_user('active@example.com')
        make_user('inactive@example.com', is_active=False)

        responses = [
            self.post('nobody@example.com'),
            self.post('inactive@example.com'),
            self.post('active@example.com'),
        ]

        self.assertEqual([r.status_code for r in responses], [200, 200, 200])
        bodies = [r.json() for r in responses]
        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual(bodies[1], bodies[2])
        self.assertTrue(bodies[0]['success'])

    def test_exactly_one_email_is_sent_and_only_for_the_valid_account(self):
        make_user('active@example.com')
        make_user('inactive@example.com', is_active=False)

        self.post('nobody@example.com')
        self.post('inactive@example.com')
        self.assertEqual(len(mail.outbox), 0)

        self.post('active@example.com')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['active@example.com'])

    def test_the_email_has_text_and_html_with_a_working_link(self):
        user = make_user()

        self.post(EMAIL)

        message = mail.outbox[0]
        link = re.search(r'https://\S+', message.body).group(0)
        parsed = urlparse(link)
        self.assertEqual(f'{parsed.scheme}://{parsed.netloc}{parsed.path}', 'https://app.example.test/reset-password')
        query = parse_qs(parsed.query)
        self.assertEqual(set(query), {'uid', 'token'})
        self.assertTrue(default_token_generator.check_token(user, query['token'][0]))
        self.assertIn('&amp;token=', message.alternatives[0][0])
        self.assertEqual(message.alternatives[0][1], 'text/html')
        self.assertIn('&token=', message.body)
        self.assertNotIn('&amp;', message.body)

    def test_the_from_address_comes_from_settings(self):
        make_user()
        with override_settings(DEFAULT_FROM_EMAIL='Support <support@example.test>'):
            self.post(EMAIL)
        self.assertEqual(mail.outbox[0].from_email, 'Support <support@example.test>')

    def test_a_user_with_an_unusable_password_gets_the_email(self):
        make_user(password=None)
        self.post(EMAIL)
        self.assertEqual(len(mail.outbox), 1)

    def test_the_email_matches_case_insensitively(self):
        make_user()
        self.post('ADA.Lovelace@Example.COM')
        self.assertEqual(len(mail.outbox), 1)

    def test_a_missing_or_malformed_email_is_400(self):
        for body in ({}, {'email': ''}, {'email': 'not-an-email'}):
            with self.subTest(body=body):
                response = self.client.post(self.url, body, content_type='application/json')
                self.assertEqual(response.status_code, 400)
        self.assertEqual(len(mail.outbox), 0)

    def test_a_delivery_failure_does_not_change_the_response_and_logs_no_address(self):
        make_user()
        expected = self.post('nobody@example.com').json()

        with mock_send_failure(EMAIL):
            with self.assertLogs('apps.users.services.password_reset', level='ERROR') as logs:
                response = self.post(EMAIL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)
        text = '\n'.join(logs.output)
        self.assertIn('user id=', text)
        self.assertNotIn(EMAIL, text)

    def test_a_bad_authorization_header_does_not_block_a_public_endpoint(self):
        make_user()
        response = self.post(EMAIL, HTTP_AUTHORIZATION='Bearer not-a-real-token')
        self.assertEqual(response.status_code, 200)


class ForgotPasswordThrottleTests(SyncBackgroundMixin, TestCase):
    url = '/v1/forgot-password/'

    def setUp(self):
        cache.clear()
        mail.outbox = []

    def post(self, **extra):
        return self.client.post(self.url, {'email': 'x@example.com'}, content_type='application/json', **extra)

    def test_the_sixth_request_in_an_hour_is_429(self):
        statuses = [self.post().status_code for _ in range(6)]
        self.assertEqual(statuses, [200] * 5 + [429])

    def test_a_forged_forwarded_for_prefix_does_not_escape_the_limit(self):
        # One proxy sits in front of Django, so only the last entry counts.
        statuses = [
            self.post(HTTP_X_FORWARDED_FOR=f'10.0.0.{n}, 203.0.113.9').status_code
            for n in range(6)
        ]
        self.assertEqual(statuses, [200] * 5 + [429])

    def test_another_client_address_has_its_own_budget(self):
        for _ in range(5):
            self.post(HTTP_X_FORWARDED_FOR='203.0.113.9')
        self.assertEqual(self.post(HTTP_X_FORWARDED_FOR='203.0.113.9').status_code, 429)
        self.assertEqual(self.post(HTTP_X_FORWARDED_FOR='203.0.113.10').status_code, 200)

    def test_the_throttle_uses_the_default_cache(self):
        from django.conf import settings
        backend = settings.CACHES['default']['BACKEND'] if getattr(settings, 'CACHES', None) else None
        self.assertIn(backend, (None, 'django.core.cache.backends.locmem.LocMemCache'))


class ResetPasswordTests(TestCase):
    url = '/v1/reset-password/'

    def setUp(self):
        cache.clear()

    def post(self, uid, token, password=STRONG):
        body = {'uid': uid, 'token': token, 'new_password': password}
        return self.client.post(self.url, body, content_type='application/json')

    def test_a_valid_link_sets_the_password(self):
        user = make_user()
        uid, token = make_link_parts(user)

        response = self.post(uid, token)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])
        user.refresh_from_db()
        self.assertTrue(user.check_password(STRONG))
        self.assertFalse(user.check_password('OldPassw0rd'))

    def test_the_new_password_can_log_in(self):
        user = make_user()
        self.post(*make_link_parts(user))

        response = self.client.post(
            '/v1/login/', {'email': EMAIL, 'password': STRONG}, content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('access', response.json()['data']['token'])

    def test_the_same_token_fails_the_second_time(self):
        user = make_user()
        uid, token = make_link_parts(user)

        first = self.post(uid, token)
        second = self.post(uid, token, password='An0therStrongOne')

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 400)
        user.refresh_from_db()
        self.assertTrue(user.check_password(STRONG))

    def test_an_expired_token_fails(self):
        user = make_user()
        uid, token = make_link_parts(user)

        with override_settings(PASSWORD_RESET_TIMEOUT=-1):
            response = self.post(uid, token)

        self.assertEqual(response.status_code, 400)
        user.refresh_from_db()
        self.assertTrue(user.check_password('OldPassw0rd'))

    def test_every_bad_link_gets_the_same_400(self):
        user = make_user()
        other = make_user('other@example.com')
        inactive = make_user('inactive@example.com', is_active=False)
        uid, token = make_link_parts(user)
        other_uid, _ = make_link_parts(other)
        inactive_uid, inactive_token = make_link_parts(inactive)

        cases = {
            'wrong uid': (other_uid, token),
            'unknown uid': (urlsafe_base64_encode(force_bytes(999999)), token),
            'garbage uid': ('!!!', token),
            'non-numeric uid': (urlsafe_base64_encode(b'abc'), token),
            'bad token': (uid, 'not-a-token'),
            'wrong-format token': (uid, 'abc-def'),
            'inactive user': (inactive_uid, inactive_token),
            'empty': ('', ''),
        }
        bodies = set()
        for name, (case_uid, case_token) in cases.items():
            with self.subTest(case=name):
                response = self.post(case_uid, case_token)
                self.assertEqual(response.status_code, 400)
                bodies.add(response.content)
        self.assertEqual(len(bodies), 1)
        user.refresh_from_db()
        self.assertTrue(user.check_password('OldPassw0rd'))

    def test_missing_uid_and_token_fields_are_the_same_generic_400(self):
        response = self.client.post(self.url, {'new_password': STRONG}, content_type='application/json')
        generic = self.post('', '')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.content, generic.content)

    def test_the_password_validators_are_enforced(self):
        user = make_user()
        uid, token = make_link_parts(user)

        for weak in ('Sh0rt', 'alllowercase1', 'ALLUPPERCASE1', 'NoDigitsHere', ''):
            with self.subTest(password=weak):
                response = self.post(uid, token, password=weak)
                self.assertEqual(response.status_code, 400)

        user.refresh_from_db()
        self.assertTrue(user.check_password('OldPassw0rd'))
        # A rejected password does not use up the link.
        self.assertEqual(self.post(uid, token).status_code, 200)

    def test_a_missing_password_is_400(self):
        user = make_user()
        uid, token = make_link_parts(user)
        response = self.client.post(self.url, {'uid': uid, 'token': token}, content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_a_user_with_an_unusable_password_can_set_a_first_password(self):
        user = make_user(password=None)
        self.assertFalse(user.has_usable_password())
        uid, token = make_link_parts(user)

        response = self.post(uid, token)

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.has_usable_password())
        self.assertTrue(user.check_password(STRONG))
        self.assertEqual(self.post(uid, token).status_code, 400)

    def test_a_link_from_the_forgot_password_email_works_end_to_end(self):
        user = make_user(password=None)
        cache.clear()
        mail.outbox = []
        with SyncBackground():
            self.client.post('/v1/forgot-password/', {'email': EMAIL}, content_type='application/json')
        query = parse_qs(urlparse(re.search(r'https://\S+', mail.outbox[0].body).group(0)).query)

        response = self.post(query['uid'][0], query['token'][0])

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password(STRONG))

    def test_a_reset_attempt_is_throttled_per_address(self):
        statuses = [self.post('', '').status_code for _ in range(21)]
        self.assertEqual(statuses[-1], 429)
        self.assertEqual(set(statuses[:-1]), {400})


class BackgroundTasksTests(TestCase):
    def test_a_task_runs_on_another_thread(self):
        seen = []
        background_tasks.submit(lambda: seen.append(threading.current_thread().name)).result(timeout=5)
        self.assertEqual(len(seen), 1)
        self.assertNotEqual(seen[0], threading.current_thread().name)
        self.assertTrue(seen[0].startswith('background-task'))

    def test_a_failing_task_is_logged_by_type_only_and_does_not_raise(self):
        def fail():
            raise ValueError(f'could not send to {EMAIL}')

        with self.assertLogs('helpers.background_tasks', level='ERROR') as logs:
            background_tasks.submit(fail).result(timeout=5)

        text = '\n'.join(logs.output)
        self.assertIn('ValueError', text)
        self.assertNotIn(EMAIL, text)


class SyncBackground:
    """Context manager form of SyncBackgroundMixin, for one block of a test."""

    def __enter__(self):
        from unittest import mock
        from apps.billing.tests.support import run_inline
        self.patcher = mock.patch('helpers.background_tasks.submit', side_effect=run_inline)
        self.patcher.start()

    def __exit__(self, *exc):
        self.patcher.stop()


class mock_send_failure:
    """Makes sending raise an SMTP-style error whose message quotes the address."""

    def __init__(self, address):
        self.address = address

    def __enter__(self):
        import smtplib
        from unittest import mock
        error = smtplib.SMTPRecipientsRefused({self.address: (550, b'mailbox unavailable')})
        self.patcher = mock.patch(
            'django.core.mail.message.EmailMultiAlternatives.send', side_effect=error,
        )
        self.patcher.start()

    def __exit__(self, *exc):
        self.patcher.stop()


class NoAddressInLogsTests(SyncBackgroundMixin, TestCase):
    def setUp(self):
        cache.clear()

    def test_no_log_line_across_the_flow_contains_an_address(self):
        records = []

        class Collector(logging.Handler):
            def emit(self, record):
                records.append(logging.Formatter().format(record))

        handler = Collector(level=logging.DEBUG)
        root = logging.getLogger()
        previous = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            user = make_user(password=None)
            self.client.post('/v1/forgot-password/', {'email': EMAIL}, content_type='application/json')
            self.client.post('/v1/forgot-password/', {'email': 'nobody@example.com'}, content_type='application/json')
            with mock_send_failure(EMAIL):
                self.client.post('/v1/forgot-password/', {'email': EMAIL}, content_type='application/json')
            self.client.post(
                '/v1/reset-password/', {'uid': 'x', 'token': 'y', 'new_password': STRONG},
                content_type='application/json',
            )
            uid, token = make_link_parts(user)
            self.client.post(
                '/v1/reset-password/', {'uid': uid, 'token': token, 'new_password': STRONG},
                content_type='application/json',
            )
        finally:
            root.removeHandler(handler)
            root.setLevel(previous)

        self.assertTrue(records)
        for line in records:
            self.assertNotIn(EMAIL, line)
            self.assertNotIn('nobody@example.com', line)

import re
from pathlib import Path
from unittest import mock

import stripe
from django.test import SimpleTestCase, override_settings

import apps.billing
from apps.billing.services import stripe_client
from apps.billing.services.stripe_client import (
    StripeNotConfigured,
    get_client,
    make_idempotency_key,
)


class GetClientTests(SimpleTestCase):
    @override_settings(STRIPE_SECRET_KEY='', STRIPE_API_VERSION='2026-10-28.endive')
    def test_raises_when_the_key_is_not_set(self):
        with self.assertRaises(StripeNotConfigured):
            get_client()

    @override_settings(STRIPE_SECRET_KEY='test-key-not-real', STRIPE_API_VERSION='2026-10-28.endive')
    def test_builds_the_client_from_settings(self):
        http_client = object()
        with mock.patch.object(stripe, 'StripeClient') as client_cls, \
                mock.patch.object(stripe, 'new_default_http_client', return_value=http_client) as new_http:
            result = get_client()

        new_http.assert_called_once_with(timeout=stripe_client.STRIPE_TIMEOUT_SECONDS)
        client_cls.assert_called_once_with(
            'test-key-not-real',
            stripe_version='2026-10-28.endive',
            max_network_retries=1,
            http_client=http_client,
        )
        self.assertIs(result, client_cls.return_value)

    def test_timeout_is_short_and_there_is_one_retry(self):
        self.assertLessEqual(stripe_client.STRIPE_TIMEOUT_SECONDS, 15)
        self.assertEqual(stripe_client.STRIPE_MAX_NETWORK_RETRIES, 1)

    @override_settings(STRIPE_SECRET_KEY='test-key-not-real', STRIPE_API_VERSION='2026-10-28.endive')
    def test_a_real_client_object_is_built_without_touching_the_network(self):
        client = get_client()
        self.assertIsInstance(client, stripe.StripeClient)


class GlobalKeyTests(SimpleTestCase):
    @override_settings(STRIPE_SECRET_KEY='test-key-not-real', STRIPE_API_VERSION='2026-10-28.endive')
    def test_building_a_client_does_not_set_the_global_key(self):
        before = stripe.api_key
        get_client()
        self.assertEqual(stripe.api_key, before)
        self.assertIsNone(stripe.api_key)

    def test_no_billing_code_assigns_the_global_key(self):
        root = Path(apps.billing.__file__).parent
        pattern = re.compile(r'stripe\.api_key\s*=')
        offenders = [
            str(path.relative_to(root))
            for path in root.rglob('*.py')
            if 'tests' not in path.parts and pattern.search(path.read_text(encoding='utf-8'))
        ]
        self.assertEqual(offenders, [])


class IdempotencyKeyTests(SimpleTestCase):
    def test_same_inputs_give_the_same_key(self):
        self.assertEqual(
            make_idempotency_key('checkout-session', 'a@example.com'),
            make_idempotency_key('checkout-session', 'a@example.com'),
        )

    def test_known_value_does_not_drift(self):
        # A retry after a deploy must still send the same key, so the
        # derivation is pinned to a literal value.
        self.assertEqual(
            make_idempotency_key('checkout-session', 'a@example.com'),
            'ttp-checkout-session-7d55f363d1e47718007bfa4c2e95084c8a8e9a30',
        )

    def test_different_inputs_give_different_keys(self):
        keys = {
            make_idempotency_key('checkout-session', 'a@example.com'),
            make_idempotency_key('checkout-session', 'b@example.com'),
            make_idempotency_key('portal-session', 'a@example.com'),
            make_idempotency_key('checkout-session', 'a@example.com', 7),
        }
        self.assertEqual(len(keys), 4)

    def test_part_boundaries_are_not_ambiguous(self):
        self.assertNotEqual(
            make_idempotency_key('s', 'ab', 'c'),
            make_idempotency_key('s', 'a', 'bc'),
        )

    def test_numbers_and_their_text_agree(self):
        self.assertEqual(make_idempotency_key('s', 7), make_idempotency_key('s', '7'))

    def test_length_is_within_stripes_limit(self):
        self.assertLessEqual(len(make_idempotency_key('s', 'x' * 5000)), 255)

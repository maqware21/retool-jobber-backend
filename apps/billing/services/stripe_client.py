"""
The only place a Stripe client is built.

The key, API version, timeout and retry count are applied to a client
object. The module-global `stripe.api_key` is never assigned, so no other
code path can pick up, or change, the key by accident.
"""
import hashlib

import stripe
from django.conf import settings

# A webhook must answer quickly and gunicorn kills a worker after 30
# seconds, so a Stripe call gets a short timeout and a single retry.
STRIPE_TIMEOUT_SECONDS = 10
STRIPE_MAX_NETWORK_RETRIES = 1

# Re-exported so callers catch Stripe failures without importing stripe.
StripeError = stripe.StripeError


class StripeNotConfigured(Exception):
    """STRIPE_SECRET_KEY is empty."""


def get_client():
    """Return a StripeClient built from settings, or raise StripeNotConfigured."""
    if not settings.STRIPE_SECRET_KEY:
        raise StripeNotConfigured('STRIPE_SECRET_KEY is not set.')
    return stripe.StripeClient(
        settings.STRIPE_SECRET_KEY,
        stripe_version=settings.STRIPE_API_VERSION,
        max_network_retries=STRIPE_MAX_NETWORK_RETRIES,
        http_client=stripe.new_default_http_client(timeout=STRIPE_TIMEOUT_SECONDS),
    )


def make_idempotency_key(scope, *parts):
    """
    Build an idempotency key from a stable value, so a retry of the same
    operation sends the same key and a different operation never shares one.

    `scope` names the operation (for example 'checkout-session'); `parts`
    are the values that identify one logical attempt (for example an
    email). Order matters. The result is well inside Stripe's 255-character
    limit.
    """
    material = '\x1f'.join([scope, *(str(part) for part in parts)])
    digest = hashlib.sha256(material.encode('utf-8')).hexdigest()[:40]
    return f'ttp-{scope}-{digest}'

import logging
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.db import transaction

from apps.billing.models import Subscription
from apps.billing.services.stripe_client import StripeNotConfigured, get_client

logger = logging.getLogger(__name__)


def _to_datetime(unix_seconds):
    if not unix_seconds:
        return None
    return datetime.fromtimestamp(unix_seconds, tz=dt_timezone.utc)


def _customer_id(stripe_subscription):
    customer = stripe_subscription.get('customer')
    if isinstance(customer, str):
        return customer
    return customer['id'] if customer else None


def _our_item(stripe_subscription):
    """The subscription item on STRIPE_PRICE_ID, or None when there is none."""
    for item in stripe_subscription['items']['data']:
        if item['price']['id'] == settings.STRIPE_PRICE_ID:
            return item
    return None


def refresh_from_stripe(stripe_subscription_id):
    """
    Retrieve the subscription from Stripe and write its whole current state
    to the Subscription row with that `stripe_subscription_id`. Returns the
    row, or None when nothing was changed.

    Nothing from a webhook payload is used, so the order events arrive in
    cannot matter: the row always ends up as Stripe reports it now.
    Nothing is changed when no row has this subscription id, or when the
    subscription is not on STRIPE_PRICE_ID. An empty STRIPE_PRICE_ID is a
    configuration error and raises StripeNotConfigured, so the caller fails
    and Stripe redelivers the event once the setting is fixed.

    The row is locked before Stripe is asked, so two refreshes of one
    subscription run one after the other and the later one fetches the
    later state. `current_period_end` is read from the subscription item
    because the pinned API version no longer puts it on the subscription.
    """
    if not settings.STRIPE_PRICE_ID:
        raise StripeNotConfigured('STRIPE_PRICE_ID is not set.')

    with transaction.atomic():
        row = (
            Subscription.objects.select_for_update()
            .filter(stripe_subscription_id=stripe_subscription_id)
            .first()
        )
        if row is None:
            logger.warning(
                "Stripe subscription %s has no Subscription row; nothing changed.",
                stripe_subscription_id,
            )
            return None

        stripe_subscription = get_client().v1.subscriptions.retrieve(stripe_subscription_id)

        item = _our_item(stripe_subscription)
        if item is None:
            logger.warning(
                "Stripe subscription %s is not on the configured price; nothing changed.",
                stripe_subscription_id,
            )
            return None

        row.status = stripe_subscription['status']
        row.trial_end = _to_datetime(stripe_subscription.get('trial_end'))
        row.current_period_end = _to_datetime(item.get('current_period_end'))
        row.cancel_at_period_end = bool(stripe_subscription.get('cancel_at_period_end'))
        row.canceled_at = _to_datetime(stripe_subscription.get('canceled_at'))
        customer_id = _customer_id(stripe_subscription)
        if customer_id:
            row.stripe_customer_id = customer_id
        row.save(update_fields=[
            'status', 'trial_end', 'current_period_end', 'cancel_at_period_end',
            'canceled_at', 'stripe_customer_id', 'updated_at',
        ])
        return row

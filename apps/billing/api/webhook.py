"""
Stripe webhook receiver.

POST /v1/billing/webhook/ is public and authenticated by the Stripe-Signature
header. Each event is applied in one transaction together with its
StripeEvent row, so a failure leaves nothing behind and Stripe's redelivery
starts clean, while a redelivered event that already succeeded is skipped.

Responses: 200 when the event was applied, ignored on purpose, or is a
duplicate; 400 for a missing or invalid signature or an unparseable body;
500 for anything that should be retried (a processing error, or a missing
STRIPE_WEBHOOK_SECRET / STRIPE_PRICE_ID / STRIPE_SECRET_KEY).

Log lines carry the event id and type, and never the payload, an email or
card data.
"""
import logging
import sys
from datetime import datetime, timezone as dt_timezone

import stripe
from django.conf import settings
from django.db import DatabaseError, IntegrityError, transaction
from django.http import HttpResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.billing.models import StripeEvent
from apps.billing.services.provisioning import (
    Outcome,
    flag_for_review,
    provision_from_checkout_session,
)
from apps.billing.services.stripe_client import to_plain
from apps.billing.services.sync import refresh_from_stripe

logger = logging.getLogger(__name__)

_CHECKOUT_COMPLETED = 'checkout.session.completed'
_SUBSCRIPTION_EVENTS = frozenset({
    'customer.subscription.created',
    'customer.subscription.updated',
    'customer.subscription.deleted',
})
_INVOICE_EVENTS = frozenset({
    'invoice.paid',
    'invoice.payment_failed',
    'invoice.payment_action_required',
})

# Outcomes that leave a Subscription row whose live state still has to be
# read from Stripe, because provisioning stores ids but not the status.
_REFRESH_OUTCOMES = frozenset({
    Outcome.PROVISIONED,
    Outcome.ATTACHED,
    Outcome.REACTIVATED,
    Outcome.RESUBSCRIBED,
    Outcome.ALREADY_PROVISIONED,
})


class _DuplicateEvent(Exception):
    """The event id is already recorded."""


def _log(level, event_id, event_type, message, *args):
    logger.log(level, "stripe event %s (%s): " + message, event_id, event_type, *args)


def _log_failure(event_id, event_type):
    """
    Log the exception being handled. Database errors can quote row values
    (a unique violation names the email), so their message and cause chain
    are replaced; the traceback frames are kept.
    """
    message = "stripe event %s (%s): processing failed; answering 500 so Stripe retries."
    exc = sys.exc_info()[1]
    if isinstance(exc, DatabaseError):
        redacted = type(exc)('database error details withheld')
        logger.error(
            message, event_id, event_type,
            exc_info=(type(exc), redacted, exc.__traceback__),
        )
    else:
        logger.exception(message, event_id, event_type)


def _record_event(event_id, event_type, created):
    """
    Insert the StripeEvent row inside its own savepoint, so a duplicate id
    can be recognised without aborting the surrounding transaction.
    """
    try:
        with transaction.atomic():
            return StripeEvent.objects.create(
                event_id=event_id,
                event_type=event_type,
                stripe_created=datetime.fromtimestamp(created, tz=dt_timezone.utc),
            )
    except IntegrityError:
        if StripeEvent.objects.filter(event_id=event_id).exists():
            raise _DuplicateEvent()
        raise


def _invoice_subscription_id(invoice):
    """
    The subscription id on an invoice. Older API versions put it at the top
    level; newer ones nest it under parent.subscription_details.
    """
    value = invoice.get('subscription')
    if not value:
        details = (invoice.get('parent') or {}).get('subscription_details') or {}
        value = details.get('subscription')
    if isinstance(value, str):
        return value
    return value.get('id') if value else None


def _handle_checkout_completed(event_id, event_type, session):
    result = provision_from_checkout_session(session)

    if result.outcome in _REFRESH_OUTCOMES:
        row = result.subscription
        subscription_id = row.stripe_subscription_id
        refreshed = refresh_from_stripe(subscription_id) if subscription_id else None
        if refreshed is None:
            note = (
                "The subscription could not be refreshed from Stripe after checkout "
                "(unknown subscription or not on the configured price)."
            )
            flag_for_review(row, note)
            _log(logging.ERROR, event_id, event_type, "%s Row flagged for review.", note)
            return note
        return ''

    if result.outcome == Outcome.IGNORED:
        _log(logging.INFO, event_id, event_type, "checkout session ignored.")
        return ''

    _log(logging.ERROR, event_id, event_type, "checkout not provisioned (%s): %s",
         result.outcome, result.note)
    return result.note


def _apply(event_id, event_type, event):
    """Apply one event's change and return a note for the StripeEvent row."""
    obj = event['data']['object']

    if event_type == _CHECKOUT_COMPLETED:
        return _handle_checkout_completed(event_id, event_type, obj)

    if event_type in _SUBSCRIPTION_EVENTS:
        subscription_id = obj.get('id')
    elif event_type in _INVOICE_EVENTS:
        subscription_id = _invoice_subscription_id(obj)
    else:
        _log(logging.INFO, event_id, event_type, "no handler; recorded only.")
        return 'no handler for this event type'

    if not subscription_id:
        _log(logging.WARNING, event_id, event_type, "no subscription id on the event.")
        return 'no subscription id on the event'

    if refresh_from_stripe(subscription_id) is None:
        _log(logging.WARNING, event_id, event_type,
             "subscription %s not refreshed (unknown subscription or wrong price).",
             subscription_id)
        return 'no matching subscription row or price'
    return ''


@method_decorator(csrf_exempt, name='dispatch')
class StripeWebhookView(View):
    http_method_names = ['post']

    def post(self, request):
        # The raw body is read first: the signature covers its exact bytes.
        payload = request.body
        signature = request.headers.get('Stripe-Signature', '').strip()

        if not signature:
            logger.warning("Stripe webhook rejected: missing Stripe-Signature header.")
            return HttpResponse(status=400)

        secret = settings.STRIPE_WEBHOOK_SECRET
        if not secret:
            logger.error("Stripe webhook cannot verify signatures: STRIPE_WEBHOOK_SECRET is not set.")
            return HttpResponse(status=500)

        try:
            event = to_plain(stripe.Webhook.construct_event(payload, signature, secret))
        except stripe.SignatureVerificationError:
            logger.warning("Stripe webhook rejected: signature verification failed.")
            return HttpResponse(status=400)
        except ValueError:
            logger.warning("Stripe webhook rejected: the body could not be parsed.")
            return HttpResponse(status=400)

        event_id = event.get('id')
        event_type = event.get('type')
        created = event.get('created')
        if not (event_id and event_type and created):
            logger.warning("Stripe webhook rejected: the event has no id, type or created time.")
            return HttpResponse(status=400)

        _log(logging.INFO, event_id, event_type, "received.")

        try:
            with transaction.atomic():
                record = _record_event(event_id, event_type, created)
                note = _apply(event_id, event_type, event)
                record.processed_at = timezone.now()
                record.note = note
                record.save(update_fields=['processed_at', 'note'])
        except _DuplicateEvent:
            _log(logging.INFO, event_id, event_type, "duplicate delivery; nothing applied.")
            return HttpResponse(status=200)
        except Exception:
            _log_failure(event_id, event_type)
            return HttpResponse(status=500)

        return HttpResponse(status=200)

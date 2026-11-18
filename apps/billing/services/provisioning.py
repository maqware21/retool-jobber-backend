import logging
from dataclasses import dataclass

from django.db import transaction

from apps.billing.models import Subscription
from apps.billing.services import notifications
from apps.tenants.models import Tenant
from apps.users.models import User

logger = logging.getLogger(__name__)

# Checkout Session metadata that marks a resubscribe for an existing tenant.
METADATA_FLOW = 'flow'
METADATA_FLOW_RESUBSCRIBE = 'resubscribe'
METADATA_TENANT_ID = 'tenant_id'

# Stripe statuses that mean the subscription is over and cannot recover. Only
# a row in one of these may take a new subscription. Any other status,
# including a blank one and any status Stripe adds later, may still be
# billing, so overwriting its ids could orphan a live Stripe subscription.
ENDED_STATUSES = frozenset({'canceled', 'incomplete_expired'})

_PRIVILEGED_ROLES = frozenset({'admin', 'superadmin'})


class Outcome:
    IGNORED = 'ignored'
    ALREADY_PROVISIONED = 'already_provisioned'
    PROVISIONED = 'provisioned'
    ATTACHED = 'attached'
    REACTIVATED = 'reactivated'
    RESUBSCRIBED = 'resubscribed'
    COLLISION = 'collision'
    UNMATCHED = 'unmatched'


@dataclass(frozen=True)
class ProvisionResult:
    """
    What the webhook needs to know. Every outcome is a normal return: none
    of them is a reason for Stripe to redeliver the event. A real failure
    (database error, missing customer role) raises instead.

    `subscription` is the row to refresh from Stripe next: provisioning
    stores ids but not the live status, which only Stripe can report.
    """
    outcome: str
    subscription: Subscription | None = None
    user: User | None = None
    note: str = ''


class ProvisioningError(Exception):
    """Provisioning failed in a way that should be retried."""


def _subscription_id(session):
    value = session.get('subscription')
    if isinstance(value, str):
        return value
    return value['id'] if value else None


def _customer_id(session):
    value = session.get('customer')
    if isinstance(value, str):
        return value
    return value['id'] if value else None


def _email(session):
    details = session.get('customer_details') or {}
    raw = details.get('email') or session.get('customer_email') or ''
    return raw.strip().lower()


def _names(session, email):
    """(first_name, last_name) from the Checkout name, else from the email."""
    full = ((session.get('customer_details') or {}).get('name') or '').strip()
    if full:
        first, _, rest = full.partition(' ')
        return first, (rest.strip() or None)
    return email.split('@', 1)[0], None


def _append_note(existing, note):
    return f"{existing}\n{note}".strip() if existing else note


def flag_for_review(row, note):
    row.needs_review = True
    row.review_note = _append_note(row.review_note, note)
    row.save(update_fields=['needs_review', 'review_note', 'updated_at'])


def provision_from_checkout_session(session):
    """
    Turn a completed Checkout Session into billing state.

    `session` is the Checkout Session as a mapping. It is acted on only
    when `mode == "subscription"` and `status == "complete"`; payment_status
    is not consulted, because a trial session reports "no_payment_required".

    Cases, in order:
      - a Subscription already holds this session id: nothing to do;
      - a resubscribe session (metadata): the tenant's existing row gets the
        new subscription id; no tenant or user is created;
      - the email belongs to a deactivated User (is_active=False): nothing
        is created and that user's tenant row, if any, is flagged for review;
      - the email belongs to a User with no tenant: a Tenant and
        Subscription are attached to that user;
      - the email belongs to a User whose tenant row is exempt, or has a
        subscription that is not known to have ended, or belongs to an
        admin: nothing is created and the existing row is flagged for
        review;
      - the email belongs to a User whose tenant row has ended
        ('canceled' or 'incomplete_expired') or never held a subscription
        id: that row takes the new subscription;
      - otherwise a Tenant, a User with an unusable password and the
        customer role, and a Subscription are created in one transaction.
    """
    if session.get('mode') != 'subscription' or session.get('status') != 'complete':
        return ProvisionResult(Outcome.IGNORED)

    session_id = session['id']
    subscription_id = _subscription_id(session)
    customer_id = _customer_id(session)
    if not subscription_id:
        logger.error("Checkout session %s is complete but has no subscription.", session_id)
        return ProvisionResult(Outcome.UNMATCHED, note='no subscription on the session')

    existing = Subscription.objects.filter(stripe_checkout_session_id=session_id).first()
    if existing is not None:
        return ProvisionResult(Outcome.ALREADY_PROVISIONED, subscription=existing)

    metadata = session.get('metadata') or {}
    if metadata.get(METADATA_FLOW) == METADATA_FLOW_RESUBSCRIBE:
        return _resubscribe(session_id, subscription_id, customer_id, metadata)

    email = _email(session)
    if not email:
        logger.error("Checkout session %s has no email; nothing created.", session_id)
        return ProvisionResult(Outcome.UNMATCHED, note='no email on the session')

    if _stripe_ids_taken(subscription_id, customer_id):
        logger.error(
            "Checkout session %s carries Stripe ids that another Subscription already holds.",
            session_id,
        )
        return ProvisionResult(Outcome.UNMATCHED, note='Stripe ids already in use')

    # Emails are compared case-insensitively because older rows may not be
    # lower-cased. Deactivated users (is_active=False) are still found:
    # User is not soft-deleted, so its email stays taken.
    user = User.objects.filter(email__iexact=email).order_by('pk').first()
    with transaction.atomic():
        if user is None:
            return _create_new_account(session, email, session_id, subscription_id, customer_id)
        return _existing_user(user, email, session_id, subscription_id, customer_id)


def _stripe_ids_taken(subscription_id, customer_id, exclude_pk=None):
    qs = Subscription.objects.filter(stripe_subscription_id=subscription_id)
    if customer_id:
        qs = qs | Subscription.objects.filter(stripe_customer_id=customer_id)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return qs.exists()


def _resubscribe(session_id, subscription_id, customer_id, metadata):
    try:
        tenant_id = int(metadata.get(METADATA_TENANT_ID))
    except (TypeError, ValueError):
        tenant_id = None

    with transaction.atomic():
        row = (
            Subscription.objects.select_for_update().filter(tenant_id=tenant_id).first()
            if tenant_id is not None else None
        )
        if row is None:
            logger.error(
                "Resubscribe session %s names a tenant with no Subscription row; nothing changed.",
                session_id,
            )
            return ProvisionResult(Outcome.UNMATCHED, note='resubscribe tenant not found')
        if _stripe_ids_taken(subscription_id, customer_id, exclude_pk=row.pk):
            logger.error(
                "Resubscribe session %s carries Stripe ids that another Subscription already holds.",
                session_id,
            )
            return ProvisionResult(Outcome.UNMATCHED, note='Stripe ids already in use')
        _point_row_at_session(row, session_id, subscription_id, customer_id)
        return ProvisionResult(Outcome.RESUBSCRIBED, subscription=row)


def _point_row_at_session(row, session_id, subscription_id, customer_id):
    row.stripe_subscription_id = subscription_id
    row.stripe_checkout_session_id = session_id
    if customer_id:
        row.stripe_customer_id = customer_id
    row.save(update_fields=[
        'stripe_subscription_id', 'stripe_checkout_session_id',
        'stripe_customer_id', 'updated_at',
    ])


def _create_new_account(session, email, session_id, subscription_id, customer_id):
    first_name, last_name = _names(session, email)
    tenant = Tenant.objects.create()
    user = User.create(
        email=email,
        first_name=first_name,
        last_name=last_name,
        password=None,
        permission='customer',
        check_validation=False,
    )
    if user is None:
        raise ProvisioningError("The user could not be created.")
    if user.role != 'customer':
        raise ProvisioningError("The customer role is missing, so the user would have no access.")
    user.tenant = tenant
    user.save(update_fields=['tenant'])
    row = Subscription.objects.create(
        tenant=tenant,
        billing_email=email,
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        stripe_checkout_session_id=session_id,
    )
    transaction.on_commit(lambda: notifications.queue_welcome_email(user))
    return ProvisionResult(Outcome.PROVISIONED, subscription=row, user=user)


def _existing_user(user, email, session_id, subscription_id, customer_id):
    if user.role in _PRIVILEGED_ROLES:
        logger.error(
            "Checkout session %s used the email of a %s user (id=%s); nothing created.",
            session_id, user.role, user.pk,
        )
        return ProvisionResult(
            Outcome.COLLISION, user=user, note='email belongs to a platform staff user',
        )

    if not user.is_active:
        note = (
            f"Checkout session {session_id} (subscription {subscription_id}) was paid for "
            "an email that belongs to a deactivated user. Nothing was created."
        )
        row = (
            Subscription.objects.select_for_update().filter(tenant_id=user.tenant_id).first()
            if user.tenant_id is not None else None
        )
        if row is not None:
            flag_for_review(row, note)
        logger.error(
            "Checkout session %s used the email of a deactivated user (id=%s); nothing created.",
            session_id, user.pk,
        )
        return ProvisionResult(Outcome.COLLISION, subscription=row, user=user, note=note)

    if user.tenant_id is None:
        tenant = Tenant.objects.create()
        user.tenant = tenant
        user.save(update_fields=['tenant'])
        row = Subscription.objects.create(
            tenant=tenant,
            billing_email=email,
            stripe_customer_id=customer_id,
            stripe_subscription_id=subscription_id,
            stripe_checkout_session_id=session_id,
        )
        return ProvisionResult(Outcome.ATTACHED, subscription=row, user=user)

    row = Subscription.objects.select_for_update().filter(tenant_id=user.tenant_id).first()
    if row is None:
        row = Subscription.objects.create(
            tenant_id=user.tenant_id,
            billing_email=email,
            stripe_customer_id=customer_id,
            stripe_subscription_id=subscription_id,
            stripe_checkout_session_id=session_id,
        )
        return ProvisionResult(Outcome.REACTIVATED, subscription=row, user=user)

    may_take_new_subscription = (
        row.status in ENDED_STATUSES or not row.stripe_subscription_id
    )
    if row.billing_exempt or not may_take_new_subscription:
        note = (
            f"Checkout session {session_id} (subscription {subscription_id}) was paid for "
            "an email whose subscription is exempt or not known to have ended. "
            "Nothing was created."
        )
        flag_for_review(row, note)
        logger.error(
            "Checkout session %s collides with tenant %s (user id=%s); flagged for review.",
            session_id, user.tenant_id, user.pk,
        )
        return ProvisionResult(Outcome.COLLISION, subscription=row, user=user, note=note)

    if not row.billing_email:
        row.billing_email = email
        row.save(update_fields=['billing_email', 'updated_at'])
    _point_row_at_session(row, session_id, subscription_id, customer_id)
    return ProvisionResult(Outcome.REACTIVATED, subscription=row, user=user)

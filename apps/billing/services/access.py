"""
Pure access rules for a tenant's subscription. Nothing here touches the
database or Stripe: both functions read attributes off the object they are
given, so they can be tested with a plain stand-in.
"""

# Stripe subscription statuses that grant access. `cancel_at_period_end`
# is deliberately not consulted: a subscription set to cancel stays
# `active` until Stripe ends it at the period end.
ACCESS_STATUSES = frozenset({'trialing', 'active'})

REASON_NO_SUBSCRIPTION = 'no_subscription'

# Lock reasons for statuses that carry their own meaning. `incomplete_expired`
# is a subscription that never started, which reads to the user as ended.
# Every other status without access (`incomplete`, `paused`, blank, or a
# status Stripe adds later) falls back to REASON_NO_SUBSCRIPTION.
_STATUS_REASONS = {
    'past_due': 'past_due',
    'unpaid': 'unpaid',
    'canceled': 'canceled',
    'incomplete_expired': 'canceled',
}


def has_access(subscription):
    """
    True when the subscription is billing-exempt or in a status that
    grants access. A missing subscription (None) never has access.
    """
    if subscription is None:
        return False
    if subscription.billing_exempt:
        return True
    return subscription.status in ACCESS_STATUSES


def lock_reason(subscription, has_tenant=True):
    """
    Why access is denied, or None when it is granted.

    Returns one of 'past_due', 'unpaid', 'canceled', 'no_subscription'.
    `has_tenant=False` (a user with no tenant) is always
    'no_subscription'.
    """
    if not has_tenant:
        return REASON_NO_SUBSCRIPTION
    if has_access(subscription):
        return None
    if subscription is None:
        return REASON_NO_SUBSCRIPTION
    return _STATUS_REASONS.get(subscription.status, REASON_NO_SUBSCRIPTION)

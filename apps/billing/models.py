from django.db import models

from helpers.models import DateModel


class Subscription(DateModel):
    """
    One row per tenant: the tenant's Stripe subscription state, or an
    exemption from billing. A tenant with no row has no access once
    enforcement is on.

    `status` mirrors Stripe's subscription status and is blank for
    exempt rows. Rows are never deleted, because `billing_email` is how
    a repeat trial for the same email is recognised.
    """

    tenant = models.OneToOneField(
        'tenants.Tenant',
        on_delete=models.PROTECT,
        related_name='subscription',
    )
    billing_exempt = models.BooleanField(default=False)
    billing_email = models.EmailField(max_length=255, blank=True, default='', db_index=True)

    stripe_customer_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    stripe_subscription_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    stripe_checkout_session_id = models.CharField(max_length=128, unique=True, null=True, blank=True)

    status = models.CharField(max_length=32, blank=True, default='')
    trial_end = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    canceled_at = models.DateTimeField(null=True, blank=True)

    card_fingerprint = models.CharField(max_length=64, blank=True, default='', db_index=True)
    welcome_email_sent_at = models.DateTimeField(null=True, blank=True)

    needs_review = models.BooleanField(default=False)
    review_note = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'billing_subscriptions'
        verbose_name = 'subscription'
        verbose_name_plural = 'subscriptions'

    def __str__(self):
        if self.billing_exempt:
            return f"Subscription(tenant={self.tenant_id}, exempt)"
        return f"Subscription(tenant={self.tenant_id}, status={self.status or '-'})"

    @property
    def has_access(self):
        from apps.billing.services.access import has_access
        return has_access(self)


class StripeEvent(models.Model):
    """
    A Stripe webhook event that has been received. The unique `event_id`
    is what makes redelivery harmless.
    """

    event_id = models.CharField(max_length=64, unique=True)
    event_type = models.CharField(max_length=100)
    stripe_created = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'billing_stripe_events'
        verbose_name = 'Stripe event'
        verbose_name_plural = 'Stripe events'

    def __str__(self):
        return f"{self.event_type} {self.event_id}"

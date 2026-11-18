from django.contrib import admin

from apps.billing.models import StripeEvent, Subscription


class SubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'tenant', 'billing_exempt', 'status', 'billing_email',
        'current_period_end', 'needs_review',
    )
    list_filter = ('billing_exempt', 'status', 'needs_review')
    search_fields = ('billing_email', 'stripe_customer_id', 'stripe_subscription_id')
    raw_id_fields = ('tenant',)

    # Stripe-owned state is changed by webhooks, not by hand. The only
    # fields staff edit are the exemption and the review flags.
    _always_readonly = (
        'billing_email', 'stripe_customer_id', 'stripe_subscription_id',
        'stripe_checkout_session_id', 'status', 'trial_end',
        'current_period_end', 'cancel_at_period_end', 'canceled_at',
        'card_fingerprint', 'welcome_email_sent_at', 'created_at', 'updated_at',
    )

    def get_readonly_fields(self, request, obj=None):
        if obj is not None:
            return self._always_readonly + ('tenant',)
        return self._always_readonly


class StripeEventAdmin(admin.ModelAdmin):
    list_display = ('event_id', 'event_type', 'stripe_created', 'received_at', 'processed_at')
    list_filter = ('event_type',)
    search_fields = ('event_id',)

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(Subscription, SubscriptionAdmin)
admin.site.register(StripeEvent, StripeEventAdmin)

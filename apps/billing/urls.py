from django.urls import path

from apps.billing.api.webhook import StripeWebhookView

app_name = 'billing'

urlpatterns = [
    # Public; authenticated by the Stripe-Signature header.
    path('webhook/', StripeWebhookView.as_view(), name='webhook'),
]

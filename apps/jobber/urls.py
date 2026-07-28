from django.urls import path

from apps.jobber.api.oauth import (
    JobberCallbackView,
    JobberConnectView,
    JobberDisconnectView,
    JobberStatusView,
)
from apps.jobber.api.webhook import JobberWebhookView

app_name = 'jobber'

urlpatterns = [
    # ── OAuth flow ──────────────────────────────────────────────────────────────
    path('connect/', JobberConnectView.as_view(), name='connect'),
    path('callback/', JobberCallbackView.as_view(), name='callback'),

    # ── Connection management ───────────────────────────────────────────────────
    path('status/', JobberStatusView.as_view(), name='status'),
    path('disconnect/', JobberDisconnectView.as_view(), name='disconnect'),

    # ── Webhooks (public — authenticated via HMAC-SHA256 signature) ────────────
    # Register this URL in the Jobber Developer Center for the APP_DISCONNECT
    # topic (and any others added later):
    #   https://api.techtrackpro.com/v1/jobber/webhook/
    path('webhook/', JobberWebhookView.as_view(), name='webhook'),
]

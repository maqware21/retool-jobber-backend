from django.urls import path

from apps.jobber.api.oauth import (
    JobberCallbackView,
    JobberConnectView,
    JobberDisconnectView,
    JobberStatusView,
)

app_name = 'jobber'

urlpatterns = [
    # ── OAuth flow ──────────────────────────────────────────────────────────────
    path('connect/', JobberConnectView.as_view(), name='connect'),
    path('callback/', JobberCallbackView.as_view(), name='callback'),

    # ── Connection management ─────────────────────────────────────────────────────
    path('status/', JobberStatusView.as_view(), name='status'),
    path('disconnect/', JobberDisconnectView.as_view(), name='disconnect'),
]

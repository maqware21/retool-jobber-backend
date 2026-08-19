from django.urls import path

from apps.jobber.api.accounts import JobberAccountsView
from apps.jobber.api.electricians_summary import JobberElectriciansSummaryView
from apps.jobber.api.employees import JobberEmployeesView
from apps.jobber.api.invoices import JobberInvoicesView
from apps.jobber.api.jobs import JobberJobsView
from apps.jobber.api.oauth import (
    JobberCallbackView,
    JobberConnectView,
    JobberDisconnectView,
    JobberStatusView,
)
from apps.jobber.api.technician_stats import JobberTechnicianStatsView
from apps.jobber.api.webhook import JobberWebhookView

app_name = 'jobber'

urlpatterns = [
    # ── OAuth flow ──────────────────────────────────────────────────────────────
    path('connect/', JobberConnectView.as_view(), name='connect'),
    path('callback/', JobberCallbackView.as_view(), name='callback'),

    # ── Connection management ───────────────────────────────────────────────────
    path('status/', JobberStatusView.as_view(), name='status'),
    path('disconnect/', JobberDisconnectView.as_view(), name='disconnect'),

    # ── Data (live proxy — no local caching) ────────────────────────────────────
    path('jobs/', JobberJobsView.as_view(), name='jobs'),
    path('invoices/', JobberInvoicesView.as_view(), name='invoices'),
    path('accounts/', JobberAccountsView.as_view(), name='accounts'),
    path('employees/', JobberEmployeesView.as_view(), name='employees'),
    path('electricians-summary/', JobberElectriciansSummaryView.as_view(), name='electricians-summary'),
    path('technician-stats/', JobberTechnicianStatsView.as_view(), name='technician-stats'),

    # ── Webhooks (public — authenticated via HMAC-SHA256 signature) ────────────
    # Register this URL in the Jobber Developer Center for the APP_DISCONNECT
    # topic (and any others added later):
    #   https://api.techtrackpro.com/v1/jobber/webhook/
    path('webhook/', JobberWebhookView.as_view(), name='webhook'),
]

import logging

from dateutil.relativedelta import relativedelta
from django.db.models import Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.views import APIView

from apps.jobber.models import JobberAccount, JobberInvoice
from apps.jobber.services.sync import ensure_fresh
from helpers.api_exception import validator_errors
from helpers.messages import MESSAGES
from helpers.user_permissions import CustomerPermission
from helpers.utils import api_response_parser

logger = logging.getLogger(__name__)

# How far back "Total Revenue" looks. A real calendar-month count, not an
# approximate day count — see _local_electricians_summary_response() below
# for why that distinction matters here.
PERIOD_MONTHS = 6


def _local_electricians_summary_response(user):
    """
    Local-table source for the Electricians panel's KPI tiles, grown one
    field at a time as each of the 4 mock tiles (Total Revenue, Jobs
    Completed, Avg Job Duration, Top Earner) goes real — per TL decision,
    replaced in place with no visual distinction from the ones still mock.
    Currently just `total_revenue` (the Total Revenue tile).

    Calls ensure_fresh(require_complete=True) first — same reasoning
    already established for Accounts/Employees: a Sum computed over a
    partially-synced Invoice set is a WRONG number, not just a stale one,
    so a PARTIAL sync gets one retry (via require_complete) before this
    proceeds regardless — see ensure_fresh()'s own docstring for exactly
    what that retry does and doesn't guarantee.

    period_start uses dateutil.relativedelta(months=PERIOD_MONTHS), not
    timedelta(days=180) — a real calendar-month subtraction (e.g. Feb 12 ->
    Aug 12) rather than an approximate 180-day window, which drifts by a
    few days depending on which months are actually in range.
    """
    tenant_id = user.tenant_id
    if not tenant_id:
        return {'connected': False, 'total_revenue': None, 'period_months': PERIOD_MONTHS, 'last_synced_at': None}

    account = JobberAccount.objects.filter(tenant_id=tenant_id, is_active=True).first()
    if account is None:
        return {'connected': False, 'total_revenue': None, 'period_months': PERIOD_MONTHS, 'last_synced_at': None}

    fresh = ensure_fresh(account.tenant, entities=['invoices'], require_complete=True)

    period_start = timezone.now() - relativedelta(months=PERIOD_MONTHS)
    total = JobberInvoice.objects.filter(
        tenant_id=tenant_id,
        is_active=True,
        status_display='Paid',
        issued_date__gte=period_start,
    ).aggregate(total=Sum('amount'))['total']

    data = {
        'connected': True,
        # Genuinely zero (no Paid invoices in the period) is a real,
        # different answer from "not connected" (total_revenue: None
        # above) — Sum() returns None for an empty queryset, so that's
        # coalesced to 0.0 here, not left as None.
        'total_revenue': float(total) if total is not None else 0.0,
        'period_months': PERIOD_MONTHS,
        'last_synced_at': fresh['last_synced_at'].isoformat() if fresh['last_synced_at'] else None,
    }
    if fresh['sync_warning']:
        data['sync_warning'] = fresh['sync_warning']
    return data


class JobberElectriciansSummaryView(APIView):
    """
    GET /v1/jobber/electricians-summary/
    Backs the Electricians panel's KPI tiles as they go real one at a time
    (see PROJECT_CONTEXT.md for exactly which ones are real vs. still
    mock). Reads local tables only via ensure_fresh() — never calls Jobber
    directly.
    """
    permission_classes = [CustomerPermission]

    def get(self, request):
        data = {'connected': False, 'total_revenue': None, 'period_months': PERIOD_MONTHS, 'last_synced_at': None}
        try:
            data = _local_electricians_summary_response(request.user)
            return api_response_parser(
                data=data,
                message=MESSAGES['SUCCESS'],
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)

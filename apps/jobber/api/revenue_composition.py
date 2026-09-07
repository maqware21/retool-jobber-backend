import logging
from datetime import datetime, time

from dateutil.relativedelta import relativedelta
from django.db.models import Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.views import APIView

from apps.goals.utils import current_month
from apps.jobber.models import JobberAccount, JobberJob
from apps.jobber.services.sync import ensure_fresh
from helpers.api_exception import validator_errors
from helpers.messages import MESSAGES
from helpers.user_permissions import CustomerPermission
from helpers.utils import api_response_parser

logger = logging.getLogger(__name__)

# Same discrete-calendar-month window as Monthly Revenue by Technician
# (monthly_revenue.py) -- a SEPARATE constant, not shared, since the two
# happen to agree today but are conceptually independent (see this
# module's own docstring for why this is a dedicated endpoint, not a
# field bolted onto that one).
MONTHS_BACK = 6

_NOT_CONNECTED_DATA = {
    'connected': False,
    'months': [],
    'rows': [],
}


def _company_revenue_expenses_for_month(tenant_id, month_start):
    """
    Real, company-wide revenue and expenses for exactly ONE calendar
    month -- approved (2026-09-07) revenue_composition_expense_profit_
    proposal.md. NO per-technician split at all (this is a company-wide
    chart, not attributed to anyone) -- a plain Sum() over the same
    archived + completed_at-windowed population every other revenue-
    driven feature in this project uses (Top Earner, Profit Margin,
    Avg Job Value, Monthly Revenue by Technician), NOT the separate
    Total Revenue tile's Paid-invoices-only population. Deliberately a
    SEPARATE query from _revenue_by_technician_for_month() (monthly_
    revenue.py) rather than reusing/refactoring it -- that function's own
    per-technician attribution machinery (calculate_top_earner()) isn't
    needed here at all, and this avoids any risk of touching its
    already-shipped, working behavior.

    expenses = Sum(line_item_cost) -- Jobber's own real jobCosting.
    lineItemCost, confirmed genuine and non-circular (see
    PROJECT_CONTEXT.md's 2026-09-07 update) -- NOT expenseCost, which
    stays confirmed dead (always 0 in this account) and isn't synced.

    Returns (revenue, expenses) as plain floats. A month with zero
    archived jobs returns (0.0, 0.0) -- a real, honest zero (no jobs
    completed that month), not "no data" -- same convention as the
    company-wide Jobs Completed/Total Revenue tiles elsewhere, which
    also report a real 0 rather than a null for an inactive month.
    line_item_cost itself can be individually null on a job that hasn't
    been re-synced since this field was added -- Sum() already skips
    nulls, so a job with a real total but not-yet-synced line_item_cost
    simply contributes 0 to expenses for that one job, not the whole
    month; this self-heals on that job's next regular sync.
    """
    range_start = timezone.make_aware(datetime.combine(month_start, time.min))
    range_end = timezone.make_aware(datetime.combine(month_start + relativedelta(months=1), time.min))

    month_jobs = JobberJob.objects.filter(
        tenant_id=tenant_id, is_active=True, job_status='archived',
        completed_at__gte=range_start, completed_at__lt=range_end,
    )
    totals = month_jobs.aggregate(revenue=Sum('total'), expenses=Sum('line_item_cost'))
    return (
        float(totals['revenue']) if totals['revenue'] is not None else 0.0,
        float(totals['expenses']) if totals['expenses'] is not None else 0.0,
    )


def _local_revenue_composition_response(tenant):
    """
    Real company-wide revenue/expenses for each of the last MONTHS_BACK
    calendar months, backing the Revenue Health panel's "Revenue
    Composition by month" chart. A dedicated endpoint, not a field on
    monthly-revenue/electricians-summary -- same reasoning already
    established for Monthly Revenue by Technician's own endpoint split
    (a differently-shaped, differently-scoped response; bundling it in
    would force an unrelated consumer to pay for computing data it
    doesn't need).

    Flat {month, revenue, expenses} rows (one per month), matching this
    project's own "one fact per row" convention -- the frontend's
    existing month-bucketed rendering (originally built for the mock
    revenueData) already expects this exact shape, just real now.
    """
    if tenant is None:
        return dict(_NOT_CONNECTED_DATA)
    tenant_id = tenant.id

    account = JobberAccount.objects.filter(tenant_id=tenant_id, is_active=True).first()
    if account is None:
        return dict(_NOT_CONNECTED_DATA)

    # require_complete=True -- same reasoning as every other aggregate in
    # this project: a company-wide revenue/expense sum computed over a
    # partially-synced entity set is a WRONG number, not just a stale one.
    fresh = ensure_fresh(account.tenant, entities=['jobs', 'visits', 'timesheet_entries'], require_complete=True)

    this_month = current_month()
    month_starts = [this_month - relativedelta(months=i) for i in range(MONTHS_BACK - 1, -1, -1)]

    months = []
    rows = []
    for month_start in month_starts:
        month_label = month_start.strftime('%Y-%m')
        months.append(month_label)
        revenue, expenses = _company_revenue_expenses_for_month(tenant_id, month_start)
        rows.append({'month': month_label, 'revenue': round(revenue, 2), 'expenses': round(expenses, 2)})

    data = {
        'connected': True,
        'months': months,
        'rows': rows,
        'last_synced_at': fresh['last_synced_at'].isoformat() if fresh['last_synced_at'] else None,
    }
    if fresh['sync_warning']:
        data['sync_warning'] = fresh['sync_warning']
    return data


class JobberRevenueCompositionView(APIView):
    """
    GET /v1/jobber/revenue-composition/
    Real company-wide revenue/expenses for the last 6 calendar months,
    backing the Revenue Health panel's "Revenue Composition by month"
    chart. Reads local tables only via ensure_fresh() -- never calls
    Jobber directly.
    """
    permission_classes = [CustomerPermission]

    def get(self, request):
        data = dict(_NOT_CONNECTED_DATA)
        try:
            data = _local_revenue_composition_response(request.user.tenant)
            return api_response_parser(
                data=data,
                message=MESSAGES['SUCCESS'],
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)

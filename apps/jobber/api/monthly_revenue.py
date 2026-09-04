import logging
from datetime import datetime, time

from dateutil.relativedelta import relativedelta
from django.utils import timezone
from rest_framework import status
from rest_framework.views import APIView

from apps.goals.utils import current_month
from apps.jobber.api.electricians_summary import calculate_top_earner
from apps.jobber.models import JobberAccount, JobberJob, JobberUser
from apps.jobber.services.sync import ensure_fresh
from helpers.api_exception import validator_errors
from helpers.messages import MESSAGES
from helpers.user_permissions import CustomerPermission
from helpers.utils import api_response_parser

logger = logging.getLogger(__name__)

# How many calendar months this chart covers, INCLUDING the current
# (partial) month -- e.g. computed on 2026-08-24, this returns the 6
# discrete calendar-month buckets 2026-03 through 2026-08.
MONTHS_BACK = 6

_NOT_CONNECTED_DATA = {
    'connected': False,
    'months': [],
    'rows': [],
}


def _revenue_by_technician_for_month(tenant_id, month_start):
    """
    Real per-technician revenue for exactly ONE calendar month --
    extracted (2026-09-04, approved last_month_goal_alert_proposal.md)
    from _local_monthly_revenue_response()'s own per-month loop body
    below, so the "last month's goal" alert rule (evaluate.py) can reuse
    this exact calculation for one isolated month without running the
    whole MONTHS_BACK-month loop just to get one month's numbers. The
    chart below now calls this function once per month instead of
    inlining the range/filter/calculate_top_earner steps directly --
    same output, byte-for-byte, confirmed unchanged against real data
    after this extraction (see last_month_goal_alert_proposal.md's build
    report).

    Same Job.total-attributed calculate_top_earner() population every
    other revenue-driven feature in this project uses (Top Earner,
    Profit Margin, current/annual goal progress) -- NOT paid-invoice-
    based.

    month_start must already be the 1st of the target month (a date, not
    a datetime) -- same convention as TeamGoal/TechnicianGoal.month and
    this module's own month_starts list below.

    Returns {user_id: revenue} exactly as calculate_top_earner() returns
    it (Decimal/float, unrounded) -- a job-less month or a technician
    with zero jobs that month is simply absent from the dict; callers
    decide what "absent" means for their own purposes (this module's
    chart below treats it as a real, displayable 0; evaluate.py's
    last_month_goal_pct branch does the same -- zero real revenue is a
    computable fact, not "no data").
    """
    range_start = timezone.make_aware(datetime.combine(month_start, time.min))
    range_end = timezone.make_aware(datetime.combine(month_start + relativedelta(months=1), time.min))

    month_jobs = JobberJob.objects.filter(
        tenant_id=tenant_id, is_active=True, job_status='archived',
        completed_at__gte=range_start, completed_at__lt=range_end,
    )
    return calculate_top_earner(month_jobs)


def _local_monthly_revenue_response(tenant):
    """
    Real per-technician revenue for each of the last MONTHS_BACK calendar
    months, backing the Electricians panel's "Monthly Revenue by
    Technician" chart.

    Flat {month, user_id, name, revenue} rows, NOT a pivoted/wide
    per-technician-series shape -- matches this project's own "one fact
    per row" convention elsewhere (TriggeredAlert, the Goals roster
    rows) rather than pre-deciding which technicians are "columns"
    server-side. The frontend's existing pivot-into-Recharts-rows
    transform (originally built for the old mock combinedMonthly data)
    turns this same shape into chart-ready rows, reused unchanged.

    Reuses calculate_top_earner(month_jobs) UNCHANGED -- called once per
    month instead of once across a whole window, exactly as already
    proven for the KPI tiles/Top Earner. Nothing here re-derives that
    attribution logic. This does mean MONTHS_BACK calls instead of one --
    genuinely trivial at this project's current real data volume (~15
    jobs total spread across up to 6 months), flagged explicitly rather
    than silently doing 6x the work of every other calculate_top_earner()
    caller without saying so.

    Month boundaries are calendar-month-aligned (1st of each month, via
    current_month() + relativedelta) -- CONFIRMED DIRECTLY (2026-08-24),
    not assumed, that this does NOT land on the same boundary as
    DEFAULT_RANGE, the frontend's rolling window for the Job Log table on
    this same page: on 2026-08-24, DEFAULT_RANGE spans 2026-02-24 to
    2026-08-24 as one continuous window (today, minus 6 calendar months,
    same day-of-month), while this endpoint's buckets are the 6 DISCRETE
    calendar months 2026-03 through 2026-08 -- e.g. late February is
    inside DEFAULT_RANGE's window but not inside any month bucket this
    endpoint returns. This is an accepted, structural difference, not a
    bug to reconcile: a bar/line chart needs discrete whole-month
    buckets; a job list needs a continuous cutoff. See
    PROJECT_CONTEXT.md and monthly_revenue_chart_proposal.md.
    """
    if tenant is None:
        return dict(_NOT_CONNECTED_DATA)
    tenant_id = tenant.id

    account = JobberAccount.objects.filter(tenant_id=tenant_id, is_active=True).first()
    if account is None:
        return dict(_NOT_CONNECTED_DATA)

    # require_complete=True -- same reasoning as electricians_summary.py/
    # technician_stats.py: a monthly revenue aggregate computed over a
    # partially-synced entity set is a WRONG number, not just a stale one.
    fresh = ensure_fresh(account.tenant, entities=['jobs', 'visits', 'timesheet_entries'], require_complete=True)

    this_month = current_month()
    # Oldest-to-newest, e.g. [2026-03-01, 2026-04-01, ..., 2026-08-01].
    month_starts = [this_month - relativedelta(months=i) for i in range(MONTHS_BACK - 1, -1, -1)]

    # Seeds every real, active technician for every month (same "seed
    # every technician, zero out rather than hide" convention already
    # established for the Employees roster and the Goals technician
    # list) -- a technician with zero revenue in a given month still gets
    # an explicit 0 row, not a silently-missing one.
    users = list(JobberUser.objects.filter(tenant_id=tenant_id, is_active=True).order_by('name'))

    months = []
    rows = []
    for month_start in month_starts:
        month_label = month_start.strftime('%Y-%m')
        months.append(month_label)

        revenue_totals = _revenue_by_technician_for_month(tenant_id, month_start)

        for user in users:
            rows.append({
                'month': month_label,
                'user_id': user.id,
                'name': user.name,
                'revenue': round(float(revenue_totals.get(user.id, 0.0)), 2),
            })

    data = {
        'connected': True,
        'months': months,
        'rows': rows,
        'last_synced_at': fresh['last_synced_at'].isoformat() if fresh['last_synced_at'] else None,
    }
    if fresh['sync_warning']:
        data['sync_warning'] = fresh['sync_warning']
    return data


class JobberMonthlyRevenueView(APIView):
    """
    GET /v1/jobber/monthly-revenue/
    Real per-technician revenue for the last 6 calendar months, backing
    the Electricians panel's "Monthly Revenue by Technician" chart.
    Reads local tables only via ensure_fresh() -- never calls Jobber
    directly. A dedicated endpoint, not a field on electricians-summary
    -- see monthly_revenue_chart_proposal.md for why (a meaningfully
    larger/differently-shaped response than that endpoint's other
    near-scalar fields; bundling it in would force every KPI-tile
    consumer to pay for computing it too).
    """
    permission_classes = [CustomerPermission]

    def get(self, request):
        data = dict(_NOT_CONNECTED_DATA)
        try:
            data = _local_monthly_revenue_response(request.user.tenant)
            return api_response_parser(
                data=data,
                message=MESSAGES['SUCCESS'],
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)

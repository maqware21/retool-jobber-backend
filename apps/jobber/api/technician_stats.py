import logging
from datetime import datetime, time

from dateutil.relativedelta import relativedelta
from django.utils import timezone
from rest_framework import status
from rest_framework.views import APIView

from apps.goals.models import TechnicianGoal
from apps.goals.utils import current_month
from apps.jobber.api.electricians_summary import (
    PERIOD_MONTHS,
    _gather_job_assignees,
    calculate_job_duration_by_user,
    calculate_top_earner,
)
from apps.jobber.models import JobberAccount, JobberJob, JobberUser
from apps.jobber.services.sync import ensure_fresh
from helpers.api_exception import validator_errors
from helpers.messages import MESSAGES
from helpers.user_permissions import CustomerPermission
from helpers.utils import api_response_parser

logger = logging.getLogger(__name__)


_NOT_CONNECTED_DATA = {
    'connected': False,
    'period_months': PERIOD_MONTHS,
    'last_synced_at': None,
    'technicians': [],
}


def _accumulate_technician_job_stats(archived_jobs):
    """
    Per-technician job counts and tracked hours across `archived_jobs` --
    the SAME archived + completed_at-windowed population Top Earner and
    the company-wide Jobs Completed / Avg Job Duration tiles already use.
    Built entirely from already-verified primitives
    (_gather_job_assignees(), calculate_job_duration_by_user()) -- neither
    "who's assigned" nor "how long they tracked" is re-derived here.

    Returns {user_id: {'jobs_completed': int, 'total_seconds': float,
    'tracked_job_count': int}}.

    total_seconds sums this user's tracked seconds across EVERY job they
    were assigned to in the window (0 for a job they tracked no time on --
    adding 0 to a sum is a no-op, so no special-casing is needed for it to
    be the correct denominator for revenue_per_hour).

    tracked_job_count only counts jobs where that SAME user's tracked
    seconds were > 0 -- this is the denominator avg_job_duration_seconds
    uses below, so a job with zero tracked time for this user is excluded
    from THAT specific average, same "no data != 0" convention already
    used for the company-wide Avg Job Duration tile. It does not affect
    total_seconds (which already naturally excludes it via the +0 no-op).
    """
    stats = {}
    for job in archived_jobs:
        assignees = _gather_job_assignees(job)
        if not assignees:
            continue
        hours_by_user = calculate_job_duration_by_user(job)
        for user_id in assignees:
            entry = stats.setdefault(
                user_id, {'jobs_completed': 0, 'total_seconds': 0.0, 'tracked_job_count': 0},
            )
            entry['jobs_completed'] += 1
            seconds = hours_by_user.get(user_id, 0)
            entry['total_seconds'] += seconds
            if seconds > 0:
                entry['tracked_job_count'] += 1
    return stats


def _accumulate_completion_counts(tenant_id, period_start):
    """
    Completion % needs a DIFFERENT population than revenue/jobs_completed/
    avg_job_duration_seconds above: ALL jobs assigned in the window,
    regardless of status -- dividing archived-by-archived would be
    trivially 100%. Windowed by jobber_created_at (job creation), not
    completed_at, since a job that's never been completed has no
    completed_at at all.

    Per TL instruction: cancelled jobs should be excluded from both sides
    of this ratio, since a cancelled job was never really "supposed to
    complete." NOT IMPLEMENTED AS SPECIFIED -- confirmed directly against
    Jobber's own schema (JobStatusTypeEnum, the enum backing Job.jobStatus
    exactly, not a different/looser one): its only values are
    requires_invoicing, archived, late, today, upcoming, action_required,
    on_hold, unscheduled, active, expiring_within_30_days. There is no
    "cancelled" value anywhere in it, and JobberJob.job_status stores this
    raw enum value directly (see sync_jobs()) -- so there is no real value
    to filter out. Flagged plainly, not silently worked around by guessing
    which of the real statuses might mean "cancelled" (e.g. on_hold is NOT
    the same thing and would be a wrong guess).

    Returns (assigned_counts, archived_counts), both {user_id: count}.
    """
    jobs = JobberJob.objects.filter(
        tenant_id=tenant_id, is_active=True, jobber_created_at__gte=period_start,
    )
    assigned_counts = {}
    archived_counts = {}
    for job in jobs:
        assignees = _gather_job_assignees(job)
        for user_id in assignees:
            assigned_counts[user_id] = assigned_counts.get(user_id, 0) + 1
            if job.job_status == 'archived':
                archived_counts[user_id] = archived_counts.get(user_id, 0) + 1
    return assigned_counts, archived_counts


def _local_technician_stats_response(user):
    """
    Local-table source for the Electricians panel's per-technician card +
    drawer fields that are now real: revenue, jobs_completed,
    revenue_per_hour, avg_job_duration_seconds, completion_percentage,
    team_revenue_share_percentage, and current-month goal progress.

    Explicitly OUT of scope here (per TL): profit margin (blocked on a
    wage-rate decision), "on pace"/projected year-end (pending TL), job
    history (separate endpoint, next round), monthly revenue trend chart
    (separate round), and all 4 threshold-based alerts (deferred together
    to a future Alerts module -- each has 2-3 disagreeing mock thresholds
    needing one reconciled decision, not four separate ones).
    """
    tenant_id = user.tenant_id
    if not tenant_id:
        return dict(_NOT_CONNECTED_DATA)

    account = JobberAccount.objects.filter(tenant_id=tenant_id, is_active=True).first()
    if account is None:
        return dict(_NOT_CONNECTED_DATA)

    fresh = ensure_fresh(account.tenant, entities=['jobs', 'visits', 'timesheet_entries'], require_complete=True)

    period_start = timezone.now() - relativedelta(months=PERIOD_MONTHS)

    # Same archived + completed_at-windowed population as Top Earner and
    # the company-wide Jobs Completed / Avg Job Duration tiles.
    archived_jobs = list(JobberJob.objects.filter(
        tenant_id=tenant_id, is_active=True, job_status='archived', completed_at__gte=period_start,
    ))

    # Revenue per tech -- REUSED directly from calculate_top_earner(),
    # which already computes exactly this internally and previously threw
    # away everything except the single winner. Not re-derived.
    revenue_totals = calculate_top_earner(archived_jobs)
    team_revenue_total = sum(revenue_totals.values())

    job_stats = _accumulate_technician_job_stats(archived_jobs)
    assigned_counts, archived_counts = _accumulate_completion_counts(tenant_id, period_start)

    # Current-month revenue, for goal progress -- calculate_top_earner()
    # reused a SECOND time, over a narrower (current calendar month only)
    # job queryset instead of the 6-month window above. Same function,
    # different input -- not a new calculation.
    #
    # month_date comes from apps.goals.utils.current_month() -- the SAME
    # function the Goals endpoints themselves use to decide "what month is
    # it" -- rather than this file separately deriving its own notion of
    # "today," which could silently disagree with Goals' if the two were
    # computed differently (current_month() uses date.today(), i.e. the
    # server process's system clock; this project's TIME_ZONE is UTC, so
    # they agree as long as the server's OS clock is also UTC -- standard
    # for this deployment, but worth naming as the assumption it is).
    month_date = current_month()
    current_month_start = timezone.make_aware(datetime.combine(month_date, time.min))
    current_month_jobs = list(JobberJob.objects.filter(
        tenant_id=tenant_id, is_active=True, job_status='archived', completed_at__gte=current_month_start,
    ))
    current_month_revenue_totals = calculate_top_earner(current_month_jobs)

    # Reuses the SAME TechnicianGoal.fetch() the Goals endpoints
    # themselves use (tenant_id= + month= only, no user_id -> a queryset,
    # the roster-shape call) -- not a new goal lookup.
    goals_by_user_id = {
        goal.user_id: goal.goal_amount
        for goal in TechnicianGoal.fetch(tenant_id=tenant_id, month=month_date)
    }

    # Seeds every real, active technician first (same convention already
    # established for the Employees roster and the Goals technician list)
    # so a technician with zero jobs this window still appears, zeroed
    # out rather than silently missing.
    users = JobberUser.objects.filter(tenant_id=tenant_id, is_active=True).order_by('name')

    technicians = []
    for tech in users:
        revenue = revenue_totals.get(tech.id, 0.0)

        stats = job_stats.get(tech.id)
        jobs_completed = stats['jobs_completed'] if stats else 0
        total_seconds = stats['total_seconds'] if stats else 0
        tracked_job_count = stats['tracked_job_count'] if stats else 0

        revenue_per_hour = (revenue / (total_seconds / 3600)) if total_seconds > 0 else None
        avg_job_duration_seconds = (
            round(total_seconds / tracked_job_count) if tracked_job_count > 0 else None
        )

        assigned = assigned_counts.get(tech.id, 0)
        archived_count = archived_counts.get(tech.id, 0)
        completion_percentage = (
            round((archived_count / assigned) * 100, 1) if assigned > 0 else None
        )

        team_revenue_share_percentage = (
            round((revenue / team_revenue_total) * 100, 1) if team_revenue_total > 0 else None
        )

        goal_amount = goals_by_user_id.get(tech.id)
        current_month_revenue = current_month_revenue_totals.get(tech.id, 0.0)
        progress_percentage = (
            round((current_month_revenue / float(goal_amount)) * 100, 1)
            if goal_amount and float(goal_amount) > 0
            else None
        )

        technicians.append({
            'user_id': tech.id,
            'name': tech.name,
            # Already synced (JobberUser.phone) -- genuinely null for a
            # real technician with no phone on file in Jobber itself, not
            # "not yet synced". Passed through as-is, never coalesced.
            'phone': tech.phone,
            # From User.customFields ("Expertise"/"Experience" Team custom
            # fields, confirmed real for this account) -- null for a
            # tenant that hasn't configured them, never a crash.
            'expertise': tech.expertise,
            'experience_years': tech.experience_years,
            # Genuinely zero (no revenue this window) is a real, different
            # answer from "no data" -- always a number here, never null.
            'revenue': round(float(revenue), 2),
            'jobs_completed': jobs_completed,
            'revenue_per_hour': round(revenue_per_hour, 2) if revenue_per_hour is not None else None,
            'avg_job_duration_seconds': avg_job_duration_seconds,
            'completion_percentage': completion_percentage,
            'team_revenue_share_percentage': team_revenue_share_percentage,
            'goal_progress': {
                'goal_amount': float(goal_amount) if goal_amount is not None else None,
                'current_month_revenue': round(float(current_month_revenue), 2),
                'progress_percentage': progress_percentage,
            },
        })

    data = {
        'connected': True,
        'period_months': PERIOD_MONTHS,
        'last_synced_at': fresh['last_synced_at'].isoformat() if fresh['last_synced_at'] else None,
        'technicians': technicians,
    }
    if fresh['sync_warning']:
        data['sync_warning'] = fresh['sync_warning']
    return data


class JobberTechnicianStatsView(APIView):
    """
    GET /v1/jobber/technician-stats/
    Per-technician real stats backing the Electricians panel's card +
    drawer fields (see PROJECT_CONTEXT.md for exactly which fields this
    covers and which remain mock/deferred). Reads local tables only via
    ensure_fresh() -- never calls Jobber directly.
    """
    permission_classes = [CustomerPermission]

    def get(self, request):
        data = dict(_NOT_CONNECTED_DATA)
        try:
            data = _local_technician_stats_response(request.user)
            return api_response_parser(
                data=data,
                message=MESSAGES['SUCCESS'],
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)

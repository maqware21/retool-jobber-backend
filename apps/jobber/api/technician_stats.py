import logging
from datetime import datetime, time
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.utils import timezone
from rest_framework import status
from rest_framework.views import APIView

from apps.goals.models import TechnicianAnnualGoal, TechnicianGoal
from apps.goals.utils import current_month, current_year
from apps.jobber.api.electricians_summary import (
    PERIOD_MONTHS,
    _gather_job_assignees,
    calculate_job_duration_by_user,
    calculate_technician_labor_cost,
    calculate_top_earner,
    split_job_revenue_among_assignees,
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


def _accumulate_technician_callback_stats(archived_jobs):
    """
    Per-technician callback counts and dollar attribution across
    `archived_jobs` — the SAME archived + completed_at-windowed population
    _accumulate_technician_job_stats() above uses (confirmed 2026-08-31,
    Part B verification: Job.completed_at tracks the LATEST closure, not
    the original one — a job's real completedAt landed within 4 seconds of
    its real callback visit's own completedAt, over an hour after the
    original visit's completion — so a job that was reopened recently
    stays inside this rolling window even if its original work is older).

    Built entirely from already-proven primitives, nothing re-derived:
      - JobberVisit.is_callback — detect_and_freeze_callbacks()'s own
        frozen finding (see that function's docstring — this is the
        SOLE place the "what counts as a callback" definition lives).
      - JobberVisit.assigned_users — the SAME multi-assignee field Top
        Earner already uses (assigned_users, not the single-assignee
        assigned_user) — confirmed convention (2026-08-31), not
        re-decided here.
      - calculate_job_duration_by_user(job, created_at_or_after=
        job.first_archived_at) — unchanged, called with the SAME
        callback-only split detect_and_freeze_callbacks() itself uses.
      - split_job_revenue_among_assignees() — unchanged, reused directly
        for the dollar split, same as calculate_job_revenue_shares()
        above uses it for whole-job revenue.

    Returns {user_id: {'callback_visits_done': int,
    'callback_dollars_lost': float, 'has_unknown_callback_cost': bool}}.

    callback_visits_done: FULL credit to every real assignee of a
    callback visit — the same "no-split" rule jobs_completed itself uses
    (confirmed convention, 2026-08-31), not a proportional count.

    callback_dollars_lost: only sums a callback's dollar SHARE when
    job.callback_bled_amount is a real (non-null) number. A null
    callback_bled_amount means "unknown cost" — Job #3's own real,
    confirmed case (a genuine callback with zero logged hours on the
    callback visit) — and must NEVER be silently coalesced into a clean
    $0 total. Split proportionally via split_job_revenue_among_assignees(),
    weighted by the CALLBACK-VISIT-ONLY hours (not the whole job's hours,
    since this dollar figure is specifically about the callback's own
    cost, not the original work).

    has_unknown_callback_cost: True for any technician with at least one
    REAL callback (is_callback=True, correctly attributed to them via
    assigned_users) whose job.callback_bled_amount is null. A separate,
    explicit flag — never dropped, never merged into the dollar total —
    so a technician's $0 in the response is never ambiguous between
    "confirmed zero lost" and "we don't actually know."
    """
    stats = {}
    for job in archived_jobs:
        callback_visits = list(job.visits.filter(is_callback=True, is_active=True))
        if not callback_visits:
            continue

        # Same split boundary detect_and_freeze_callbacks() itself uses —
        # computed once per job, reused for every callback visit on it
        # (there's at most one under the current one-shot detection design,
        # but this doesn't assume that).
        callback_hours_by_user = calculate_job_duration_by_user(job, created_at_or_after=job.first_archived_at)
        has_known_amount = job.callback_bled_amount is not None

        for visit in callback_visits:
            assignees = list(visit.assigned_users.all())
            if not assignees:
                continue

            dollar_shares = {}
            if has_known_amount:
                hours_by_user = {u.id: callback_hours_by_user.get(u.id, 0) for u in assignees}
                dollar_shares = split_job_revenue_among_assignees(float(job.callback_bled_amount), hours_by_user)

            for user in assignees:
                entry = stats.setdefault(
                    user.id,
                    {'callback_visits_done': 0, 'callback_dollars_lost': 0.0, 'has_unknown_callback_cost': False},
                )
                entry['callback_visits_done'] += 1
                if has_known_amount:
                    entry['callback_dollars_lost'] += dollar_shares.get(user.id, 0.0)
                else:
                    entry['has_unknown_callback_cost'] = True
    return stats


def _accumulate_technician_labor_cost(archived_jobs):
    """
    Real labor cost per technician across `archived_jobs` -- the SAME
    population every other per-technician stat here uses. Built entirely
    from calculate_technician_labor_cost() (unchanged, reused directly);
    nothing re-derived.

    Returns {user_id: Decimal total labor cost} -- ONLY for technicians
    with at least one real, non-zero labour_rate found on at least one
    job in the window. A technician with no real rate data anywhere is
    simply ABSENT from this dict, never present with a fabricated $0 --
    the caller (get_technician_stats()) uses this dict's own presence/
    absence to decide whether to show a real profit_margin_percentage or
    "no data" (None), same "absent != a real 0" convention used
    throughout this app.

    Known simplification, not silently hidden: unlike callback_dollars_
    lost's has_unknown_callback_cost flag, a technician with SOME
    real-rate jobs and SOME no-rate jobs in the same window gets a
    margin computed only from the known-rate subset, with no separate
    "this is partial" signal -- not built here, since the approved
    proposal didn't ask for one; worth adding later if this ever becomes
    a real, observed case (this account currently has no non-zero rates
    at all yet, so it hasn't been).
    """
    costs = {}
    for job in archived_jobs:
        assignees = _gather_job_assignees(job)
        if not assignees:
            continue
        hours_by_user = calculate_job_duration_by_user(job)
        for user_id in assignees:
            cost = calculate_technician_labor_cost(job, user_id, hours_by_user)
            if cost is None:
                continue
            costs[user_id] = costs.get(user_id, Decimal('0')) + cost
    return costs


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


def get_technician_stats(tenant):
    """
    Local-table source for the Electricians panel's per-technician card +
    drawer fields that are now real: revenue, jobs_completed,
    revenue_per_hour, avg_job_duration_seconds, completion_percentage,
    team_revenue_share_percentage, current-month goal progress, and
    (2026-08-21) a SIMPLE real annual goal progress -- goal amount vs.
    real year-to-date revenue as a plain percentage, deliberately with
    no "on pace"/projected-year-end field (see the ytd_jobs block below).

    Takes a Tenant instance directly (not a Django user object) --
    renamed and widened from the original _local_technician_stats_response(user)
    (2026-08-21) specifically because this now has a SECOND real caller:
    apps.alerts.services.evaluate_alert_rules() needs the exact same
    numbers the Electricians panel's cards render, not a re-derived copy,
    and a tenant-taking, unprefixed function is the natural shared shape
    for that -- cheaper to introduce now, with 2 callers from the start,
    than later. JobberTechnicianStatsView.get() below passes
    request.user.tenant.

    Explicitly OUT of scope here (per TL): "on pace"/projected year-end
    for BOTH monthly and annual (pending TL -- needs real multi-month
    history that doesn't exist yet), job history (separate endpoint,
    next round), and monthly revenue trend chart (separate round). The
    4 threshold-based alerts previously deferred here now have a real
    home -- see apps.alerts.

    profit_margin_percentage was ORIGINALLY built (2026-09-03, labor_
    cost_profit_margin_proposal.md) against Jobber's own real per-entry
    labour_rate. SUPERSEDED (2026-09-06, direct TL decision, no design
    proposal): most real customers won't have that field filled in --
    the same onboarding-burden concern that already ruled out Custom
    Fields elsewhere in this project -- so the margin below no longer
    reads labor_costs/_accumulate_technician_labor_cost() at all. That
    function (and the underlying labour_rate model field/sync
    widening/calculate_technician_labor_cost()) is left completely in
    place, untouched, working code -- just no longer wired into THIS
    number. See _accumulate_technician_callback_stats()'s own docstring
    and the profit_margin_percentage block below for the real, current
    formula.
    """
    if tenant is None:
        return dict(_NOT_CONNECTED_DATA)
    tenant_id = tenant.id

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
    callback_stats = _accumulate_technician_callback_stats(archived_jobs)
    # NOT called here (2026-09-06, superseded direct TL decision -- see
    # this function's own docstring): _accumulate_technician_labor_cost()
    # still exists, unchanged, and still works -- it's just no longer
    # part of profit_margin_percentage's real formula below, so computing
    # it here would be a real, wasted per-job cost for a value nobody
    # reads. Revenue population for the margin below is still
    # `revenue_totals` above (Top Earner's Job.total-attributed share),
    # explicitly NOT the separate Total Revenue tile's Paid-invoices-only
    # figure -- that part of the original resolution is unchanged.
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
    # computed differently. current_month() uses timezone.localdate()
    # (2026-08-21 fix) -- correct relative to settings.TIME_ZONE ('UTC')
    # regardless of the server's own OS clock timezone, not merely
    # correct as long as the OS clock happens to also be UTC.
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

    # Year-to-date revenue, for the drawer's real (but deliberately SIMPLE)
    # Annual target line -- calculate_top_earner() reused a THIRD time,
    # over a Jan-1-of-this-year-to-now job queryset. Same function, same
    # "different window, not a new calculation" pattern as current-month
    # above -- NOT a projection, NOT an "on pace" pace signal: those need
    # real multi-month history that doesn't exist yet (the same reason the
    # old mock version's projection was nonsensical once real data hit
    # it). Just a real goal amount vs. real YTD revenue, as a plain
    # percentage -- nothing speculative.
    year_date = current_year()
    year_start = timezone.make_aware(datetime.combine(year_date, time.min))
    ytd_jobs = list(JobberJob.objects.filter(
        tenant_id=tenant_id, is_active=True, job_status='archived', completed_at__gte=year_start,
    ))
    ytd_revenue_totals = calculate_top_earner(ytd_jobs)

    # Mirrors goals_by_user_id above exactly, against TechnicianAnnualGoal
    # instead of TechnicianGoal.
    annual_goals_by_user_id = {
        goal.user_id: goal.goal_amount
        for goal in TechnicianAnnualGoal.fetch(tenant_id=tenant_id, year=year_date)
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

        # PENDING CONFIRMATION status lifted (2026-08-31, approved) -- see
        # _accumulate_technician_callback_stats()'s own docstring for the
        # full reasoning (assigned_users attribution, full-credit count vs.
        # proportional-dollar split, the has_unknown_callback_cost flag).
        cb_stats = callback_stats.get(tech.id)
        callback_visits_done = cb_stats['callback_visits_done'] if cb_stats else 0
        callback_dollars_lost = cb_stats['callback_dollars_lost'] if cb_stats else 0.0
        has_unknown_callback_cost = cb_stats['has_unknown_callback_cost'] if cb_stats else False
        # None (not 0) when jobs_completed is 0 -- "no data" (this
        # technician completed nothing this window, the ratio is
        # undefined), distinct from a real, confirmed 0% when they
        # completed jobs but had zero callbacks. Same "no data != 0"
        # convention as every other ratio in this function.
        callback_rate = (
            round((callback_visits_done / jobs_completed) * 100, 1) if jobs_completed > 0 else None
        )

        revenue_per_hour = (revenue / (total_seconds / 3600)) if total_seconds > 0 else None
        avg_job_duration_seconds = (
            round(total_seconds / tracked_job_count) if tracked_job_count > 0 else None
        )

        # SUPERSEDED FORMULA (2026-09-06, direct TL decision -- see this
        # function's own docstring for why labour_rate was dropped).
        # profit_margin_percentage is now (revenue - callback_dollars_
        # lost) / revenue x 100 -- reusing revenue_totals and
        # _accumulate_technician_callback_stats()'s own output directly,
        # no new calculation. This is deliberately NOT a full accounting
        # margin (no materials/other costs) -- it specifically measures
        # how much of this technician's revenue was eaten by real
        # callbacks (see TechnicianCard's own tooltip for the same
        # clarification shown to the customer).
        #
        # None (never a divide-by-zero or a fabricated 0%) when revenue
        # is 0 -- same "no data != 0" convention as every other ratio
        # here. Zero real callbacks this window -> callback_dollars_lost
        # is a real 0.0 and has_unknown_callback_cost is False -> a
        # clean, confident 100%, not marked as partial. A real callback
        # with has_unknown_callback_cost=True still computes a real
        # number from whatever KNOWN cost exists (callback_dollars_lost
        # already excludes unknown-cost callbacks entirely -- see that
        # function's own docstring) -- has_unknown_callback_cost itself
        # is what TechnicianCard reuses to mark this SAME number as a
        # minimum, not a new backend flag.
        profit_margin_percentage = (
            round(((revenue - callback_dollars_lost) / revenue) * 100, 1)
            if revenue > 0
            else None
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

        annual_goal_amount = annual_goals_by_user_id.get(tech.id)
        ytd_revenue = ytd_revenue_totals.get(tech.id, 0.0)
        annual_progress_percentage = (
            round((ytd_revenue / float(annual_goal_amount)) * 100, 1)
            if annual_goal_amount and float(annual_goal_amount) > 0
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
            # New (2026-09-03) -- the raw counts completion_percentage
            # itself is computed from, exposed alongside it so the drawer
            # can show "X of Y jobs" instead of just the ratio. Reuses
            # `assigned`/`archived_count` from _accumulate_completion_counts()
            # above directly -- not re-derived. Prefixed `completion_` on
            # purpose: this is a GENUINELY DIFFERENT population from
            # `jobs_completed` above (all jobs assigned in the window by
            # jobber_created_at, regardless of status, vs. jobs_completed's
            # archived + completed_at-windowed population) -- pairing these
            # with jobs_completed instead of completion_percentage would be
            # a real, silent mismatch, not just a naming nitpick.
            'completion_jobs_assigned': assigned,
            'completion_jobs_archived': archived_count,
            # (revenue - callback_dollars_lost) / revenue x 100 (2026-09-06,
            # superseded direct TL decision -- see the computation above for
            # the full reasoning). null (not 0%) only when revenue itself is
            # 0 this window -- never a divide-by-zero or a fabricated
            # number.
            'profit_margin_percentage': profit_margin_percentage,
            'team_revenue_share_percentage': team_revenue_share_percentage,
            # New (2026-08-31, approved) -- backend-only this round, no
            # frontend wiring yet. callback_visits_done: full credit to
            # every real assignee (assigned_users), same convention as
            # jobs_completed. callback_rate: null only when jobs_completed
            # is 0 (no data), a real 0.0 otherwise. callback_dollars_lost
            # sums ONLY callbacks with a real, known callback_bled_amount
            # -- has_unknown_callback_cost is the separate, explicit signal
            # for "at least one of this technician's real callbacks has an
            # unknown cost," so a 0 here is never ambiguous between
            # "confirmed zero lost" and "we don't actually know." See
            # _accumulate_technician_callback_stats()'s own docstring.
            'callback_visits_done': callback_visits_done,
            'callback_rate': callback_rate,
            'callback_dollars_lost': round(callback_dollars_lost, 2),
            'has_unknown_callback_cost': has_unknown_callback_cost,
            'goal_progress': {
                'goal_amount': float(goal_amount) if goal_amount is not None else None,
                'current_month_revenue': round(float(current_month_revenue), 2),
                'progress_percentage': progress_percentage,
            },
            # SIMPLE and real, deliberately -- see the ytd_jobs comment
            # above for why this has no "on pace"/projection field.
            'annual_goal_progress': {
                'goal_amount': float(annual_goal_amount) if annual_goal_amount is not None else None,
                'ytd_revenue': round(float(ytd_revenue), 2),
                'progress_percentage': annual_progress_percentage,
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
            data = get_technician_stats(request.user.tenant)
            return api_response_parser(
                data=data,
                message=MESSAGES['SUCCESS'],
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)

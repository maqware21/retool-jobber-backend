import logging
from datetime import timedelta

from dateutil.relativedelta import relativedelta
from django.db.models import Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.views import APIView

from apps.jobber.models import JobberAccount, JobberInvoice, JobberJob, JobberUser
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


_NOT_CONNECTED_DATA = {
    'connected': False,
    'total_revenue': None,
    'jobs_completed': None,
    'avg_job_duration_seconds': None,
    'top_earner': None,
    'period_months': PERIOD_MONTHS,
    'last_synced_at': None,
}


def _merge_intervals(intervals):
    """
    intervals: a list of (start, end) datetime tuples for ONE (job, user)
    pair. Returns the total seconds actually covered by their UNION, not
    the naive sum of each interval's own length — two overlapping ranges
    count their overlap once, not twice.

    Confirmed decision (2026-08-16): Job 2's real two entries for the same
    technician, 04:00-08:00 (14400s) and 04:00-09:00 (18000s), must merge
    to 04:00-09:00 = 18000s, not the naive sum 32400s. This is the function
    that produces that result — sort by start, walk forward, extend the
    current merged interval whenever the next one starts at or before its
    current end, otherwise close it out and start a new one.
    """
    if not intervals:
        return 0

    ordered = sorted(intervals, key=lambda iv: iv[0])
    merged = []
    cur_start, cur_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= cur_end:
            # Overlapping (or exactly touching) — extend, don't double-count.
            cur_end = max(cur_end, end)
        else:
            merged.append((cur_start, cur_end))
            cur_start, cur_end = start, end
    merged.append((cur_start, cur_end))

    return sum((end - start).total_seconds() for start, end in merged)


def calculate_job_duration_by_user(job):
    """
    Same merge logic calculate_job_duration_seconds() below is built on —
    grouped by user, each user's own overlapping timesheet-entry intervals
    merged via _merge_intervals() before summing — extracted out
    (2026-08-17) as its own function so Top Earner's per-technician revenue
    split can reuse the EXACT SAME, already-proven merge math instead of
    re-deriving hours independently from raw TimeSheetEntry rows.
    Re-implementing this a second time would risk silently reintroducing
    the exact Job 2 double-counting bug this logic exists to prevent.

    This is a PURE EXTRACTION of what used to be calculate_job_duration_seconds()'s
    own body — the merge math itself is UNCHANGED, not just similar.
    Verified bit-for-bit identical against real data via
    verify_top_earner_step3.py (Step 3a): calculate_job_duration_seconds()
    still returns exactly the same values for jobs 1, 2, 5, 6, 7, 12 as it
    did before this refactor.

    Per the confirmed decisions (2026-08-16, unchanged):
      - The SAME (job, user) pair's entries are merged into their time-range
        UNION before summing (via _merge_intervals above) — never a naive
        sum of each entry's own final_duration_seconds, which would
        double-count real overlapping time (the confirmed Job 2 case).
      - An entry with no linked local user can't be safely grouped with
        another no-user entry by identity — merging two unidentified
        people's time as if they were the same person would be a WORSE
        guess than not merging at all. Not yet observed in real data
        (every real entry checked so far has a linked user); each such
        entry gets its own group (keyed by the entry's own id) so it
        contributes its own duration unmerged, rather than risking an
        incorrect merge. Flagged, not silent.

    Returns {user_id: merged_seconds} — RAW (unrounded) seconds, one entry
    per user (or synthetic no-user group) who has at least one timesheet
    entry on this job. A job with zero entries returns {} (empty dict),
    not None — calculate_job_duration_seconds() below is the one that
    turns "no data" into None, to preserve its existing contract exactly.
    Deliberately NOT rounded per-user here: calculate_job_duration_seconds()
    needs to sum-then-round exactly once, at the end, to reproduce its
    pre-refactor behavior bit-for-bit — rounding each user's total first
    and then summing the rounded values is not guaranteed to equal
    rounding the sum, so that rounding step stays where it always was.

    Takes a JobberJob instance, not a bare id — lets this be called
    directly against real ORM objects in tests/verification scripts.
    """
    entries = list(job.timesheet_entries.filter(is_active=True))
    if not entries:
        return {}

    by_user = {}
    for entry in entries:
        group_key = entry.user_id if entry.user_id is not None else f'_no_user_{entry.id}'
        by_user.setdefault(group_key, []).append(entry)

    per_user_totals = {}
    for group_key, user_entries in by_user.items():
        seconds = 0
        intervals = []
        for entry in user_entries:
            start = entry.started_at
            if start is None:
                # No usable start time at all — cannot place this entry on
                # a timeline to merge it with anything. Not expected for a
                # completed/stopped entry (finalDuration only applies to
                # stopped entries), but add its own duration unmerged
                # rather than silently dropping real time. Flagged, not
                # expected in practice.
                seconds += entry.final_duration_seconds or 0
                continue

            end = entry.ended_at
            if end is None:
                # A still-ticking entry that ended up in this set (not
                # expected for an archived/completed job in practice) —
                # derive an end from the authoritative final_duration_seconds
                # rather than guessing at a real end time.
                end = start + timedelta(seconds=entry.final_duration_seconds or 0)

            intervals.append((start, end))

        seconds += _merge_intervals(intervals)
        per_user_totals[group_key] = seconds

    return per_user_totals


def calculate_job_duration_seconds(job):
    """
    Real, standalone, testable per-job duration calculation — deliberately
    NOT inline in the endpoint, so it can be called directly (as
    verify_avg_job_duration_calc.py does) against real JobberJob instances
    without going through a request/view at all.

    Now a thin wrapper over calculate_job_duration_by_user() (2026-08-17
    refactor) — that function owns the merge math; this just sums its
    per-user breakdown and applies the same single sum-then-round step
    this function always used, exactly reproducing pre-refactor behavior
    bit-for-bit (verified via verify_top_earner_step3.py, Step 3a).

    Per the confirmed decisions (2026-08-16, unchanged by this refactor):
      - DIFFERENT users overlapping the same job (not yet observed in real
        data) are summed independently, each AFTER their own overlaps are
        merged — a labour-hours definition (e.g. 2 people x 4 concurrent
        hours = 8 labour-hours), not wall-clock elapsed time. This remains
        an explicit, documented open assumption — see PROJECT_CONTEXT.md —
        not re-decided here.
      - A job with ZERO timesheet entries returns None, never 0 — the
        caller (avg_job_duration_seconds() below) must treat that as "no
        data," not as a real 0-duration job, same convention already
        established for completed_at.
    """
    per_user = calculate_job_duration_by_user(job)
    if not per_user:
        return None
    return int(round(sum(per_user.values())))


def split_job_revenue_among_assignees(job_revenue, hours_by_user):
    """
    "Top Earner" per-technician revenue split for one job — real,
    standalone, testable pure function (plain numbers/dicts in and out, no
    ORM/DB coupling), deliberately not inline in any endpoint/orchestration
    code (none exists yet — this is Part A: schema + pure functions only).

    hours_by_user: {user_id: tracked_seconds} — MUST include every person
    actually ASSIGNED to the job as a key, with 0 for anyone assigned but
    who tracked no time on it. This function has no concept of "assigned"
    vs. "tracked" as separate things — the caller (future orchestration,
    Part B) builds this dict from JobberVisit.assigned_users (every
    assignee) and calculate_job_duration_by_user() (who actually tracked
    what) before calling this.

    Per Jobber's own confirmed attribution rule (Team Productivity Report +
    support bot) — 3 discrete cases, plus one gap this project is filling
    in with its own interpretation, flagged explicitly below:

      1. Everyone assigned tracked time -> proportional split by hours.
      2. No one tracked time -> EQUAL split among all assigned (per TL
         decision, 2026-08-17: NOT excluded from Top Earner, even though a
         job with zero tracked time is excluded from the separate Avg Job
         Duration average — two different features, two different,
         independently-confirmed rules for the same underlying "no time
         data" situation).
      3. Exactly one person tracked time -> 100% to that one person.
      4. [GAP — not covered by Jobber's stated 3 rules. OUR INTERPRETATION,
         NOT INDEPENDENTLY CONFIRMED for this exact sub-case]: some but not
         all assigned people tracked time (2+ people tracked, but fewer
         than everyone assigned) -> proportional split among only those who
         tracked, excluding the 0-hour assignees from the pool entirely —
         the natural extension of rule 1's "proportional by hours"
         principle with non-trackers simply removed from the split, rather
         than a new rule invented from nothing. Revisit against Jobber's
         docs/support bot specifically for this sub-case before treating it
         as settled.

    Returns {user_id: revenue_share} — a plain dict, same currency unit as
    job_revenue (no rounding applied here; that's a display concern for
    whatever renders this).

    Intended pairing (Part B, not built yet): job_revenue = Job.total for
    an archived job in the same completed_at-windowed population
    jobs_completed/avg_job_duration_seconds already use — NOT the Paid-
    invoice-filtered population Total Revenue uses. This is a genuine,
    confirmed population/definition mismatch, not an oversight: Top
    Earner's per-technician shares, summed across every technician, will
    NOT necessarily equal the page's own Total Revenue tile, since
    Job.total counts an archived job's full billed value regardless of
    that job's own invoice's payment status, while Total Revenue only
    counts invoices already marked Paid. Flagged here so whoever wires up
    the orchestration in Part B sees it at the exact point it matters, not
    just in PROJECT_CONTEXT.md.
    """
    assignees = list(hours_by_user.keys())
    if not assignees:
        return {}

    tracked = {u: h for u, h in hours_by_user.items() if h > 0}

    if len(tracked) == len(assignees):
        # Rule 1: everyone tracked — proportional split by hours.
        total_hours = sum(tracked.values())
        return {u: job_revenue * (h / total_hours) for u, h in tracked.items()}

    if not tracked:
        # Rule 2: no one tracked — equal split among all assigned.
        share = job_revenue / len(assignees)
        return {u: share for u in assignees}

    if len(tracked) == 1:
        # Rule 3: exactly one person tracked — 100% to them.
        only_user = next(iter(tracked))
        return {only_user: job_revenue}

    # Rule 4 (gap-filling interpretation, see docstring) — some but not
    # all tracked: proportional split among only those who tracked.
    total_hours = sum(tracked.values())
    return {u: job_revenue * (h / total_hours) for u, h in tracked.items()}


def _gather_job_assignees(job):
    """
    Every REAL, locally-known assignee across ALL of a job's visits,
    deduped by user id — the "assigned" side of Top Earner's split, from
    JobberVisit.assigned_users (the additive M2M added in Part A). This is
    NOT the same population as calculate_job_duration_by_user()'s keys,
    which are keyed by whoever actually has a TimeSheetEntry (including a
    possible synthetic no-user bucket that doesn't correspond to any real
    assignee at all) — this function is strictly "who is assigned,"
    independent of who tracked anything.

    Returns {user_id: JobberUser}. Not optimized for scale (a per-job
    query, same as every other per-job helper in this module) — fine at
    this project's current real data volume (13 jobs, 14 visits total).
    """
    assignees = {}
    for visit in job.visits.filter(is_active=True).prefetch_related('assigned_users'):
        for assigned_user in visit.assigned_users.all():
            assignees[assigned_user.id] = assigned_user
    return assignees


def calculate_job_revenue_shares(job):
    """
    Orchestration (impure — hits the DB) tying together every
    already-verified Part A piece for ONE job's Top Earner contribution.
    Nothing here is speculative; every piece below was individually
    proven correct in Part A before this function combines them:

      1. Every real assignee across the job's visits, deduped —
         _gather_job_assignees() above, from the new assigned_users M2M.
      2. Each assignee's tracked hours on this job —
         calculate_job_duration_by_user(job), the EXACT SAME merge logic
         already verified bit-for-bit against real data (job 2 -> 18000s,
         confirmed). NOT re-derived here. An assignee with zero timesheet
         entries gets 0 hours (not omitted) — they must still appear in
         hours_by_user for split_job_revenue_among_assignees() to
         correctly tell "no one tracked" from "everyone tracked" from
         "partial."
      3. job.total as revenue — same archived + completed_at population
         jobs_completed/avg_job_duration_seconds already use, NOT the
         Paid-invoice population Total Revenue uses (confirmed mismatch,
         see split_job_revenue_among_assignees()'s own docstring).
         Explicitly coerced to float here — job.total is a Decimal, and
         split_job_revenue_among_assignees() does float division
         internally (Decimal * float raises TypeError in Python); this is
         the one place that conversion needs to happen, so it happens
         here rather than inside the already-verified pure function.
      4. split_job_revenue_among_assignees() — the already-verified pure
         4-branch split, untouched.

    KNOWN EDGE CASE, not silently absorbed: a TimeSheetEntry with no
    linked local JobberUser (calculate_job_duration_by_user()'s synthetic
    "_no_user_<entry_id>" bucket) doesn't correspond to any real assignee,
    so its tracked hours are NOT included in hours_by_user at all here —
    that time is invisible to the split, neither counted as "someone
    tracked" evidence nor attributed to anyone's revenue share. Not yet
    observed in real data (every real entry checked so far has a linked
    user); flagged, not silently dropped.

    Returns {user_id: revenue_share} for this one job — an empty dict if
    the job has no assignees at all (nothing to attribute revenue to).
    """
    assignees = _gather_job_assignees(job)
    if not assignees:
        return {}

    hours_by_entry_user = calculate_job_duration_by_user(job)
    hours_by_user = {
        user_id: hours_by_entry_user.get(user_id, 0)
        for user_id in assignees
    }

    return split_job_revenue_among_assignees(float(job.total), hours_by_user)


def calculate_top_earner(archived_jobs):
    """
    Accumulates each technician's revenue_share (calculate_job_revenue_shares())
    across every job in `archived_jobs` into a running total for the whole
    window. `archived_jobs` is passed in by the caller (the same
    archived + completed_at-windowed queryset jobs_completed/
    avg_job_duration_seconds already use) rather than re-queried here —
    same "reuse, don't re-derive" discipline as the rest of this feature.

    Returns {user_id: accumulated_revenue} across the whole window — not
    yet resolved to a name or narrowed to a single "top" pick; see
    pick_top_earner() for that. Empty dict if no job in the window has any
    assignee at all.
    """
    totals = {}
    for job in archived_jobs:
        shares = calculate_job_revenue_shares(job)
        for user_id, share in shares.items():
            totals[user_id] = totals.get(user_id, 0) + share
    return totals


def pick_top_earner(totals, users_by_id):
    """
    totals: {user_id: accumulated_revenue} from calculate_top_earner().
    users_by_id: {user_id: JobberUser}, for name resolution.

    TIE-BREAK DECISION (2026-08-17, explicit, not silent): on an exact tie
    for the highest accumulated revenue, the winner is whoever's name
    sorts first alphabetically (ascending, case-insensitive). Simple,
    fully deterministic — the "Top Earner" tile can never flicker between
    two different people showing the identical number on repeat loads or
    re-syncs of the exact same underlying data.

    Returns {'name': str, 'revenue': float} (revenue rounded to 2 decimal
    places for display — the only rounding point for this value; the
    proportional-split arithmetic upstream can produce long floating
    decimals, e.g. $333.333333...), or None if `totals` is empty (no
    archived job in the window had any assignee at all — the null case
    the endpoint contract requires, handled defensively here rather than
    letting max()/min() raise on an empty sequence).
    """
    if not totals:
        return None

    max_revenue = max(totals.values())
    tied_user_ids = [uid for uid, revenue in totals.items() if revenue == max_revenue]
    winner_id = min(tied_user_ids, key=lambda uid: (users_by_id[uid].name or '').lower())

    return {
        'name': users_by_id[winner_id].name,
        'revenue': round(float(max_revenue), 2),
    }


def _local_electricians_summary_response(user):
    """
    Local-table source for the Electricians panel's KPI tiles — ALL 4 mock
    tiles (Total Revenue, Jobs Completed, Avg Job Duration, Top Earner)
    are now real, replaced in place with no visual distinction from the
    original mock version, per TL decision.

    Calls ensure_fresh(require_complete=True) first — same reasoning
    already established for Accounts/Employees: a Sum/count/average
    computed over a partially-synced entity set is a WRONG number, not
    just a stale one, so a PARTIAL sync gets one retry (via
    require_complete) before this proceeds regardless — see
    ensure_fresh()'s own docstring for exactly what that retry does and
    doesn't guarantee. Requests 'invoices', 'jobs', 'visits', AND
    'timesheet_entries' — avg_job_duration_seconds and top_earner both
    read JobberTimeSheetEntry via each job's timesheet_entries, same
    completeness requirement as the other fields reading their own
    entities. 'visits' is REQUIRED explicitly here, not implied by 'jobs'
    — confirmed against sync_tenant()'s own branching: 'jobs'/
    'timesheet_entries' only trigger the shared job_nodes fetch, but
    sync_visits() (which populates JobberVisit.assigned_users, Part A)
    only runs when 'visits' is itself in the requested entity set. Omitting
    it here would silently leave top_earner reading stale/never-synced
    assignee data.

    period_start uses dateutil.relativedelta(months=PERIOD_MONTHS), not
    timedelta(days=180) — a real calendar-month subtraction (e.g. Feb 12 ->
    Aug 12) rather than an approximate 180-day window, which drifts by a
    few days depending on which months are actually in range. Shared by
    total_revenue, jobs_completed, AND avg_job_duration_seconds — same
    6-month window, and the same underlying archived-jobs population, for
    all three.
    """
    tenant_id = user.tenant_id
    if not tenant_id:
        return dict(_NOT_CONNECTED_DATA)

    account = JobberAccount.objects.filter(tenant_id=tenant_id, is_active=True).first()
    if account is None:
        return dict(_NOT_CONNECTED_DATA)

    fresh = ensure_fresh(account.tenant, entities=['invoices', 'jobs', 'visits', 'timesheet_entries'], require_complete=True)

    period_start = timezone.now() - relativedelta(months=PERIOD_MONTHS)

    total = JobberInvoice.objects.filter(
        tenant_id=tenant_id,
        is_active=True,
        status_display='Paid',
        issued_date__gte=period_start,
    ).aggregate(total=Sum('amount'))['total']

    # "archived = completed" is settled (confirmed from 3 separate angles —
    # direct testing, Jobber's own docs, Jobber's support bot). completed_at
    # is nullable in Jobber's own schema and, per a live cross-check
    # (2026-08-16), is untested for the one case that plausibly nulls it —
    # an archived job with NO linked invoice at all (skip-invoicing config,
    # or cancelled — a real, valid case). completed_at__gte=period_start
    # naturally EXCLUDES a null completed_at (SQL: NULL >= X is unknown,
    # never true) rather than falling back to another date field (e.g.
    # jobber_created_at) — a job's creation date can sit arbitrarily far
    # from when it was actually archived/completed, so defaulting to it
    # would risk a WRONG inclusion/exclusion, not just an approximate one.
    # Net effect: jobs_completed may UNDERCOUNT slightly if/when a
    # no-invoice archived job's completed_at turns out to also be null —
    # a documented, bounded gap, not a silent guess.
    archived_jobs = JobberJob.objects.filter(
        tenant_id=tenant_id,
        is_active=True,
        job_status='archived',
        completed_at__gte=period_start,
    )
    jobs_completed = archived_jobs.count()

    # Same archived-jobs population as jobs_completed above — deliberately
    # reused, not re-queried, so both numbers describe the same underlying
    # job set. Jobs with zero timesheet entries are excluded from the
    # average entirely (calculate_job_duration_seconds returns None for
    # them) — never folded in as a 0-duration job, which would silently
    # drag the average down for jobs Jobber simply has no time data for.
    per_job_durations = [
        d for d in (calculate_job_duration_seconds(job) for job in archived_jobs)
        if d is not None
    ]
    avg_job_duration_seconds = (
        round(sum(per_job_durations) / len(per_job_durations))
        if per_job_durations
        else None
    )

    # Same archived-jobs population as jobs_completed/avg_job_duration_seconds
    # above — the third and last consumer of this exact queryset. Revenue
    # source is job.total (see split_job_revenue_among_assignees()'s and
    # calculate_job_revenue_shares()'s own docstrings for the confirmed,
    # deliberate population mismatch against this endpoint's own
    # total_revenue field above, which uses Paid invoices instead).
    top_earner_totals = calculate_top_earner(archived_jobs)
    if top_earner_totals:
        users_by_id = {u.id: u for u in JobberUser.objects.filter(id__in=top_earner_totals.keys())}
        top_earner = pick_top_earner(top_earner_totals, users_by_id)
    else:
        # Null case per the endpoint contract: no archived job in the
        # window had any assignee at all — not an error, handled
        # defensively rather than letting pick_top_earner() see an empty
        # dict unexpectedly (it also guards this itself, belt-and-braces).
        top_earner = None

    data = {
        'connected': True,
        # Genuinely zero (no Paid invoices in the period) is a real,
        # different answer from "not connected" (total_revenue: None
        # above) — Sum() returns None for an empty queryset, so that's
        # coalesced to 0.0 here, not left as None.
        'total_revenue': float(total) if total is not None else 0.0,
        'jobs_completed': jobs_completed,
        'avg_job_duration_seconds': avg_job_duration_seconds,
        'top_earner': top_earner,
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
        data = dict(_NOT_CONNECTED_DATA)
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

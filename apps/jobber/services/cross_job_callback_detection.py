"""
Cross-job callback detection -- the client's own replacement definition
for detect_and_freeze_callbacks()'s "same job reopened" model.
detect_and_freeze_callbacks() itself is left completely intact and
callable (see its own docstring); only its call site in sync_tenant()
is disconnected, in favor of detect_cross_job_callbacks() below.

A callback here is a BRAND NEW, separate Job row -- never a reopen of
the original job. 3 of the 4 real keyword-match locations are wired up
(job title, visit title, visit instructions); Jobber's own Notes feature
(a separate JobNoteUnionConnection type on both Job and Visit) is a
DELIBERATE, explicitly-flagged follow-up, not built here -- leaving it
out can only produce a missed keyword match, never a false positive,
since every one of the other 3 real conditions still has to agree too.

Event-driven, self-correcting detection (no permanent freeze): every
pass re-examines every job that genuinely still needs it, computed
FRESH each time from real, immutable or current field values -- never
from a persisted "done" flag. A job's real text/hours/schedule can firm
up well after it's first matched (a technician often doesn't log hours
or type "no charge" until closing the job out, and a scheduled visit's
time can be corrected after the fact) -- a permanent "done" flag would
freeze whatever was true the first time a pass happened to run, which
can be wrong or incomplete, with no way to ever correct it.
JobberJob.cross_job_callback_resolved stays in the schema, additive-
only, but is no longer read or written anywhere in this module.
"""
import logging
from datetime import timedelta

from django.utils import timezone

from apps.jobber.api.electricians_summary import (
    _quantize_money,
    calculate_job_duration_by_user,
    job_has_logged_labour_hours,
)
from apps.jobber.models import JobberJob
from apps.jobber.services import client
from apps.jobber.services.sync import CALLBACK_SCHEDULED_HOURS_CEILING, _to_datetime, _to_decimal

logger = logging.getLogger(__name__)

# The real, fixed match-eligibility window from the client's own
# proposal -- the gap between the 2 jobs' own real timestamps, a fixed
# historical fact that doesn't change as "now" advances. A not-yet-
# matched job stops being worth rescanning once it's older than this,
# since no earlier job could ever satisfy the window going forward
# either.
CROSS_JOB_CALLBACK_WINDOW_DAYS = 30

# A SEPARATE, longer leash for a job that's ALREADY matched (sticky, see
# _find_earlier_match()'s own docstring) but whose dollar amount is still
# unknown or estimated -- real logged hours or a schedule correction can
# show up well after the match-eligibility window itself has closed.
# Never rescanned past this, even if still unknown -- see
# detect_cross_job_callbacks()'s own docstring for why an unbounded
# rescan isn't needed (the 10-minute poll + webhooks both keep working
# regardless; this constant only bounds how long the UNKNOWN/ESTIMATED
# state itself keeps getting retried).
CROSS_JOB_CALLBACK_AMOUNT_RESCAN_CEILING_DAYS = 90

# Verbatim from the client's own proposal.
CROSS_JOB_CALLBACK_KEYWORDS = [
    'callback', 'return visit', 'redo', 'warranty', 'no charge',
    'fix previous', 'warranty call',
]


def _job_text_has_keyword(job):
    """
    Signal 3 -- scans 3 of the 4 real keyword-match locations: this job's
    own title, and every one of its real active visits' own title and
    instructions. Deliberately checks the CANDIDATE job's own text only,
    never the earlier job's -- a free return visit's own note/title is
    what actually describes it, same as the client's own proposal.
    """
    texts = [job.title or '']
    for visit in job.visits.filter(is_active=True):
        texts.append(visit.title or '')
        texts.append(visit.instructions or '')
    haystack = ' '.join(texts).lower()
    return any(keyword in haystack for keyword in CROSS_JOB_CALLBACK_KEYWORDS)


def _needs_rescan(job, now):
    """
    Computed FRESH every call, from real current/immutable field values
    only -- never from a persisted terminal flag. A job needs rescanning
    if EITHER:

      1. It's still within its own real CROSS_JOB_CALLBACK_WINDOW_DAYS
         (30 days) of creation -- covers both a not-yet-matched job
         (still worth trying to match) AND an already-matched job that's
         still young (its amount might still need recomputing as real
         hours come in). Deliberately unconditional on match/amount
         state within this window -- a confirmed, already-settled job
         still gets re-examined until its own window closes; a known,
         accepted minor inefficiency, not a correctness gap.
      2. It's ALREADY matched (cross_job_callback_of is set) AND it still
         has NO real logged labour hours of its own (still estimate
         mode, per job_has_logged_labour_hours() -- not yet a real,
         per-person labour-mode figure) AND it's within
         CROSS_JOB_CALLBACK_AMOUNT_RESCAN_CEILING_DAYS (90 days) of
         creation -- a longer leash specifically for the estimate, past
         the match-eligibility window itself. Checked against this job's
         own REAL current timesheet entries, never against the stored
         cross_job_callback_bled_amount/_is_estimated fields themselves
         -- a legacy stored amount that predates this check (non-null,
         not flagged estimated) must not be mistaken for "settled" when
         the job genuinely still has no logged hours.

    A job with no real jobber_created_at can't have either condition
    evaluated honestly, so it's excluded -- same "no data != assumed"
    convention used everywhere else in this app.

    An UNMATCHED job older than 30 days returns False permanently (not
    via a stored flag -- simply because condition 1 can never be true
    again for it, and condition 2 requires a match that doesn't exist).
    A MATCHED job that already has real logged hours (already in labour
    mode), once past 30 days, also returns False (condition 1 no longer
    true; condition 2 excludes a job already in labour mode by design)
    -- once labour mode starts, there's nothing left to rescan for that
    job's estimate ever again.
    """
    if job.jobber_created_at is None:
        return False
    age = now - job.jobber_created_at
    if age <= timedelta(days=CROSS_JOB_CALLBACK_WINDOW_DAYS):
        return True
    if job.cross_job_callback_of_id is not None:
        still_estimate_mode = not job_has_logged_labour_hours(job)
        if still_estimate_mode and age <= timedelta(days=CROSS_JOB_CALLBACK_AMOUNT_RESCAN_CEILING_DAYS):
            return True
    return False


def _find_earlier_match(job, candidates):
    """
    All 4 real conditions required TOGETHER, per the approved design --
    never a scored/weighted combination of any 2 or 3:
      1. Same real Property.id (job.property_id) -- exact match, never
         string/fuzzy address comparison.
      2. Same real client (job.client_id).
      3. A real keyword hit on THIS job's own text (see
         _job_text_has_keyword above).
      4. Safeguard: a real, non-null, EQUAL service_type on both jobs --
         an unconfirmable (null) service_type on either side is NOT
         assumed to pass, same "no data != assumed" convention used
         throughout this app.
      5. The earlier job's real completed_at is within
         CROSS_JOB_CALLBACK_WINDOW_DAYS of this job's real
         jobber_created_at.

    ONLY EVER CALLED for a job with no existing cross_job_callback_of --
    see detect_cross_job_callbacks()'s own main loop. Once a job is
    matched, this function is never called again for it; the match is
    STICKY and permanent (confirmed: no code path anywhere in this
    module reassigns or clears cross_job_callback_of once set).

    Returns the nearest matching earlier JobberJob, or None.
    """
    if not job.property_id or not job.client_id or not job.service_type:
        return None
    if not job.jobber_created_at:
        return None
    if not _job_text_has_keyword(job):
        return None

    best = None
    best_gap = None
    for other in candidates:
        if other.id == job.id:
            continue
        if other.property_id != job.property_id:
            continue
        if other.client_id != job.client_id:
            continue
        if not other.service_type or other.service_type != job.service_type:
            continue
        if not other.completed_at or other.completed_at >= job.jobber_created_at:
            continue
        gap = job.jobber_created_at - other.completed_at
        if gap > timedelta(days=CROSS_JOB_CALLBACK_WINDOW_DAYS):
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = other, gap
    return best


def _compute_cross_job_bled_amount(account, earlier_job, later_job):
    """
    ESTIMATE MODE ONLY -- the real per-person labour-mode figure is never
    computed here; once later_job has any real logged hours of its own,
    technician_stats.py's _accumulate_technician_cross_job_callback_stats()
    computes each contributor's own hours x their own rate fresh, at read
    time, from local entries, entirely bypassing this function's result.
    This function skips its own live fetch outright once that's true --
    there's nothing left for it to estimate.

    A genuine no-charge job still carries a real, non-zero Job.total --
    Jobber prices line items independently of whether the job is ever
    invoiced. earlier_job's own real rate (its total / its own real
    logged hours) x later_job's own real SCHEDULED visit hours, capped at
    CALLBACK_SCHEDULED_HOURS_CEILING.

    Returns (amount_or_None, is_estimated, fetch_failed) -- a real
    3-way distinction, not 2:

      - fetch_failed=True means the live scheduled-time lookup
        (fetch_job_visits_for_callback_detection) could not be reached
        right now (a transient JobberAPIError). amount/is_estimated
        returned alongside this are meaningless placeholders (None,
        False) -- the caller MUST NOT write them; whatever was
        previously stored stays untouched. This is "could not be
        computed right now," distinct from a real negative result.
      - fetch_failed=False means amount/is_estimated ARE the real,
        current, honest answer -- including a genuine (None, False),
        which means real data was actually checked and there is
        genuinely no usable cost basis right now, OR that later_job has
        already moved into labour mode (nothing to estimate). The caller
        SHOULD write this, even if it downgrades a previous estimated
        value (e.g. real hours just got logged, or no usable schedule
        exists) -- that's the honest current truth, not a failure, same
        "no data != 0" convention as everywhere else.

    Safe against stale/incomplete local timesheet data on purpose:
    calculate_job_duration_by_user() is a pure local-DB read with a
    confirmed "{} on zero entries" contract (its own docstring), never
    an exception -- so earlier_hours simply comes back 0 on stale/missing
    data, which the `earlier_hours <= 0` check below already turns into a
    real, successful (not failed) None result.
    """
    if job_has_logged_labour_hours(later_job):
        return None, False, False

    earlier_hours = sum(calculate_job_duration_by_user(earlier_job).values()) / 3600
    if earlier_hours <= 0 or earlier_job.total is None:
        return None, False, False
    job_rate = earlier_job.total / _to_decimal(earlier_hours)

    # No logged hours on the later job -- the real scheduled-time fallback,
    # reusing detect_and_freeze_callbacks()'s EXACT existing live fetch
    # (fetch_job_visits_for_callback_detection already carries
    # startAt/endAt per visit -- no new query needed for this).
    try:
        visits_raw = client.fetch_job_visits_for_callback_detection(account, later_job.jobber_id)
    except client.JobberAPIError:
        return None, False, True

    if not visits_raw:
        return None, False, False

    latest_visit_raw = max(visits_raw, key=lambda v: v.get('createdAt') or '')
    scheduled_start = _to_datetime(latest_visit_raw.get('startAt'))
    scheduled_end = _to_datetime(latest_visit_raw.get('endAt'))
    if not (scheduled_start and scheduled_end and scheduled_end > scheduled_start):
        return None, False, False

    scheduled_hours = (scheduled_end - scheduled_start).total_seconds() / 3600
    if scheduled_hours > CALLBACK_SCHEDULED_HOURS_CEILING:
        return None, False, False

    return _quantize_money(job_rate * _to_decimal(scheduled_hours)), True, False


def detect_cross_job_callbacks(account, tenant):
    """
    Called once per sync_tenant() pass (see that function's own call
    site) -- NOT a one-shot "just transitioned" trigger like
    detect_and_freeze_callbacks(). Re-scans every real job that
    _needs_rescan() says still needs it, every pass, because a
    candidate's own real text/hours/schedule can firm up at any point
    during its real rescan window (a technician often doesn't log hours
    or type "no charge" until closing the job out) -- a one-time check
    would miss that. See _needs_rescan()'s own docstring for the exact,
    fresh-computed-every-time eligibility rule.

    2 distinct kinds of work happen here, never mixed:

      1. An UNMATCHED candidate (cross_job_callback_of is None) goes
         through _find_earlier_match() -- the only place a match is ever
         created. If found, it's written ONCE, together with whatever
         amount _compute_cross_job_bled_amount() can determine right
         now (or None/unknown if that live fetch itself failed -- there's
         no PRIOR amount to protect on a brand-new match, so storing a
         real, honest "unknown for now" is correct; it'll naturally
         retry next pass via _needs_rescan()'s own "unknown" branch).

      2. An ALREADY-matched candidate (cross_job_callback_of already
         set) NEVER goes through _find_earlier_match() again -- the
         match is STICKY and permanent once set (confirmed: no code
         path below reassigns or clears cross_job_callback_of). Only
         the amount/is_estimated get recomputed, from the SAME fixed
         (earlier, later) pair. If the earlier job is inactive or
         missing, the stored values are left completely untouched and
         logged -- there's nothing safe to recompute from. If the live
         fetch itself fails transiently, the stored values are ALSO left
         completely untouched (see _compute_cross_job_bled_amount()'s
         own fetch_failed contract) -- never overwritten with None.

    Writes only happen when a value actually changes (an explicit
    before/after comparison, not an unconditional save every pass) --
    this is cosmetic for correctness (the value would end up the same
    either way) but avoids a real, wasted UPDATE + synced_at-style
    churn on every single pass for a job that hasn't actually changed.

    Not optimized for scale (loads every active job for the tenant into
    memory once per pass, same as _gather_job_assignees()'s own
    documented tradeoff elsewhere in this codebase) -- fine at this
    project's current real data volume; revisit if that changes.

    Each candidate's processing is individually isolated in its own
    try/except: sync_tenant() calls this function inside the SAME try
    block that still has sync_invoices()/sync_expenses() ahead of it
    (confirmed directly in sync.py) -- an unhandled exception from one
    bad job here would otherwise propagate out, mark the WHOLE sync
    pass as failed, and skip invoices/expenses for this tenant on every
    pass, not just fail to process that one job. An exception here is
    logged with the job's own id and that job is skipped; every other
    candidate still gets processed normally in the same pass.
    """
    now = timezone.now()
    all_active_jobs = list(JobberJob.objects.filter(tenant=tenant, is_active=True))
    candidates = [job for job in all_active_jobs if _needs_rescan(job, now)]

    matched = 0
    recomputed = 0
    failed = 0
    for job in candidates:
        try:
            if job.cross_job_callback_of_id is not None:
                earlier = job.cross_job_callback_of
                if earlier is None or not earlier.is_active:
                    logger.info(
                        "detect_cross_job_callbacks: job=%s's earlier job (id=%s) is "
                        "inactive or missing -- leaving stored cross-job values "
                        "untouched, not recomputing.",
                        job.jobber_id, job.cross_job_callback_of_id,
                    )
                    continue

                amount, is_estimated, fetch_failed = _compute_cross_job_bled_amount(account, earlier, job)
                if fetch_failed:
                    logger.info(
                        "detect_cross_job_callbacks: job=%s's scheduled-time lookup "
                        "failed this pass -- leaving its stored amount/estimated "
                        "flag untouched, will retry next pass.",
                        job.jobber_id,
                    )
                    continue

                if (
                    amount != job.cross_job_callback_bled_amount
                    or is_estimated != job.cross_job_callback_bled_amount_is_estimated
                ):
                    job.cross_job_callback_bled_amount = amount
                    job.cross_job_callback_bled_amount_is_estimated = is_estimated
                    job.save(update_fields=[
                        'cross_job_callback_bled_amount',
                        'cross_job_callback_bled_amount_is_estimated',
                    ])
                    recomputed += 1
            else:
                match = _find_earlier_match(job, all_active_jobs)
                if match is None:
                    continue

                amount, is_estimated, fetch_failed = _compute_cross_job_bled_amount(account, match, job)
                if fetch_failed:
                    # No prior value exists to protect on a brand-new match --
                    # store the honest "unknown for now"; _needs_rescan()'s
                    # own "amount still unknown" branch picks this up again
                    # next pass.
                    amount, is_estimated = None, False

                job.cross_job_callback_of = match
                job.cross_job_callback_bled_amount = amount
                job.cross_job_callback_bled_amount_is_estimated = is_estimated
                job.save(update_fields=[
                    'cross_job_callback_of',
                    'cross_job_callback_bled_amount',
                    'cross_job_callback_bled_amount_is_estimated',
                ])
                matched += 1
        except Exception:
            failed += 1
            logger.exception(
                "detect_cross_job_callbacks: unexpected error processing job=%s "
                "(id=%s) -- skipping this job only, continuing with the rest of "
                "this pass.",
                job.jobber_id, job.id,
            )
            continue

    return {'checked': len(candidates), 'matched': matched, 'recomputed': recomputed, 'failed': failed}

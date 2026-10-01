"""
Cross-job callback detection -- the client's validated replacement for
detect_and_freeze_callbacks()'s "same job reopened" definition. See
cross_job_callback_detection_replacement_plan.md for the full design and
cross-check against research_alternative_callback_detection.py, whose
exact 4-condition rule (address AND client AND keyword, with a matching
real service_type, within CROSS_JOB_CALLBACK_WINDOW_DAYS) this reuses
unchanged -- that script validated this precise rule against real test
data before it was approved to replace the live trigger.

A callback here is a BRAND NEW, separate Job row -- never a reopen of
the original job. detect_and_freeze_callbacks() itself is left
completely intact and callable (see its own docstring); only its call
site in sync_tenant() is disconnected, per explicit instruction, in
favor of detect_cross_job_callbacks() below.

4 real keyword-match locations exist in the design; only 3 are wired up
here. The 4th, Jobber's own Notes feature (a separate JobNoteUnionConnection
type on both Job and Visit, never touched anywhere in this app), is a
DELIBERATE, explicitly-flagged follow-up -- confirmed as the heaviest
schema lift of the 4 (a genuinely new, additional query shape, not a
one-field widening like title/instructions), sequenced after this build
rather than blocking it. Leaving it out can only produce a missed
keyword match (a false negative on Signal 3 alone) -- it can never cause
a false positive, since every one of the other 3 real conditions still
has to agree too. Safe to ship without it.
"""
from datetime import timedelta

from django.utils import timezone

from apps.jobber.api.electricians_summary import calculate_job_duration_by_user
from apps.jobber.models import JobberJob
from apps.jobber.services import client
from apps.jobber.services.sync import CALLBACK_SCHEDULED_HOURS_CEILING, _to_datetime, _to_decimal

# Same real window the client's own proposal specified, confirmed via
# research_alternative_callback_detection.py against real test data.
CROSS_JOB_CALLBACK_WINDOW_DAYS = 30

# Verbatim from the client's own proposal notes -- same list already
# validated in research_alternative_callback_detection.py.
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
    what actually describes it, same as the client's own proposal and
    the research script's own validated design.
    """
    texts = [job.title or '']
    for visit in job.visits.filter(is_active=True):
        texts.append(visit.title or '')
        texts.append(visit.instructions or '')
    haystack = ' '.join(texts).lower()
    return any(keyword in haystack for keyword in CROSS_JOB_CALLBACK_KEYWORDS)


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
    Option 1 (approved, confirmed via verify_job5_total.py's real result
    that a genuine no-charge job still carries a real, non-zero
    Job.total) -- adapts the existing rate formula across 2 separate
    jobs instead of one job split by first_archived_at: earlier_job's
    own real rate (its total / its own real logged hours) x later_job's
    own real logged hours, or the same scheduled-time fallback +
    CALLBACK_SCHEDULED_HOURS_CEILING if later_job has no logged hours.
    Null whenever it genuinely can't be computed -- never a fabricated
    0, same "no data != 0" convention as detect_and_freeze_callbacks().

    Safe against stale/incomplete local timesheet data on purpose, since
    this is now called whenever 'jobs' or 'visits' sync, NOT gated on
    'timesheet_entries' having run this same pass (see sync_tenant()'s
    own call site comment): calculate_job_duration_by_user() is a pure
    local-DB read with a confirmed "{} on zero entries" contract (its own
    docstring), never an exception -- so earlier_hours/later_hours simply
    come back 0 on stale/missing data, which the `earlier_hours <= 0`
    check below already turns into a plain None, not a crash or a wrong
    number computed from partial data.

    Returns (amount_or_None, is_estimated).
    """
    earlier_hours = sum(calculate_job_duration_by_user(earlier_job).values()) / 3600
    if earlier_hours <= 0 or earlier_job.total is None:
        return None, False
    job_rate = earlier_job.total / _to_decimal(earlier_hours)

    later_hours_by_user = calculate_job_duration_by_user(later_job)
    later_hours = sum(later_hours_by_user.values()) / 3600
    if later_hours_by_user and later_hours > 0:
        return job_rate * _to_decimal(later_hours), False

    # No logged hours on the later job -- same scheduled-time fallback as
    # detect_and_freeze_callbacks(), reusing its EXACT existing live
    # fetch (fetch_job_visits_for_callback_detection already carries
    # startAt/endAt per visit -- no new query needed for this).
    try:
        visits_raw = client.fetch_job_visits_for_callback_detection(account, later_job.jobber_id)
    except client.JobberAPIError:
        return None, False
    if not visits_raw:
        return None, False

    latest_visit_raw = max(visits_raw, key=lambda v: v.get('createdAt') or '')
    scheduled_start = _to_datetime(latest_visit_raw.get('startAt'))
    scheduled_end = _to_datetime(latest_visit_raw.get('endAt'))
    if not (scheduled_start and scheduled_end and scheduled_end > scheduled_start):
        return None, False

    scheduled_hours = (scheduled_end - scheduled_start).total_seconds() / 3600
    if scheduled_hours > CALLBACK_SCHEDULED_HOURS_CEILING:
        return None, False

    return job_rate * _to_decimal(scheduled_hours), True


def detect_cross_job_callbacks(account, tenant):
    """
    Called once per sync_tenant() pass (see that function's own call
    site) -- NOT a one-shot "just transitioned" trigger like
    detect_and_freeze_callbacks(). Re-scans every real, currently
    UNRESOLVED job for this tenant every pass, because a candidate job's
    own real text can gain a keyword match at any point during its real
    30-day window (a technician often doesn't type "no charge" until
    closing the job out) -- a one-time check at creation would miss
    that. Every job this function ever examines ends in exactly one
    of 3 real states: still open (re-checked next pass), matched
    (frozen permanently), or window-expired with no match (also frozen
    permanently, so a genuinely non-matching job isn't re-checked
    forever) -- see JobberJob.cross_job_callback_resolved's own model
    comment.

    Deliberately filters candidates on cross_job_callback_resolved=False
    ALONE, not also on a recent-creation-date bound -- a job must stay
    eligible to be examined right up until its own window has elapsed,
    so THIS function is what finalizes it; pre-filtering by date here
    would let an old, still-unresolved job silently age out of ever
    being selected again without ever being marked resolved.

    Not optimized for scale (loads every active job for the tenant into
    memory once per pass, same as _gather_job_assignees()'s own
    documented tradeoff elsewhere in this codebase) -- fine at this
    project's current real data volume; revisit if that changes.
    """
    all_active_jobs = list(JobberJob.objects.filter(tenant=tenant, is_active=True))
    candidates = [job for job in all_active_jobs if not job.cross_job_callback_resolved]

    matched = 0
    resolved = 0
    for job in candidates:
        match = _find_earlier_match(job, all_active_jobs)
        if match is not None:
            amount, is_estimated = _compute_cross_job_bled_amount(account, match, job)
            job.cross_job_callback_of = match
            job.cross_job_callback_resolved = True
            job.cross_job_callback_bled_amount = amount
            job.cross_job_callback_bled_amount_is_estimated = is_estimated
            job.save(update_fields=[
                'cross_job_callback_of',
                'cross_job_callback_resolved',
                'cross_job_callback_bled_amount',
                'cross_job_callback_bled_amount_is_estimated',
            ])
            matched += 1
            resolved += 1
        elif job.jobber_created_at and timezone.now() - job.jobber_created_at > timedelta(days=CROSS_JOB_CALLBACK_WINDOW_DAYS):
            job.cross_job_callback_resolved = True
            job.save(update_fields=['cross_job_callback_resolved'])
            resolved += 1

    return {'checked': len(candidates), 'matched': matched, 'resolved': resolved}

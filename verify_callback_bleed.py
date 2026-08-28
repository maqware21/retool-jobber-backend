"""
Verification-only: real Callback Bleed math for the confirmed real test
case (2026-08-28) -- Job #3, tenant_id=4 (2 visits; the original invoiced
$1,200 with real logged hours; the callback -- createdAt later --
invoice: null). No models, no endpoint, no frontend yet -- this proves
the math against real data, same as every other new calculation in this
project.

Real schema gap, confirmed and worth stating plainly before the numbers:
JobberTimeSheetEntry (the LOCAL model) has NO visit linkage at all today
-- confirmed against apps/jobber/models.py: it stores
tenant/job/user/jobber_id/final_duration_seconds/started_at/ended_at/
synced_at, nothing else. Yet Jobber's own live schema DOES expose this
directly: TimeSheetEntry.visit is a real, confirmed field (checked via
introspection against jobber_graphql_schema.json this round). So this
script pulls the job's real timesheet entries LIVE, each with its real
`visit { id }` attached, rather than from local tables -- the local
model genuinely cannot answer "which visit was this hour logged
against" yet, that's a real, confirmed limitation, not a detail being
glossed over.

Proposed smallest correct change to calculate_job_duration_by_user()
(apps/jobber/api/electricians_summary.py) -- NOT applied this round, per
"no permanent build yet", and blocked on the schema gap above:

    def calculate_job_duration_by_user(job, exclude_visit_ids=None):
        entries = list(job.timesheet_entries.filter(is_active=True))
        if exclude_visit_ids:
            entries = [e for e in entries if e.visit_id not in exclude_visit_ids]
        if not entries:
            return {}
        ...  # everything below, including _merge_intervals(), UNCHANGED

One new optional parameter, one filter line ahead of the existing merge
loop -- _merge_intervals() and every line after it stay byte-for-byte
the same, exactly as asked. This needs JobberTimeSheetEntry.visit (a new
nullable FK to JobberVisit, populated by widening the sync's
timeSheetEntries selection to include `visit { id }`) added first --
`entry.visit_id` is not a real field today. That's a real model +
migration + sync change, genuinely out of scope for this "no models
yet" verification round.

This script proves the SAME merge math in the meantime -- _merge_intervals
is imported directly, completely unmodified, from electricians_summary.py
-- against real LIVE data instead of local tables, so the real numbers
can be sanity-checked now without waiting on that model change.

Scope note: the nested timeSheetEntries/visits connections on the detail
query below are fetched as a single page each (100 / 25), matching this
script's one-real-job scale -- not full-pagination-safe for a job with
more entries than that. Flag, don't silently truncate, if that cap is
ever hit for a real test job.

FIX (2026-08-28) -- real root cause confirmed with hard evidence from the
first version's own new requestedQueryCost logging: the original single
job-search query paginated the FULL account fetching nested visit +
timesheet-entry detail for EVERY job on EVERY page, just to find the one
job matching jobNumber == 3 -- a cost that scales with total account
volume, not with the one job actually needed. Confirmed: requestedCost
28330 against a maximumAvailable of 10000, actualQueryCost=0 (rejected
outright before running at all -- not a timing/retry issue, the query
itself was too expensive as written).

Split into two queries, per the confirmed fix:
  1. _JOB_SEARCH_QUERY -- id + jobNumber ONLY, paginated the same way,
     used purely to find which job's real id matches jobNumber == 3.
     Cheap by construction -- no nested connections at all.
  2. _JOB_DETAIL_QUERY -- Query.job(id: EncodedId!) (confirmed real,
     singular root field via introspection), called exactly ONCE with
     the real id the search step found, pulling the full visit +
     timesheet-entry detail this verification actually needs. Real cost
     paid once, for exactly the one job that matters -- not once per job
     checked along the way. This also resolves the earlier "no confirmed
     way to construct an EncodedId from the raw dashboard number"
     concern from the prior verify_callback_visit_invoice.py round --
     the id here comes directly from Jobber's own search response, never
     guessed at or derived.
"""
from datetime import timedelta

from dateutil.parser import isoparse

from apps.jobber.api.electricians_summary import _merge_intervals
from apps.jobber.models import JobberAccount
from apps.jobber.services.client import FETCH_ALL_MAX_PAGES, FETCH_ALL_PAGE_SIZE, JobberAPIError, execute

TENANT_ID = 4
TARGET_JOB_NUMBER = 3

_JOB_SEARCH_QUERY = """
query FindJobByNumber($first: Int!, $after: String) {
  jobs(first: $first, after: $after) {
    nodes {
      id
      jobNumber
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

_JOB_DETAIL_QUERY = """
query GetJobDetailById($id: EncodedId!) {
  job(id: $id) {
    id
    jobNumber
    jobStatus
    total
    visits(first: 25) {
      nodes {
        id
        createdAt
        invoice { id total }
      }
    }
    timeSheetEntries(first: 100) {
      nodes {
        id
        startAt
        endAt
        finalDuration
        user { id name { full } }
        visit { id }
      }
    }
  }
}
"""


def _find_job_id_by_number(account, job_number):
    """
    Cheap search only -- id + jobNumber, no nested detail at all. No
    jobNumber filter exists in JobFilterAttributes (confirmed against the
    schema), so this still paginates and matches client-side, but each
    page now costs a small, fixed amount regardless of how much visit/
    timesheet history any given job carries.
    """
    cursor = None
    for _page_num in range(FETCH_ALL_MAX_PAGES):
        data = execute(account, _JOB_SEARCH_QUERY, {'first': FETCH_ALL_PAGE_SIZE, 'after': cursor})
        jobs = (data or {}).get('jobs') or {}
        for node in jobs.get('nodes') or []:
            if node.get('jobNumber') == job_number:
                return node.get('id')
        page_info = jobs.get('pageInfo') or {}
        if not page_info.get('hasNextPage'):
            break
        cursor = page_info.get('endCursor')
    return None


def _fetch_job_detail(account, job_id):
    """One call, one job, by its real id -- pays the full visit/
    timesheet-entry cost exactly once."""
    data = execute(account, _JOB_DETAIL_QUERY, {'id': job_id})
    return (data or {}).get('job')


def _entries_to_seconds_by_user(entries):
    """
    Mirrors calculate_job_duration_by_user()'s own per-user grouping +
    _merge_intervals() call EXACTLY -- same algorithm -- over raw live
    GraphQL entry dicts instead of JobberTimeSheetEntry ORM rows, since
    there's no local `.user_id`/`.started_at` etc. to read for entries
    whose visit attribution only exists live (see module docstring).
    """
    by_user = {}
    for i, entry in enumerate(entries):
        user = entry.get('user') or {}
        user_id = user.get('id')
        group_key = user_id if user_id is not None else f'_no_user_{entry.get("id", i)}'
        by_user.setdefault(group_key, []).append(entry)

    per_user_totals = {}
    for group_key, user_entries in by_user.items():
        seconds = 0
        intervals = []
        for entry in user_entries:
            start_raw = entry.get('startAt')
            start = isoparse(start_raw) if start_raw else None
            if start is None:
                seconds += entry.get('finalDuration') or 0
                continue
            end_raw = entry.get('endAt')
            end = isoparse(end_raw) if end_raw else start + timedelta(seconds=entry.get('finalDuration') or 0)
            intervals.append((start, end))
        seconds += _merge_intervals(intervals)
        per_user_totals[group_key] = seconds
    return per_user_totals


account = JobberAccount.objects.filter(tenant_id=TENANT_ID, is_active=True).first()
print("account:", account)

if account is None:
    print(f"No active JobberAccount for tenant_id={TENANT_ID} -- cannot query live.")
else:
    job_id = None
    job = None
    try:
        print(f"\nStep A: cheap search for job #{TARGET_JOB_NUMBER}'s real id (id+jobNumber only)...")
        job_id = _find_job_id_by_number(account, TARGET_JOB_NUMBER)
        if job_id is None:
            print(f"Job #{TARGET_JOB_NUMBER} not found for tenant_id={TENANT_ID}.")
        else:
            print(f"Found id={job_id}. Step B: fetching full detail for exactly this one job...")
            job = _fetch_job_detail(account, job_id)
    except JobberAPIError as exc:
        print(f"Jobber API error while locating/fetching job #{TARGET_JOB_NUMBER}: {exc}")

    if job is None:
        print(f"\nCould not obtain job #{TARGET_JOB_NUMBER}'s detail for tenant_id={TENANT_ID}.")
    else:
        job_total = job.get('total')
        print(f"\n=== Job #{job.get('jobNumber')} (id={job.get('id')}) ===")
        print(f"jobStatus: {job.get('jobStatus')}   Job.total: {job_total}")

        visits = (job.get('visits') or {}).get('nodes') or []
        if len(visits) != 2:
            print(f"NOTE: expected exactly 2 visits for this confirmed test case, found {len(visits)}. Reporting as observed.")
        visits_by_created = sorted(visits, key=lambda v: v.get('createdAt') or '')
        print(f"\n--- Visits, by createdAt ({len(visits_by_created)} total) ---")
        for i, v in enumerate(visits_by_created):
            print(f"  visit[{i}] id={v.get('id')} createdAt={v.get('createdAt')} invoice={v.get('invoice')}")

        callback_visit = visits_by_created[-1] if visits_by_created else None
        callback_visit_id = callback_visit.get('id') if callback_visit else None
        print(
            f"\nCallback visit (createdAt-latest): id={callback_visit_id}, "
            f"invoice={callback_visit.get('invoice') if callback_visit else None}"
        )

        entries = (job.get('timeSheetEntries') or {}).get('nodes') or []
        print(f"\n{len(entries)} real timesheet entries pulled for this job.")

        original_entries, callback_entries, unattributed_entries = [], [], []
        for e in entries:
            visit = e.get('visit')
            visit_id = visit.get('id') if visit else None
            if visit_id is None:
                unattributed_entries.append(e)
            elif visit_id == callback_visit_id:
                callback_entries.append(e)
            else:
                original_entries.append(e)

        if unattributed_entries:
            print(
                f"\nWARNING: {len(unattributed_entries)} entries have NO visit link at all "
                "(visit: null) -- cannot attribute them to original vs callback; excluded from "
                "BOTH totals below, reported separately, not silently dropped:"
            )
            for e in unattributed_entries:
                print(f"    id={e.get('id')} user={e.get('user')} finalDuration={e.get('finalDuration')}")

        print(f"\n--- Original-visit entries ({len(original_entries)}) ---")
        for e in original_entries:
            print(
                f"    id={e.get('id')} user={(e.get('user') or {}).get('id')} "
                f"startAt={e.get('startAt')} endAt={e.get('endAt')} finalDuration={e.get('finalDuration')}"
            )

        print(f"\n--- Callback-visit entries ({len(callback_entries)}) ---")
        for e in callback_entries:
            print(
                f"    id={e.get('id')} user={(e.get('user') or {}).get('id')} "
                f"startAt={e.get('startAt')} endAt={e.get('endAt')} finalDuration={e.get('finalDuration')}"
            )

        original_by_user = _entries_to_seconds_by_user(original_entries)
        callback_by_user = _entries_to_seconds_by_user(callback_entries)

        original_total_seconds = sum(original_by_user.values())
        callback_total_seconds = sum(callback_by_user.values())

        original_hours = original_total_seconds / 3600
        callback_hours = callback_total_seconds / 3600

        print("\n=== Step 1: job-specific rate ===")
        print(f"Original-visit total real hours (merged, per-user then summed): {original_hours:.4f}h")
        print(f"Per-user breakdown (seconds): {original_by_user}")
        job_rate = None
        if job_total is None or original_hours <= 0:
            print(
                "Cannot compute a real rate -- Job.total is missing or original hours are "
                "zero. Reporting real inputs as-is, not fabricating a rate."
            )
        else:
            job_rate = job_total / original_hours
            print(f"Job-specific rate = {job_total} / {original_hours:.4f}h = ${job_rate:.2f}/hr")

        print("\n=== Step 2: Callback Bleed ===")
        print(f"Callback-visit total real hours (merged, per-user then summed): {callback_hours:.4f}h")
        print(f"Per-user breakdown (seconds): {callback_by_user}")
        if job_rate is None or callback_hours <= 0:
            print("Cannot compute Callback Bleed -- missing rate or zero callback hours. Reporting real inputs as-is.")
        else:
            callback_bleed = callback_hours * job_rate
            print(f"Callback Bleed = {callback_hours:.4f}h x ${job_rate:.2f}/hr = ${callback_bleed:.2f}")

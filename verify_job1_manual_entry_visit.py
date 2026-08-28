"""
Quick check (2026-08-28): does Job #1's existing real timesheet entry
carry a real visit link, or is it null too -- same as what was seen on
Job #3's callback visit? No new test data needed.

Tests the new hypothesis: a MANUALLY-ADDED time entry (via a job's own
"Add Time Entry" button, not a live timer run from within a specific
visit) may never carry a visit link at all -- regardless of whether the
job was ever reopened. If true, a null Visit.invoice/TimeSheetEntry.visit
is NOT exclusively a "this came from a genuine callback" signal -- it
could also just mean "this entry was added manually," a real, distinct
confound worth knowing before trusting that field as a clean callback
detector.

Looks up Job #1's real jobber_id from the LOCAL JobberJob table (already
synced in an earlier round -- see electricians_summary.py's own
docstrings referencing real Jobs 1, 2, 5, 6, 7, 12) rather than a live
full-account search -- avoids the exact "expensive full-account scan"
mistake verify_callback_bleed.py's first version made. Uses the same
Query.job(id: EncodedId!) single-job lookup already proven cheap this
round, requesting ONLY timeSheetEntries (visits/invoice aren't needed
for this specific question).

Defaults to tenant_id=1 -- the original connected test tenant Job #1/2/
5/6/7/12 belong to, distinct from tenant_id=4 (the separate real test
case used for the callback-heuristic R&D). Prints exactly which local
JobberJob row was found, so this isn't a silent guess.
"""
from apps.jobber.models import JobberAccount, JobberJob
from apps.jobber.services.client import JobberAPIError, execute

TENANT_ID = 1
TARGET_JOB_NUMBER = 1

_JOB_ENTRIES_QUERY = """
query GetJobTimesheetEntriesById($id: EncodedId!) {
  job(id: $id) {
    id
    jobNumber
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

local_job = JobberJob.objects.filter(tenant_id=TENANT_ID, job_number=TARGET_JOB_NUMBER, is_active=True).first()
print(f"local JobberJob row: {local_job}  jobber_id={local_job.jobber_id if local_job else None}")

if local_job is None:
    print(
        f"No locally-synced JobberJob found for tenant_id={TENANT_ID}, "
        f"job_number={TARGET_JOB_NUMBER} -- cannot proceed without guessing an id."
    )
else:
    account = JobberAccount.objects.filter(tenant_id=TENANT_ID, is_active=True).first()
    print("account:", account)

    if account is None:
        print(f"No active JobberAccount for tenant_id={TENANT_ID} -- cannot query live.")
    else:
        job = None
        try:
            data = execute(account, _JOB_ENTRIES_QUERY, {'id': local_job.jobber_id})
            job = (data or {}).get('job')
        except JobberAPIError as exc:
            print(f"Jobber API error fetching job #{TARGET_JOB_NUMBER}: {exc}")

        if job is None:
            print(f"Live job(id=...) returned null for jobber_id={local_job.jobber_id} -- id may be stale.")
        else:
            entries = (job.get('timeSheetEntries') or {}).get('nodes') or []
            print(
                f"\n=== Job #{job.get('jobNumber')} (id={job.get('id')}) -- "
                f"{len(entries)} real timesheet entr{'y' if len(entries) == 1 else 'ies'} ==="
            )
            for e in entries:
                print(f"\nentry id={e.get('id')}")
                print(f"    user:          {e.get('user')}")
                print(f"    startAt:       {e.get('startAt')}")
                print(f"    endAt:         {e.get('endAt')}")
                print(f"    finalDuration: {e.get('finalDuration')}")
                print(f"    visit:         {e.get('visit')!r}")

            print("\n=== The question this script exists to answer ===")
            null_visit_entries = [e for e in entries if e.get('visit') is None]
            if not entries:
                print("No real timesheet entries found on this job -- cannot test the hypothesis here.")
            elif null_visit_entries:
                print(
                    f"{len(null_visit_entries)}/{len(entries)} real entries show visit: null on Job #1 -- "
                    "real evidence supporting the hypothesis that a null Visit link is NOT exclusive to "
                    "callback visits; it can also mean 'this entry was added manually,' independent of any "
                    "reopen/callback history."
                )
            else:
                print(
                    "Every real entry on this job DOES carry a real visit link -- no support here for the "
                    "manually-added-entries-are-visit-less hypothesis, at least on this one job. Doesn't rule "
                    "it out elsewhere, just not reproduced here."
                )

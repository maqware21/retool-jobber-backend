"""
PART B verification (2026-08-30) -- does JobberJob.completed_at reflect
the ORIGINAL archival, or does a later reopen/re-archive cycle change it?
Run via `python manage.py shell < verify_completed_at_reopen_behavior.py`.

Why this matters: get_technician_stats()'s archived_jobs population (and
therefore the proposed callback_visits_done/callback_rate/
callback_dollars_lost per-technician stats) filters on
`completed_at__gte=period_start` (a rolling PERIOD_MONTHS window). If
completed_at reflects the CURRENT/latest archival event, a job reopened
recently stays inside the window regardless of how old the original work
was -- safe for this feature. If it instead reflects the ORIGINAL
archival/invoice and is never updated by a later reopen, a job whose
original work is now older than the window, but which got a genuine
recent callback, would be silently EXCLUDED from the population this
proposal's stats are computed over -- a real correctness gap, not
cosmetic.

Real test case: Job #3 (tenant_id=4) has a confirmed real reopen history
-- first visit completed+invoiced, job reopened, second visit added (the
real callback, no new invoice), job re-archived. Compares the job's own
completedAt against each of its two real visits' own completedAt to
determine which one it actually tracks. Jobs #5/#6 (same tenant) are
checked too for comparison, though neither has a confirmed real reopen
history.

No detection logic changed by this script -- read-only, reports the real
timestamps plainly.
"""
from apps.jobber.models import JobberAccount, JobberJob
from apps.jobber.services.client import JobberAPIError, execute

TENANT_ID = 4
TARGET_JOB_NUMBERS = (3, 5, 6)

_VISIT_TIMING_QUERY = """
query GetJobVisitTimingForCompletedAtCheck($id: EncodedId!) {
  job(id: $id) {
    id
    jobNumber
    jobStatus
    completedAt
    visits(first: 25) {
      nodes {
        id
        createdAt
        completedAt
        invoice { id }
      }
    }
  }
}
"""

account = JobberAccount.objects.filter(tenant_id=TENANT_ID, is_active=True).first()
print("account:", account)

for job_number in TARGET_JOB_NUMBERS:
    local_job = JobberJob.objects.filter(tenant_id=TENANT_ID, job_number=job_number, is_active=True).first()
    if local_job is None:
        print(f"\nJob #{job_number}: no local row for tenant_id={TENANT_ID}.")
        continue

    print(f"\n=== Job #{job_number} (jobber_id={local_job.jobber_id}) ===")
    print(f"Local JobberJob.completed_at:      {local_job.completed_at}")
    print(f"Local JobberJob.first_archived_at: {local_job.first_archived_at}")

    if account is None:
        continue
    try:
        data = execute(account, _VISIT_TIMING_QUERY, {'id': local_job.jobber_id})
    except JobberAPIError as exc:
        print(f"Jobber API error: {exc}")
        continue

    job = (data or {}).get('job')
    if job is None:
        print("Live job(id=...) returned null.")
        continue

    print(f"Live jobStatus:    {job.get('jobStatus')}")
    print(f"Live Job.completedAt: {job.get('completedAt')}")

    visits = (job.get('visits') or {}).get('nodes') or []
    visits_sorted = sorted(visits, key=lambda v: v.get('createdAt') or '')
    print(f"\n{len(visits_sorted)} real visit(s), by createdAt:")
    for i, v in enumerate(visits_sorted):
        print(
            f"  visit[{i}] id={v.get('id')} createdAt={v.get('createdAt')} "
            f"completedAt={v.get('completedAt')} invoice={v.get('invoice')}"
        )

    if len(visits_sorted) >= 2:
        original_visit = visits_sorted[0]
        latest_visit = visits_sorted[-1]
        job_completed = job.get('completedAt')
        orig_completed = original_visit.get('completedAt')
        latest_completed = latest_visit.get('completedAt')

        print("\n--- Reasoning ---")
        print(f"Original (createdAt-earliest) visit completedAt: {orig_completed}")
        print(f"Latest (createdAt-latest) visit completedAt:      {latest_completed}")
        print(f"Job.completedAt:                                  {job_completed}")

        if job_completed and orig_completed and job_completed == orig_completed:
            print(
                "Job.completedAt EXACTLY MATCHES the ORIGINAL (earliest) visit's completedAt "
                "-- reflects the ORIGINAL archival, NOT updated by the later reopen/re-archive."
            )
        elif job_completed and latest_completed and job_completed == latest_completed:
            print(
                "Job.completedAt EXACTLY MATCHES the LATEST visit's completedAt "
                "-- reflects the CURRENT/most recent (re-)archival."
            )
        else:
            print(
                "Job.completedAt does not exactly match either visit's completedAt -- "
                "report the raw values above for manual comparison (e.g. it may track "
                "invoice issuance timing instead of either visit's own completion)."
            )
    else:
        print("\nFewer than 2 visits -- no reopen history to compare against for this job.")

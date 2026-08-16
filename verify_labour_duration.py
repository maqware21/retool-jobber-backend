"""
Verification pass for the "Avg Job Duration" cutover — all 3 steps in one
script, since Steps 2-3 only make sense if Step 1 finds real data.

Run via `python manage.py shell < verify_labour_duration.py`.

STEP 1 — pure local query, no live call: does JobberJob.labour_duration_seconds
have real non-zero values now, for tenant_id=1? (Last known state, before
richer test data existed: all zero.)

STEP 2 — only if Step 1 finds non-zero values: for up to 2 of those jobs,
make a LIVE GraphQL call for that job's real TimeSheetEntry records via
Job.timeSheetEntries (a direct field on Job — no need to go through
Visits), summing the real finalDuration (Seconds!, confirmed in the
schema) across all of that job's entries. Also re-fetches the job's live
jobCosting.labourDuration alongside it, as an extra freshness check
against the already-synced local labour_duration_seconds value.

STEP 3 — reports the real numbers side by side for each job checked:
local labour_duration_seconds (already synced) vs. live jobCosting.labourDuration
(re-fetched now) vs. summed live finalDuration across all TimeSheetEntry
records for that job. Whether they match or clearly diverge is reported
as real numbers, not a yes/no guess.
"""
import json

from apps.jobber.models import JobberAccount, JobberJob
from apps.jobber.services import client

# Standing instruction: always filter test-account lookups by tenant_id=1
# explicitly -- there are 2 real JobberAccount rows in this database
# (tenant_id=1 is the real, data-rich test account; tenant_id=3 belongs to
# a teammate and is nearly empty). Never .first() with no tenant filter.
account = JobberAccount.objects.filter(is_active=True, tenant_id=1).first()
tenant_id = account.tenant_id
print("tenant_id:", tenant_id)

jobs = JobberJob.objects.filter(tenant_id=tenant_id, is_active=True).order_by('job_number')
print(f"\n=== STEP 1: local labour_duration_seconds for all {jobs.count()} active jobs ===")

non_zero = [j for j in jobs if j.labour_duration_seconds and j.labour_duration_seconds > 0]
zero_or_null = [j for j in jobs if not j.labour_duration_seconds]
print(f"  - with a non-zero labour_duration_seconds: {len(non_zero)}")
print(f"  - with zero or null labour_duration_seconds: {len(zero_or_null)}")

for j in jobs:
    print(json.dumps({
        'job_number': j.job_number,
        'jobber_id': j.jobber_id,
        'job_status': j.job_status,
        'labour_duration_seconds': j.labour_duration_seconds,
        'labour_duration_hours': j.labour_duration_hours,
        'labour_cost': str(j.labour_cost) if j.labour_cost is not None else None,
    }, default=str))

if not non_zero:
    print("\nNo jobs with a non-zero labour_duration_seconds found -- stopping here. "
          "Steps 2-3 (live TimeSheetEntry cross-check) need at least one such job to check.")
else:
    _CHECK_QUERY = """
    query GetJobTimesheetCheck($id: EncodedId!) {
      job(id: $id) {
        id
        jobNumber
        jobCosting { labourDuration labourCost }
        timeSheetEntries(first: 25) {
          nodes {
            id
            startAt
            endAt
            duration
            finalDuration
            ticking
          }
        }
      }
    }
    """

    print("\n=== STEP 2 & 3: live TimeSheetEntry cross-check for up to 2 jobs ===")
    for job in non_zero[:2]:
        data = client.execute(account, _CHECK_QUERY, {'id': job.jobber_id})
        live_job = (data or {}).get('job') or {}
        live_costing = live_job.get('jobCosting') or {}
        entries = (live_job.get('timeSheetEntries') or {}).get('nodes') or []

        summed_final_duration = sum(e.get('finalDuration') or 0 for e in entries)

        print(json.dumps({
            'job_number': job.job_number,
            'jobber_id': job.jobber_id,
            'local_labour_duration_seconds (already synced)': job.labour_duration_seconds,
            'live_jobCosting_labourDuration (re-fetched now)': live_costing.get('labourDuration'),
            'live_jobCosting_labourCost (re-fetched now)': live_costing.get('labourCost'),
            'timeSheetEntries_count': len(entries),
            'summed_live_finalDuration_across_entries': summed_final_duration,
            'raw_entries': entries,
        }, indent=2, default=str))

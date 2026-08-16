"""
Direct TimeSheetEntry check, independent of jobCosting.labourDuration.

Run via `python manage.py shell < verify_timesheet_entries_direct.py`.

The previous script (verify_labour_duration.py) only cross-checked
TimeSheetEntry data for jobs where jobCosting already reported a non-zero
labour_duration_seconds -- none did, so that cross-check never actually
ran. This does NOT gate on labour_duration_seconds at all: it makes a
live call to job.timeSheetEntries(first: 25) for 2-3 real archived jobs
regardless, and reports exactly what comes back -- ignoring jobCosting
entirely this time, so a genuine TimeSheetEntry (even if jobCosting says
0) would show up here.
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

jobs = JobberJob.objects.filter(tenant_id=tenant_id, is_active=True, job_status='archived').order_by('job_number')[:3]
print(f"Checking {jobs.count()} archived jobs directly for real TimeSheetEntry data:")

_DIRECT_QUERY = """
query GetJobTimesheetEntriesDirect($id: EncodedId!) {
  job(id: $id) {
    id
    jobNumber
    timeSheetEntries(first: 25) {
      nodes {
        id
        startAt
        endAt
        duration
        finalDuration
        ticking
        user { id name { full } }
      }
    }
  }
}
"""

any_entries_found = False
for job in jobs:
    data = client.execute(account, _DIRECT_QUERY, {'id': job.jobber_id})
    live_job = (data or {}).get('job') or {}
    entries = (live_job.get('timeSheetEntries') or {}).get('nodes') or []
    if entries:
        any_entries_found = True

    print(json.dumps({
        'job_number': job.job_number,
        'jobber_id': job.jobber_id,
        'timeSheetEntries_count': len(entries),
        'raw_entries': entries,
    }, indent=2, default=str))

print("\n--- Summary ---")
if any_entries_found:
    print("At least one real TimeSheetEntry was found for a checked job -- "
          "compare this against that job's jobCosting.labourDuration (already "
          "confirmed 0) to see whether that's a real discrepancy.")
else:
    print("Genuinely empty for every job checked -- confirms no time was ever "
          "logged for these jobs at all. Not a bug anywhere; just missing from "
          "the mock/test data.")

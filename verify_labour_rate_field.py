"""
Separate, quick check (2026-08-30) -- NOT part of the callback-tracking
build, nothing built on this yet either way. Run via
`python manage.py shell < verify_labour_rate_field.py`.

TimeSheetEntry.labourRate is a real field (confirmed via schema
introspection), never checked against real data before. jobCosting.
labourCost is already confirmed broken/always-0 on this account -- this is
a DIFFERENT field on a DIFFERENT object (a per-entry rate, not a per-job
cost rollup), so that finding doesn't tell us anything about this one.

Looks up Job #1's real jobber_id from the LOCAL JobberJob table (same
approach as verify_job1_manual_entry_visit.py -- avoids guessing an id or
doing an expensive full-account scan) and queries that job's real
timeSheetEntries directly for id/finalDuration/labourRate. Reports the
raw values plainly -- populated or null -- and nothing else.
"""
from apps.jobber.models import JobberAccount, JobberJob
from apps.jobber.services.client import JobberAPIError, execute

TENANT_ID = 4
TARGET_JOB_NUMBER = 1

_LABOUR_RATE_QUERY = """
query GetJobTimesheetEntriesForLabourRateCheck($id: EncodedId!) {
  job(id: $id) {
    id
    jobNumber
    timeSheetEntries(first: 100) {
      nodes {
        id
        finalDuration
        labourRate
      }
    }
  }
}
"""

local_job = JobberJob.objects.filter(tenant_id=TENANT_ID, job_number=TARGET_JOB_NUMBER, is_active=True).first()
print(f"local JobberJob row: {local_job}  jobber_id={local_job.jobber_id if local_job else None}")

if local_job is None:
    print(f"No locally-synced JobberJob found for tenant_id={TENANT_ID}, job_number={TARGET_JOB_NUMBER}.")
else:
    account = JobberAccount.objects.filter(tenant_id=TENANT_ID, is_active=True).first()
    print("account:", account)

    if account is None:
        print(f"No active JobberAccount for tenant_id={TENANT_ID} -- cannot query live.")
    else:
        job = None
        try:
            data = execute(account, _LABOUR_RATE_QUERY, {'id': local_job.jobber_id})
            job = (data or {}).get('job')
        except JobberAPIError as exc:
            print(f"Jobber API error fetching job #{TARGET_JOB_NUMBER}: {exc}")

        if job is None:
            print(f"Live job(id=...) returned null for jobber_id={local_job.jobber_id}.")
        else:
            entries = (job.get('timeSheetEntries') or {}).get('nodes') or []
            print(f"\n=== Job #{job.get('jobNumber')} -- {len(entries)} real timesheet entr{'y' if len(entries) == 1 else 'ies'} ===")
            populated = 0
            for e in entries:
                rate = e.get('labourRate')
                if rate is not None:
                    populated += 1
                print(f"  entry id={e.get('id')} finalDuration={e.get('finalDuration')} labourRate={rate!r}")

            print(f"\n{populated}/{len(entries)} real entries have a non-null labourRate.")

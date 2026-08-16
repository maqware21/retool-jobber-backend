"""
Step 3 verification for Part B: computes calculate_job_duration_seconds()
directly against the ALREADY-SYNCED real JobberTimeSheetEntry rows from
the previous round's verify_timesheet_entries_synced.py run, and reports
the actual computed values -- not asserted -- for manual confirmation.

Run via `python manage.py shell < verify_avg_job_duration_calc.py`.

Pure local computation -- no live Jobber call, no sync needed. Uses
whatever JobberTimeSheetEntry rows are already in the database right now.

Expected (from the already-confirmed real rows, per the last round's
verify_timesheet_entries_synced.py output):
  - job_number=2: two overlapping entries for the same user (Waseem Farhad),
    04:00-08:00 (14400s) and 04:00-09:00 (18000s) -- merged union is
    04:00-09:00 = 18000s, NOT the naive sum 32400s.
  - job_number=1,5,6,7,12: single entries each, no overlap possible --
    expect each job's own final_duration_seconds unchanged.

Also computes and reports what avg_job_duration_seconds would be for
tenant_id=1's current real data, using the exact same archived-jobs +
6-month window as jobs_completed, and the exact same
calculate_job_duration_seconds() the endpoint calls -- so the real number
is known before the endpoint is ever hit for real.
"""
import json

from dateutil.relativedelta import relativedelta
from django.utils import timezone

from apps.jobber.api.electricians_summary import PERIOD_MONTHS, calculate_job_duration_seconds
from apps.jobber.models import JobberAccount, JobberJob

# Standing instruction: always filter test-account lookups by tenant_id=1
# explicitly -- there are 2 real JobberAccount rows in this database
# (tenant_id=1 is the real, data-rich test account; tenant_id=3 belongs to
# a teammate and is nearly empty). Never .first() with no tenant filter.
account = JobberAccount.objects.filter(is_active=True, tenant_id=1).first()
tenant_id = account.tenant_id
print("tenant_id:", tenant_id)

print("\n=== calculate_job_duration_seconds() against real synced rows, per known job ===")
for job_number in (1, 2, 5, 6, 7, 12):
    job = JobberJob.objects.filter(tenant_id=tenant_id, is_active=True, job_number=job_number).first()
    if job is None:
        print(f"job_number={job_number}: NOT FOUND locally")
        continue
    raw_entries = list(
        job.timesheet_entries.filter(is_active=True)
        .values('jobber_id', 'user_id', 'started_at', 'ended_at', 'final_duration_seconds')
    )
    result = calculate_job_duration_seconds(job)
    print(json.dumps({
        'job_number': job_number,
        'raw_entries': raw_entries,
        'computed_duration_seconds': result,
    }, indent=2, default=str))

print("\n=== Full avg_job_duration_seconds computation, same population as jobs_completed ===")
period_start = timezone.now() - relativedelta(months=PERIOD_MONTHS)
archived_jobs = JobberJob.objects.filter(
    tenant_id=tenant_id, is_active=True, job_status='archived', completed_at__gte=period_start,
)
print(f"Archived jobs in the {PERIOD_MONTHS}-month window: {archived_jobs.count()}")

per_job = []
for job in archived_jobs:
    d = calculate_job_duration_seconds(job)
    per_job.append({'job_number': job.job_number, 'duration_seconds': d})
    print(json.dumps({'job_number': job.job_number, 'duration_seconds': d}, default=str))

durations = [p['duration_seconds'] for p in per_job if p['duration_seconds'] is not None]
avg = round(sum(durations) / len(durations)) if durations else None
print(json.dumps({
    'jobs_with_at_least_1_entry': len(durations),
    'jobs_excluded_zero_entries': len(per_job) - len(durations),
    'avg_job_duration_seconds': avg,
    'avg_job_duration_hours': round(avg / 3600, 2) if avg is not None else None,
}, indent=2, default=str))

"""
Step 4 verification for Part A of the "Avg Job Duration" work: does
sync_timesheet_entries() correctly land real TimeSheetEntry data into the
new local JobberTimeSheetEntry table?

Run via `python manage.py shell < verify_timesheet_entries_synced.py`.

Runs a real sync_tenant() (full sync -- Clients/Users/Jobs/Visits/
Invoices/TimeSheetEntries) for tenant_id=1, then reports every
JobberTimeSheetEntry row now stored locally, with special attention to:
  - Job 2, which should show EXACTLY 2 raw entries (both stored, NOT
    merged -- merging is explicitly Part B's job, not this step's) --
    the confirmed real data-entry mistake (same technician, same
    04:00 start, different 08:00/09:00 ends).
  - The other 5 known jobs with real time data from the earlier live
    check (1, 5, 6, 7, 12), confirming the right technician and the
    right start/end times landed for each.
"""
import json

from apps.jobber.models import JobberAccount, JobberTimeSheetEntry
from apps.jobber.services.sync import sync_tenant

# Standing instruction: always filter test-account lookups by tenant_id=1
# explicitly -- there are 2 real JobberAccount rows in this database
# (tenant_id=1 is the real, data-rich test account; tenant_id=3 belongs to
# a teammate and is nearly empty). Never .first() with no tenant filter.
account = JobberAccount.objects.filter(is_active=True, tenant_id=1).first()
tenant_id = account.tenant_id
print("tenant_id:", tenant_id)

print("\n=== Running a full sync_tenant() (includes the new timesheet_entries entity) ===")
run = sync_tenant(account)
print(json.dumps({
    'status': run.status,
    'error_message': run.error_message,
    'clients_synced': run.clients_synced,
    'users_synced': run.users_synced,
    'jobs_synced': run.jobs_synced,
    'visits_synced': run.visits_synced,
    'invoices_synced': run.invoices_synced,
    # No timesheet_entries_synced column on JobberSyncRun -- not added
    # this round (Part A's scope was the new entity + sync logic only).
    # The real row counts are queried directly from JobberTimeSheetEntry
    # below instead.
}, indent=2, default=str))

entries = JobberTimeSheetEntry.objects.filter(tenant_id=tenant_id, is_active=True).select_related('job', 'user').order_by('job__job_number', 'started_at')
print(f"\n=== Every active JobberTimeSheetEntry row now stored: {entries.count()} total ===")
for e in entries:
    print(json.dumps({
        'job_number': e.job.job_number,
        'entry_jobber_id': e.jobber_id,
        'user_name': e.user.name if e.user else None,
        'started_at': str(e.started_at),
        'ended_at': str(e.ended_at),
        'final_duration_seconds': e.final_duration_seconds,
    }, default=str))

print("\n=== Job 2 specifically -- must show EXACTLY 2 raw entries, unmerged ===")
job2_entries = JobberTimeSheetEntry.objects.filter(
    tenant_id=tenant_id, is_active=True, job__job_number=2,
).select_related('user').order_by('started_at')
print(f"Job 2 entry count: {job2_entries.count()} (expected: 2)")
for e in job2_entries:
    print(json.dumps({
        'entry_jobber_id': e.jobber_id,
        'user_name': e.user.name if e.user else None,
        'started_at': str(e.started_at),
        'ended_at': str(e.ended_at),
        'final_duration_seconds': e.final_duration_seconds,
    }, default=str))

print("\n=== Row counts for the other 5 known jobs with real time data (1, 5, 6, 7, 12) ===")
for job_number in (1, 5, 6, 7, 12):
    count = JobberTimeSheetEntry.objects.filter(
        tenant_id=tenant_id, is_active=True, job__job_number=job_number,
    ).count()
    print(f"job_number={job_number}: {count} entr{'y' if count == 1 else 'ies'}")

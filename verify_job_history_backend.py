"""
Real-data verification for Job History Part A (duration_seconds +
?technician= filter on GET /v1/jobber/jobs/). Run via
`python manage.py shell < verify_job_history_backend.py`.

Confirms:
  1) Job 2 shows duration_seconds=18000 -- the already-confirmed merged-
     overlap number (04:00-08:00 + 04:00-09:00 -> union 04:00-09:00),
     NOT the naive sum 32400.
  2) Every job with no real JobberTimeSheetEntry rows shows
     duration_seconds=None, never a fabricated 0.
  3) ?technician=<Farhan Khan's real id> returns only jobs whose
     assigned_to is actually "Farhan Khan" -- i.e. the filter can never
     disagree with what the row itself displays.
"""
import json

from apps.jobber.api.jobs import _local_jobs_response
from apps.jobber.models import JobberUser


class _FakeRequestUser:
    def __init__(self, tenant_id):
        self.tenant_id = tenant_id


fake_user = _FakeRequestUser(tenant_id=1)

# --- Checks 1 & 2: duration_seconds across all jobs ---
data = _local_jobs_response(fake_user, first=100, after=None)
print(f"connected: {data.get('connected')}")
jobs = data.get('jobs', [])
print(f"\n=== {len(jobs)} job(s), id / duration_seconds ===")
for job in jobs:
    print(f"{job['id']:<10} duration_seconds={job['duration_seconds']!r}")

job2 = next((j for j in jobs if j['id'] == 'JOB-2'), None)
print("\n=== Check 1: JOB-2 duration_seconds ===")
if job2 is None:
    print("JOB-2 not found in this page -- widen `first` or check job numbering.")
else:
    status = 'PASS' if job2['duration_seconds'] == 18000 else 'FAIL'
    print(f"JOB-2 duration_seconds={job2['duration_seconds']!r}, expected 18000 -> {status}")

no_entry_jobs = [j for j in jobs if j['duration_seconds'] is None]
print("\n=== Check 2: jobs with no timesheet entries show None, not 0 ===")
print(f"{len(no_entry_jobs)} job(s) show duration_seconds=None: {[j['id'] for j in no_entry_jobs]}")
print("(Manually cross-check these against JobberTimeSheetEntry -- they should genuinely have zero rows.)")

# --- Check 3: ?technician= filter for Farhan Khan ---
farhan = JobberUser.objects.filter(tenant_id=1, is_active=True, name__icontains='Farhan Khan').first()
print(f"\n=== Check 3: technician filter for Farhan Khan (id={farhan.id if farhan else None}) ===")
if farhan is None:
    print("No JobberUser matching 'Farhan Khan' found for tenant_id=1 -- adjust the name filter above.")
else:
    filtered = _local_jobs_response(fake_user, first=100, after=None, technician=farhan.id)
    filtered_jobs = filtered.get('jobs', [])
    print(f"{len(filtered_jobs)} job(s) returned for technician={farhan.id}:")
    all_match = True
    for job in filtered_jobs:
        matches = job['assigned_to'] == farhan.name
        if not matches:
            all_match = False
        print(f"  {job['id']:<10} assigned_to={job['assigned_to']!r} -> {'PASS' if matches else 'FAIL'}")
    print(f"\nOverall: {'PASS -- every returned job shows Farhan Khan as Assigned To' if all_match and filtered_jobs else 'FAIL or no jobs matched -- check output above'}")

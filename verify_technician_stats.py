"""
Part B verification: real per-technician stats and the phone sync
addition. Run via `python manage.py shell < verify_technician_stats.py`.

Covers:
  1) A real sync, then confirms JobberUser.phone actually populated
     (or genuinely null, for a real user with no phone on file in Jobber
     -- both are valid outcomes, only a request/response-level failure
     would not be).
  2) The real, distinct job_status values seen in this tenant's local
     data -- concrete, real-data confirmation (not just the schema check)
     that "cancelled" genuinely does not exist as a value here either.
  3) _local_technician_stats_response() against the real connected
     tenant -- actual per-technician numbers, not asserted. Report these
     back so they can be sanity-checked against what's visible in
     Jobber's own UI.
"""
import json

from apps.jobber.api.technician_stats import _local_technician_stats_response
from apps.jobber.models import JobberAccount, JobberJob, JobberUser
from apps.jobber.services.sync import sync_tenant


class _FakeRequestUser:
    """
    _local_technician_stats_response(user) only ever reads user.tenant_id
    (matching the real view's request.user) -- a minimal stand-in avoids
    this script depending on a specific customer-role User row existing.
    """
    def __init__(self, tenant_id):
        self.tenant_id = tenant_id

# Standing instruction: always filter test-account lookups by tenant_id=1
# explicitly.
account = JobberAccount.objects.filter(is_active=True, tenant_id=1).first()
tenant_id = account.tenant_id
print("tenant_id:", tenant_id)

print("\n=== 1) Real sync, then confirm JobberUser.phone ===")
run = sync_tenant(account)
print(json.dumps({'sync_status': run.status, 'error_message': run.error_message}, default=str))

for u in JobberUser.objects.filter(tenant_id=tenant_id, is_active=True).order_by('name'):
    print(json.dumps({'user_id': u.id, 'name': u.name, 'phone': u.phone}, default=str))

print("\n=== 2) Real, distinct job_status values for this tenant (confirms no 'cancelled' in real data either) ===")
statuses = list(
    JobberJob.objects.filter(tenant_id=tenant_id, is_active=True)
    .values_list('job_status', flat=True)
    .distinct()
)
print("distinct job_status values:", statuses)
print("'cancelled' present:", 'cancelled' in statuses)

print("\n=== 3) _local_technician_stats_response() -- real per-technician numbers ===")
data = _local_technician_stats_response(_FakeRequestUser(tenant_id))
print(json.dumps(data, indent=2, default=str))

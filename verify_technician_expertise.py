"""
Part B, Step 3 verification: a real sync, then confirm JobberUser.
expertise/experience_years actually populate from the real "Expertise"/
"Experience" Team custom fields. Run via
`python manage.py shell < verify_technician_expertise.py`.

Also confirms the technician-stats endpoint response carries both
fields through unchanged.
"""
import json

from apps.jobber.api.technician_stats import _local_technician_stats_response
from apps.jobber.models import JobberAccount, JobberUser
from apps.jobber.services.sync import sync_tenant


class _FakeRequestUser:
    def __init__(self, tenant_id):
        self.tenant_id = tenant_id


# Standing instruction: always filter test-account lookups by tenant_id=1
# explicitly.
account = JobberAccount.objects.filter(is_active=True, tenant_id=1).first()
tenant_id = account.tenant_id
print("tenant_id:", tenant_id)

print("\n=== 1) Real sync, then confirm JobberUser.expertise/experience_years ===")
run = sync_tenant(account)
print(json.dumps({'sync_status': run.status, 'error_message': run.error_message}, default=str))

for u in JobberUser.objects.filter(tenant_id=tenant_id, is_active=True).order_by('name'):
    print(json.dumps({
        'user_id': u.id,
        'name': u.name,
        'expertise': u.expertise,
        'experience_years': u.experience_years,
    }, default=str))

print("\n=== 2) technician-stats response carries both fields through ===")
data = _local_technician_stats_response(_FakeRequestUser(tenant_id))
for tech in data.get('technicians', []):
    print(json.dumps({
        'name': tech['name'],
        'expertise': tech['expertise'],
        'experience_years': tech['experience_years'],
    }, default=str))

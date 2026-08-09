"""
Compare live-proxy vs local-table Employees output for the real connected
tenant. Run via `python manage.py shell < compare_employees.py`.

LOCAL calls ensure_fresh(..., require_complete=True) -- may retry once
synchronously if the sync it triggers comes back PARTIAL. Expected known
discrepancy vs LIVE: a Visit with more than one assignedUser only ever has
its first one stored locally (see sync.py's sync_visits() docstring), so
job_count can be lower locally for any employee whose only credit on a job
came from being a visit's non-first assignee.
"""
import json

from django.utils import timezone

from apps.jobber.api.employees import _rank_employees, _local_employees_response
from apps.jobber.models import JobberAccount
from apps.jobber.services import client

account = JobberAccount.objects.filter(is_active=True).first()
user = account.tenant.users.first()
print("tenant_id:", account.tenant_id, "user_id:", user.id if user else None)

print("\n=== LIVE (calls Jobber right now, full pull) ===")
try:
    job_nodes = client.fetch_all_pages(client.fetch_jobs, account, 'fetch_jobs')
    user_nodes = client.fetch_all_pages(client.fetch_users, account, 'fetch_users')
    live_data = {
        'connected': True,
        'employees': _rank_employees(job_nodes, user_nodes),
        'computed_at': timezone.now().isoformat(),
    }
except Exception as exc:
    live_data = {'error': str(exc)}
print(json.dumps(live_data, indent=2, default=str))

print("\n=== LOCAL (ensure_fresh(require_complete=True) + local tables -- not wired into the view) ===")
local_data = _local_employees_response(user)
print(json.dumps(local_data, indent=2, default=str))

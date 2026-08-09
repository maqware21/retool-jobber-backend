"""
Compare live-proxy vs local-table Jobs output for the real connected
tenant. Run via `python manage.py shell < compare_jobs.py`.

LIVE calls the exact same functions JobberJobsView.get() calls internally
(client.fetch_jobs + _map_job) -- a real Jobber call happens.
LOCAL calls the new, not-yet-wired _local_jobs_response(), which calls
ensure_fresh() (may trigger a sync if stale) then reads local tables only.
"""
import json

from apps.jobber.api.jobs import _map_job, _local_jobs_response
from apps.jobber.models import JobberAccount
from apps.jobber.services import client

account = JobberAccount.objects.filter(is_active=True).first()
user = account.tenant.users.first()
print("tenant_id:", account.tenant_id, "user_id:", user.id if user else None)

print("\n=== LIVE (calls Jobber right now) ===")
try:
    raw = client.fetch_jobs(account, first=25, after=None)
    page_info = raw.get('pageInfo') or {}
    live_data = {
        'connected': True,
        'jobs': [_map_job(node) for node in (raw.get('nodes') or [])],
        'page_info': {
            'has_next_page': bool(page_info.get('hasNextPage')),
            'end_cursor': page_info.get('endCursor'),
        },
    }
except Exception as exc:
    live_data = {'error': str(exc)}
print(json.dumps(live_data, indent=2, default=str))

print("\n=== LOCAL (ensure_fresh() + local tables -- not wired into the view) ===")
local_data = _local_jobs_response(user, first=25, after=None)
print(json.dumps(local_data, indent=2, default=str))

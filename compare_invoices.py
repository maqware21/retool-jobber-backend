"""
Compare live-proxy vs local-table Invoices output for the real connected
tenant. Run via `python manage.py shell < compare_invoices.py`.
"""
import json

from apps.jobber.api.invoices import _map_invoice, _compute_summary, _local_invoices_response
from apps.jobber.models import JobberAccount
from apps.jobber.services import client

account = JobberAccount.objects.filter(is_active=True, tenant_id=1).first()
user = account.tenant.users.first()
print("tenant_id:", account.tenant_id, "user_id:", user.id if user else None)

print("\n=== LIVE (calls Jobber right now) ===")
try:
    raw = client.fetch_invoices(account, first=25, after=None)
    page_info = raw.get('pageInfo') or {}
    invoices = [_map_invoice(node) for node in (raw.get('nodes') or [])]
    live_data = {
        'connected': True,
        'invoices': invoices,
        'summary': _compute_summary(invoices),
        'page_info': {
            'has_next_page': bool(page_info.get('hasNextPage')),
            'end_cursor': page_info.get('endCursor'),
        },
    }
except Exception as exc:
    live_data = {'error': str(exc)}
print(json.dumps(live_data, indent=2, default=str))

print("\n=== LOCAL (ensure_fresh() + local tables -- not wired into the view) ===")
local_data = _local_invoices_response(user, first=25, after=None)
print(json.dumps(local_data, indent=2, default=str))

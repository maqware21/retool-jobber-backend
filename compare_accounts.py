"""
Compare live-proxy vs local-table Accounts output for the real connected
tenant. Run via `python manage.py shell < compare_accounts.py`.

LOCAL calls ensure_fresh(..., require_complete=True) -- may retry once
synchronously if the sync it triggers comes back PARTIAL.
"""
import json

from django.utils import timezone

from apps.jobber.api.accounts import _rank_accounts, _service_type_breakdown, _local_accounts_response
from apps.jobber.models import JobberAccount
from apps.jobber.services import client

account = JobberAccount.objects.filter(is_active=True).first()
user = account.tenant.users.first()
print("tenant_id:", account.tenant_id, "user_id:", user.id if user else None)

print("\n=== LIVE (calls Jobber right now, full pull) ===")
try:
    job_nodes = client.fetch_all_pages(client.fetch_jobs, account, 'fetch_jobs')
    invoice_nodes = client.fetch_all_pages(client.fetch_invoices, account, 'fetch_invoices')
    live_data = {
        'connected': True,
        'accounts': _rank_accounts(job_nodes, invoice_nodes),
        'service_type_breakdown': _service_type_breakdown(job_nodes),
        'computed_at': timezone.now().isoformat(),
    }
except Exception as exc:
    live_data = {'error': str(exc)}
print(json.dumps(live_data, indent=2, default=str))

print("\n=== LOCAL (ensure_fresh(require_complete=True) + local tables -- not wired into the view) ===")
local_data = _local_accounts_response(user)
print(json.dumps(local_data, indent=2, default=str))

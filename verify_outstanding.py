"""
Real verification (2026-09-09) for the Outstanding KPI -- real remaining
balance across genuinely unpaid invoices.

Run via `python manage.py shell < verify_outstanding.py`.

Reports the real electricians-summary endpoint's own outstanding value,
independently recomputes it from raw JobberInvoice data (a fresh
Sum('balance') query, not reusing the endpoint's own query object), and
lists every real, contributing invoice (its status_display and balance)
for a direct human sanity check.
"""
from django.db.models import Sum

from apps.jobber.api.electricians_summary import _local_electricians_summary_response
from apps.jobber.models import JobberInvoice
from apps.tenants.models import Tenant

TENANT_ID = 4


class _FakeUser:
    tenant_id = TENANT_ID


tenant = Tenant.objects.filter(id=TENANT_ID).first()
print("tenant:", tenant)

data = _local_electricians_summary_response(_FakeUser())
print(f"connected: {data.get('connected')}")
print(f"outstanding (endpoint): {data.get('outstanding')}")

contributing = JobberInvoice.objects.filter(
    tenant_id=TENANT_ID, is_active=True,
).exclude(status_display='Draft').filter(balance__gt=0)

print(f"\n{contributing.count()} real contributing invoice(s):")
for inv in contributing:
    print(f"  invoice_number={inv.invoice_number} status_display={inv.status_display} "
          f"amount={inv.amount} balance={inv.balance}")

recomputed = contributing.aggregate(total=Sum('balance'))['total']
recomputed_float = float(recomputed) if recomputed is not None else 0.0
status = "MATCH" if data.get('outstanding') == recomputed_float else "MISMATCH"
print(f"\nRecomputed outstanding: {recomputed_float} -> {status}")

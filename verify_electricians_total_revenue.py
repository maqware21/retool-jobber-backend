"""
Step 1 verification for the Electricians "Total Revenue" cutover. Run via
`python manage.py shell < verify_electricians_total_revenue.py`.

Queries the local JobberInvoice table directly for the connected tenant --
real row count, how many are status_display='Paid', and their real
amount/issued_date values. Also runs the exact query the new endpoint
uses (Paid + issued_date >= now - relativedelta(months=6)) so the number
the endpoint will actually return is known before the endpoint is ever
hit for real.
"""
import json

from dateutil.relativedelta import relativedelta
from django.db.models import Sum
from django.utils import timezone

from apps.jobber.models import JobberAccount, JobberInvoice

account = JobberAccount.objects.filter(is_active=True).first()
tenant_id = account.tenant_id
print("tenant_id:", tenant_id)

all_invoices = JobberInvoice.objects.filter(tenant_id=tenant_id)
active_invoices = all_invoices.filter(is_active=True)
paid_invoices = active_invoices.filter(status_display='Paid')

print("\n--- Row counts ---")
print(json.dumps({
    'JobberInvoice total rows': all_invoices.count(),
    'JobberInvoice active rows': active_invoices.count(),
    'active AND status_display=Paid': paid_invoices.count(),
}, indent=2))

print("\n--- Every active invoice: jobber_id, status_display, amount, issued_date ---")
for inv in active_invoices.order_by('issued_date'):
    print(json.dumps({
        'jobber_id': inv.jobber_id,
        'invoice_number': inv.invoice_number,
        'status_display': inv.status_display,
        'amount': str(inv.amount),
        'issued_date': str(inv.issued_date),
        'synced_at': str(inv.synced_at),
    }, default=str))

print("\n--- The exact query the new endpoint runs ---")
period_start = timezone.now() - relativedelta(months=6)
print("period_start (now - relativedelta(months=6)):", period_start)

in_period_paid = paid_invoices.filter(issued_date__gte=period_start)
print(f"\nPaid invoices with issued_date >= period_start: {in_period_paid.count()}")
for inv in in_period_paid.order_by('issued_date'):
    print(json.dumps({
        'jobber_id': inv.jobber_id,
        'amount': str(inv.amount),
        'issued_date': str(inv.issued_date),
    }, default=str))

total = in_period_paid.aggregate(total=Sum('amount'))['total']
print("\nSum(amount) over that filtered set (this is what the endpoint will return as total_revenue):")
print(json.dumps({'total_revenue': float(total) if total is not None else 0.0}, indent=2))

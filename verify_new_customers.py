"""
Real verification (2026-09-09) for the New Customers KPI -- approved
new_customers_metric_proposal.md, Option b (earliest real job, ANY
status, full unwindowed history per client).

Run via `python manage.py shell < verify_new_customers.py`.

Reports the real electricians-summary endpoint's own new_customers
count, independently recomputes it from raw JobberJob data (a fresh
Min('jobber_created_at') per client, not reusing the endpoint's own
query object), and lists exactly which real clients counted as new and
why -- for a direct human sanity check against what's actually in
Jobber for this account.
"""
from dateutil.relativedelta import relativedelta
from django.db.models import Min
from django.utils import timezone

from apps.jobber.api.electricians_summary import PERIOD_MONTHS, _local_electricians_summary_response
from apps.jobber.models import JobberClient, JobberJob
from apps.tenants.models import Tenant

TENANT_ID = 4


class _FakeUser:
    tenant_id = TENANT_ID


tenant = Tenant.objects.filter(id=TENANT_ID).first()
print("tenant:", tenant)

data = _local_electricians_summary_response(_FakeUser())
print(f"connected: {data.get('connected')}")
print(f"new_customers (endpoint): {data.get('new_customers')}")

period_start = timezone.now() - relativedelta(months=PERIOD_MONTHS)
first_job_dates = (
    JobberJob.objects.filter(tenant_id=TENANT_ID, is_active=True)
    .values('client_id')
    .annotate(first_job_at=Min('jobber_created_at'))
)
new_client_ids = [
    row['client_id'] for row in first_job_dates
    if row['first_job_at'] is not None and row['first_job_at'] >= period_start
]
print(f"new_customers (recomputed independently): {len(new_client_ids)}")

if new_client_ids:
    print("\nReal clients counted as new this window:")
    for client in JobberClient.objects.filter(id__in=new_client_ids):
        first_job = JobberJob.objects.filter(
            tenant_id=TENANT_ID, is_active=True, client_id=client.id,
        ).order_by('jobber_created_at').first()
        print(
            f"  {client.name}: earliest real job_number={first_job.job_number if first_job else None} "
            f"status={first_job.job_status if first_job else None} "
            f"jobber_created_at={first_job.jobber_created_at if first_job else None}"
        )
else:
    print("\nNo real clients counted as new this window.")

status = "MATCH" if data.get('new_customers') == len(new_client_ids) else "MISMATCH"
print(f"\n{status}")

"""
Real verification (2026-09-09) for the Labor Cost KPI -- approved
labor_cost_kpi_proposal.md.

Run via `python manage.py shell < verify_labor_cost.py`.

Reports the real electricians-summary endpoint's own labor_cost value,
independently recomputes it from raw JobberTimeSheetEntry data (a fresh
per-entry sum, not reusing the endpoint's own query object), and lists
every real, usable entry that contributed -- for a direct human sanity
check. As of this writing, this account is confirmed to have NO real
non-zero labour_rate values anywhere (verify_labour_rate_field.py/
verify_labour_rate_field_job3.py) -- labor_cost is EXPECTED to be None
right now, which is the correct, honest result, not a bug. Re-run once
a real rate has actually been entered in Jobber for at least one
technician's logged hours.
"""
from decimal import Decimal

from apps.jobber.api.electricians_summary import PERIOD_MONTHS, _local_electricians_summary_response
from apps.jobber.models import JobberJob, JobberTimeSheetEntry
from apps.tenants.models import Tenant
from django.utils import timezone
from dateutil.relativedelta import relativedelta

TENANT_ID = 4


class _FakeUser:
    tenant_id = TENANT_ID


tenant = Tenant.objects.filter(id=TENANT_ID).first()
print("tenant:", tenant)

data = _local_electricians_summary_response(_FakeUser())
print(f"connected: {data.get('connected')}")
print(f"labor_cost (endpoint): {data.get('labor_cost')}")

period_start = timezone.now() - relativedelta(months=PERIOD_MONTHS)
archived_jobs = JobberJob.objects.filter(
    tenant_id=TENANT_ID, is_active=True, job_status='archived', completed_at__gte=period_start,
)
entries = JobberTimeSheetEntry.objects.filter(
    tenant_id=TENANT_ID, is_active=True, job__in=archived_jobs,
).exclude(labour_rate__isnull=True).exclude(labour_rate=0)

print(f"\n{entries.count()} real, usable timesheet entr{'y' if entries.count() == 1 else 'ies'} "
      f"(non-null, non-zero labour_rate) in the window:")
recomputed = Decimal('0')
for e in entries:
    hours = e.final_duration_seconds / 3600
    cost = Decimal(str(hours)) * e.labour_rate
    recomputed += cost
    print(f"  entry={e.jobber_id} job_id={e.job_id} hours={hours:.2f} rate={e.labour_rate} cost={cost}")

recomputed_float = float(recomputed) if entries.exists() else None
status = "MATCH" if data.get('labor_cost') == recomputed_float else "MISMATCH"
print(f"\nRecomputed labor_cost: {recomputed_float} -> {status}")

if not entries.exists():
    print(
        "\nNo real, usable labour_rate exists yet in this account's window -- labor_cost=None is "
        "EXPECTED and correct, not a bug. Re-run once a real rate has been entered in Jobber."
    )

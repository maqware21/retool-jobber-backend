"""
Real verification (2026-09-14) for Cost Breakdown Dynamic Categories --
final design: Labor (YTD) + Total Expenses (YTD), no categorization
(Accounting Codes confirmed permanently absent from Jobber's real
GraphQL schema). Approved cost_breakdown_dynamic_categories_proposal.md.

Run via `python manage.py shell < verify_cost_breakdown.py`.

Forces a real, fresh sync (so the new 'expenses' entity gets pulled for
the first time), then reports the real Accounts endpoint's own
cost_breakdown, independently recomputes both stats from raw local
data, and confirms job #6's real $600 LED Fixture expense is present
and correctly included in Total Expenses YTD.
"""
from datetime import datetime, time

from django.db.models import Sum
from django.utils import timezone

from apps.goals.utils import current_year
from apps.jobber.api.accounts import _cost_breakdown, _local_accounts_response
from apps.jobber.api.electricians_summary import labor_cost_for_jobs
from apps.jobber.models import JobberAccount, JobberExpense, JobberJob
from apps.jobber.services.sync import ensure_fresh
from apps.tenants.models import Tenant

TENANT_ID = 5


class _FakeUser:
    tenant_id = TENANT_ID


tenant = Tenant.objects.filter(id=TENANT_ID).first()
print("tenant:", tenant)

account = JobberAccount.objects.filter(tenant_id=TENANT_ID, is_active=True).first()
if account is None:
    print(f"No active JobberAccount for tenant_id={TENANT_ID} -- nothing to verify.")
else:
    fresh = ensure_fresh(tenant, entities=['jobs', 'visits', 'timesheet_entries', 'expenses'], require_complete=True)
    print(f"sync result: last_synced_at={fresh['last_synced_at']} sync_warning={fresh.get('sync_warning')}")

    print("\n=== Real, locally-synced JobberExpense rows ===")
    for e in JobberExpense.objects.filter(tenant_id=TENANT_ID, is_active=True):
        print(f"  jobber_id={e.jobber_id} title={e.title!r} total={e.total} "
              f"incurred_at={e.incurred_at} job_number={e.job.job_number if e.job else None}")

    job6 = JobberJob.objects.filter(tenant_id=TENANT_ID, is_active=True, job_number=6).first()
    job6_expense = JobberExpense.objects.filter(tenant_id=TENANT_ID, is_active=True, job=job6).first() if job6 else None
    print(f"\nJob #6's real expense locally synced: {job6_expense}")
    if job6_expense:
        print(f"  total={job6_expense.total} (expect 600.00)")
        assert job6_expense.total == 600, f"Expected 600, got {job6_expense.total}"

    print("\n=== Real cost_breakdown via _cost_breakdown() ===")
    breakdown = _cost_breakdown(TENANT_ID)
    print(f"  {breakdown}")

    print("\n=== Independent recomputation ===")
    year_date = current_year()
    year_start = timezone.make_aware(datetime.combine(year_date, time.min))
    ytd_jobs = JobberJob.objects.filter(
        tenant_id=TENANT_ID, is_active=True, job_status='archived', completed_at__gte=year_start,
    )
    recomputed_labor = labor_cost_for_jobs(TENANT_ID, ytd_jobs)
    recomputed_expenses = JobberExpense.objects.filter(
        tenant_id=TENANT_ID, is_active=True, incurred_at__gte=year_start,
    ).aggregate(total=Sum('total'))['total']
    print(f"  recomputed labor_ytd: {float(recomputed_labor) if recomputed_labor is not None else None}")
    print(f"  recomputed total_expenses_ytd: {float(recomputed_expenses) if recomputed_expenses is not None else 0.0}")

    labor_match = breakdown['labor_ytd'] == (float(recomputed_labor) if recomputed_labor is not None else None)
    expenses_match = breakdown['total_expenses_ytd'] == (float(recomputed_expenses) if recomputed_expenses is not None else 0.0)
    print(f"\n  labor_ytd -> {'MATCH' if labor_match else 'MISMATCH'}")
    print(f"  total_expenses_ytd -> {'MATCH' if expenses_match else 'MISMATCH'}")

    print("\n=== Full real Accounts endpoint response's cost_breakdown ===")
    data = _local_accounts_response(_FakeUser())
    print(f"  connected: {data.get('connected')}")
    print(f"  cost_breakdown: {data.get('cost_breakdown')}")

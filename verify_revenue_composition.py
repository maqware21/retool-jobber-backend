"""
Real verification (2026-09-07) for Revenue Composition's expense/profit
split -- approved revenue_composition_expense_profit_proposal.md. Run
via `python manage.py shell < verify_revenue_composition.py`.

Confirms:
  1. A real, fresh sync populates line_item_cost on already-existing
     local JobberJob rows (a row synced before this build shipped won't
     have it until its next real sync -- ensure_fresh() below forces
     exactly that).
  2. Jobs #1-6 (this account's real historical jobs) show
     line_item_cost == total (cost=price) -- a real $0 profit, not a bug.
  3. Job #7 specifically, if it has archived since the last check: its
     real, distinct cost (400 on a 1000 job, confirmed live) flows
     through correctly -- reported plainly either way (found & correct,
     or not yet archived).
  4. The full 6-month _local_revenue_composition_response() output,
     printed plainly, for a final human sanity check against Jobber's
     own dashboard.
"""
from apps.jobber.api.revenue_composition import _local_revenue_composition_response
from apps.jobber.models import JobberAccount, JobberJob
from apps.jobber.services.sync import ensure_fresh
from apps.tenants.models import Tenant

TENANT_ID = 4

tenant = Tenant.objects.filter(id=TENANT_ID).first()
print("tenant:", tenant)

account = JobberAccount.objects.filter(tenant_id=TENANT_ID, is_active=True).first()
if account is None:
    print(f"No active JobberAccount for tenant_id={TENANT_ID} -- nothing to verify.")
else:
    fresh = ensure_fresh(tenant, entities=['jobs', 'visits', 'timesheet_entries'], require_complete=True)
    print(f"sync result: last_synced_at={fresh['last_synced_at']} sync_warning={fresh.get('sync_warning')}")

    print("\n=== Jobs #1-6 (real historical jobs) -- expect line_item_cost == total ===")
    for job in JobberJob.objects.filter(
        tenant_id=TENANT_ID, is_active=True, job_number__in=[1, 2, 3, 4, 5, 6],
    ).order_by('job_number'):
        matches = job.line_item_cost is not None and job.line_item_cost == job.total
        print(
            f"  Job #{job.job_number}: total={job.total} line_item_cost={job.line_item_cost!r} "
            f"-> {'MATCH (real $0 profit)' if matches else 'DOES NOT MATCH -- investigate'}"
        )

    print("\n=== Job #7 ===")
    job7 = JobberJob.objects.filter(tenant_id=TENANT_ID, is_active=True, job_number=7).first()
    if job7 is None:
        print("  Job #7 has no local row yet -- not yet synced (expected if still Upcoming and never archived).")
    else:
        print(
            f"  job_status={job7.job_status} total={job7.total} line_item_cost={job7.line_item_cost!r} "
            f"completed_at={job7.completed_at}"
        )
        if job7.job_status != 'archived':
            print("  Job #7 has not archived yet -- cannot be inside the Composition window until it does.")
        elif job7.line_item_cost is not None and job7.total is not None and job7.line_item_cost != job7.total:
            print(
                f"  CONFIRMED: Job #7's real, distinct cost ({job7.line_item_cost}) vs price "
                f"({job7.total}) is now locally synced correctly."
            )
        else:
            print("  UNEXPECTED -- Job #7 is archived but line_item_cost does not show the expected divergence.")

    print("\n=== Full 6-month Revenue Composition response ===")
    data = _local_revenue_composition_response(tenant)
    print(f"connected: {data.get('connected')}")
    for row in data.get('rows', []):
        profit = round(row['revenue'] - row['expenses'], 2)
        print(f"  {row['month']}: revenue={row['revenue']} expenses={row['expenses']} profit={profit}")

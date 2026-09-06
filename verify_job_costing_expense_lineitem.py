"""
Real verification (2026-09-06) -- Revenue Health Phase 2, Part B.
Verification only, nothing built from this yet.

Live-checks Jobber's own job.jobCosting.expenseCost and .lineItemCost for
2-3 real archived jobs (tenant_id=4) -- deliberately NOT labourCost/
labourDuration, already confirmed broken/always-0 weeks ago (a different,
unrelated finding about a different pair of fields on the same JobCosting
object). Determines whether Revenue Composition's expense/profit split --
and possibly a real, labor-EXCLUDED "expenses" figure -- is genuinely
buildable, separate from Labor Cost's own already-confirmed-rejected
status (TimeSheetEntry.labourRate, rejected 2026-09-06 for company-wide
use because most real customers won't fill it in).

Run via `python manage.py shell < verify_job_costing_expense_lineitem.py`.

Picks the first 3 real, locally-synced archived jobs for tenant_id=4 (same
"use an already-synced local jobber_id, avoid guessing/full-account-scan"
approach as every other single-job live check in this project) and queries
each one's live job.jobCosting directly via Query.job(id:) -- the same
cheap, single-job lookup pattern already used elsewhere (e.g.
verify_labour_rate_field_job3.py) -- requesting ONLY id/jobNumber/
jobCosting { expenseCost lineItemCost }, nothing else. Reports the raw
values plainly, populated or null, and nothing else -- no interpretation
beyond what's printed.
"""
from apps.jobber.models import JobberAccount, JobberJob
from apps.jobber.services.client import JobberAPIError, execute

TENANT_ID = 4

_JOB_COSTING_QUERY = """
query GetJobCostingExpenseLineItem($id: EncodedId!) {
  job(id: $id) {
    id
    jobNumber
    jobCosting {
      expenseCost
      lineItemCost
    }
  }
}
"""

account = JobberAccount.objects.filter(tenant_id=TENANT_ID, is_active=True).first()
print("account:", account)

if account is None:
    print(f"No active JobberAccount for tenant_id={TENANT_ID} -- cannot query live.")
else:
    local_jobs = list(
        JobberJob.objects.filter(tenant_id=TENANT_ID, is_active=True, job_status='archived')
        .order_by('job_number')[:3]
    )
    print(f"{len(local_jobs)} local archived job(s) picked to check: "
          f"{[j.job_number for j in local_jobs]}")

    for local_job in local_jobs:
        try:
            data = execute(account, _JOB_COSTING_QUERY, {'id': local_job.jobber_id})
        except JobberAPIError as exc:
            print(f"\nJob #{local_job.job_number}: Jobber API error: {exc}")
            continue

        job = (data or {}).get('job')
        if job is None:
            print(f"\nJob #{local_job.job_number}: live job(id=...) returned null.")
            continue

        costing = job.get('jobCosting') or {}
        print(
            f"\nJob #{job.get('jobNumber')}: "
            f"jobCosting.expenseCost={costing.get('expenseCost')!r}  "
            f"jobCosting.lineItemCost={costing.get('lineItemCost')!r}"
        )

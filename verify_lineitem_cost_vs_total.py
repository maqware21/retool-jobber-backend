"""
Real verification (2026-09-06) -- Revenue Health Phase 2, Part B
follow-up. Verification only, no building yet.

Confirms directly whether job.jobCosting.lineItemCost (real, non-zero,
per verify_job_costing_expense_lineitem.py's own real findings: $350/
$850/$1200 across Jobs #1/#2/#3) equals or closely tracks that SAME
job's own real Job.total -- if so, lineItemCost is circular for margin
purposes (cost = price, always, by construction), not a usable cost
source; if it genuinely diverges in a meaningful way, that's a
different, more useful finding, worth a real proposal.

Run via `python manage.py shell < verify_lineitem_cost_vs_total.py`.

Reuses the exact same 3 real archived jobs (tenant_id=4, job_number
1/2/3) and the exact same live jobCosting query already run for the
expenseCost check -- compares each job's live lineItemCost against ITS
OWN already-synced local Job.total, side by side, plainly.
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
        line_item_cost = costing.get('lineItemCost')
        job_total = float(local_job.total)
        matches = line_item_cost is not None and abs(line_item_cost - job_total) < 0.01

        print(
            f"\nJob #{job.get('jobNumber')}: "
            f"Job.total (local, real)={job_total}  "
            f"jobCosting.lineItemCost (live, real)={line_item_cost!r}  "
            f"jobCosting.expenseCost (live, real)={costing.get('expenseCost')!r}  "
            f"-> {'EQUAL to Job.total (circular)' if matches else 'DIFFERENT from Job.total'}"
        )

"""
Part C fact-check: is ANY of Job.jobCosting usable, or is the whole
sub-object stale/zero in this Jobber account the same way labourDuration
already was? No relation to Part A/B's Top Earner logic -- purely
informational, no code changes.

Run via `python manage.py shell < verify_jobcosting_fields.py`.

Queries the FULL jobCosting sub-object (not just labourCost/lineItemCost/
expenseCost as literally named) for 3 real archived jobs -- profitAmount,
profitPercentage, totalCost, totalRevenue, belowMinimumThreshold too, at
zero extra query cost in the same call, since the actual question this
answers is "could Profit Margin ever be built from jobCosting directly?"
and that needs the profit/cost/revenue fields, not just the 3 named ones.
"""
import json

from apps.jobber.models import JobberAccount, JobberJob
from apps.jobber.services import client

# Standing instruction: always filter test-account lookups by tenant_id=1
# explicitly.
account = JobberAccount.objects.filter(is_active=True, tenant_id=1).first()
tenant_id = account.tenant_id
print("tenant_id:", tenant_id)

jobs = JobberJob.objects.filter(tenant_id=tenant_id, is_active=True, job_status='archived').order_by('job_number')[:3]
print(f"Checking jobCosting live for {jobs.count()} real archived jobs:")

_JOBCOSTING_QUERY = """
query GetJobCostingCheck($id: EncodedId!) {
  job(id: $id) {
    id
    jobNumber
    total
    jobCosting {
      labourCost
      labourDuration
      lineItemCost
      expenseCost
      profitAmount
      profitPercentage
      totalCost
      totalRevenue
      belowMinimumThreshold
    }
  }
}
"""

for job in jobs:
    data = client.execute(account, _JOBCOSTING_QUERY, {'id': job.jobber_id})
    live_job = (data or {}).get('job') or {}
    costing = live_job.get('jobCosting') or {}
    print(json.dumps({
        'job_number': job.job_number,
        'local_job_total': str(job.total),
        'live_jobCosting': costing,
    }, indent=2, default=str))

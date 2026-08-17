"""
Part B verification: real per-technician revenue totals for every archived
job in the window right now, and the final top_earner result the endpoint
would return. Run via `python manage.py shell < verify_top_earner_final.py`.

Real numbers, not asserted -- reports calculate_job_revenue_shares() for
every archived job individually (so you can see exactly which rule fired
for each job and why), then the accumulated per-technician totals across
the whole window, then the final pick_top_earner() result.
"""
import json

from apps.jobber.models import JobberAccount, JobberJob, JobberUser
from apps.jobber.api.electricians_summary import (
    PERIOD_MONTHS,
    calculate_job_revenue_shares,
    calculate_top_earner,
    pick_top_earner,
)
from dateutil.relativedelta import relativedelta
from django.utils import timezone

# Standing instruction: always filter test-account lookups by tenant_id=1
# explicitly.
account = JobberAccount.objects.filter(is_active=True, tenant_id=1).first()
tenant_id = account.tenant_id
print("tenant_id:", tenant_id)

period_start = timezone.now() - relativedelta(months=PERIOD_MONTHS)
archived_jobs = list(JobberJob.objects.filter(
    tenant_id=tenant_id, is_active=True, job_status='archived', completed_at__gte=period_start,
))
print(f"Archived jobs in the {PERIOD_MONTHS}-month window: {len(archived_jobs)}")

print("\n=== Per-job revenue shares (which rule fired, for whom) ===")
for job in archived_jobs:
    shares = calculate_job_revenue_shares(job)
    # Resolve names for readability.
    user_ids = list(shares.keys())
    names = {u.id: u.name for u in JobberUser.objects.filter(id__in=user_ids)}
    print(json.dumps({
        'job_number': job.job_number,
        'job_total': str(job.total),
        'shares': {names.get(uid, uid): round(share, 2) for uid, share in shares.items()},
    }, indent=2, default=str))

print("\n=== Accumulated per-technician totals across the whole window ===")
totals = calculate_top_earner(archived_jobs)
users_by_id = {u.id: u for u in JobberUser.objects.filter(id__in=totals.keys())}
readable_totals = {users_by_id[uid].name: round(rev, 2) for uid, rev in totals.items()}
print(json.dumps(readable_totals, indent=2, default=str))

print("\n=== Final top_earner result (what the endpoint returns) ===")
top_earner = pick_top_earner(totals, users_by_id)
print(json.dumps(top_earner, indent=2, default=str))

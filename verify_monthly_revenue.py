"""
Verification for GET /v1/jobber/monthly-revenue/ (Monthly Revenue by
Technician chart, 2026-08-24). Run via
`python manage.py shell < verify_monthly_revenue.py`.

Prints the real months/rows for tenant_id=1, and cross-checks: summing
this endpoint's per-month revenue for each technician across all 6
month buckets should exactly equal calculate_top_earner() called ONCE
over the SAME month-aligned range (the earliest bucket's start through
now) -- same underlying job set, just partitioned by month vs. not.

Deliberately NOT compared against electricians-summary's own Top
Earner number -- that uses a CONTINUOUS rolling window
(timezone.now() - relativedelta(months=6)), which is a different range
than this endpoint's calendar-month-aligned buckets (confirmed
directly this round -- see monthly_revenue_chart_proposal.md). Comparing
against that would produce a real, expected mismatch for the wrong
reason (a few days at the start of the window, not a real bug), which
is exactly the kind of boundary confusion this script exists to avoid.
"""
import json

from apps.jobber.api.electricians_summary import calculate_top_earner
from apps.jobber.api.monthly_revenue import MONTHS_BACK, _local_monthly_revenue_response
from apps.goals.utils import current_month
from apps.jobber.models import JobberJob
from apps.tenants.models import Tenant
from django.utils import timezone
from dateutil.relativedelta import relativedelta

tenant = Tenant.objects.filter(id=1).first()
print("tenant:", tenant)

data = _local_monthly_revenue_response(tenant)
print("connected:", data.get("connected"))
print("months:", data.get("months"))
print(f"\n=== {len(data.get('rows', []))} row(s) ===")
for r in data.get("rows", []):
    print(f"{r['month']}  user_id={r['user_id']:<4} {r['name']:<20} revenue={r['revenue']}")

# --- Cross-check: sum of the 6 monthly buckets per technician vs.
# calculate_top_earner() over the SAME month-aligned range in one call.
totals_by_month_sum = {}
for r in data.get("rows", []):
    totals_by_month_sum[r["user_id"]] = totals_by_month_sum.get(r["user_id"], 0.0) + r["revenue"]

earliest_bucket_start = current_month() - relativedelta(months=MONTHS_BACK - 1)
aligned_start = timezone.make_aware(
    timezone.datetime.combine(earliest_bucket_start, timezone.datetime.min.time())
)
archived_jobs_aligned = JobberJob.objects.filter(
    tenant_id=tenant.id, is_active=True, job_status="archived", completed_at__gte=aligned_start,
)
totals_aligned = calculate_top_earner(archived_jobs_aligned)

print("\n=== Cross-check: sum-of-6-monthly-buckets vs. ONE month-aligned calculate_top_earner() call ===")
print(f"(both computed from {earliest_bucket_start} onward -- same range, just partitioned differently)")
all_user_ids = set(totals_by_month_sum) | set(totals_aligned)
for uid in all_user_ids:
    bucketed = round(totals_by_month_sum.get(uid, 0.0), 2)
    aligned = round(float(totals_aligned.get(uid, 0.0)), 2)
    status = "MATCH" if bucketed == aligned else "MISMATCH"
    print(f"user_id={uid}: bucketed_sum={bucketed} single_call={aligned} -> {status}")

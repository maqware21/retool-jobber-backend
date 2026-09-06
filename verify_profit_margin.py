"""
SUPERSEDED (2026-09-06, direct TL decision) -- profit_margin_percentage
no longer uses labour_rate/labor cost at all (see technician_stats.py's
own current docstring); this script's recomputation below tests a
formula the endpoint no longer runs. Kept as the historical record of
the original labour_rate-based verification. See
verify_profit_margin_callback_formula.py for the current, real check.

Real verification (2026-09-03) for the new profit_margin_percentage in
get_technician_stats() -- approved labor_cost_profit_margin_proposal.md.
Run via `python manage.py shell < verify_profit_margin.py`.

As of this writing, this account has NO real non-zero
JobberTimeSheetEntry.labour_rate values at all (confirmed:
verify_labour_rate_field.py found the one real entry checked reads
0.00 -- "not entered", not broken). Every technician is therefore
EXPECTED to show profit_margin_percentage=None right now -- that's the
correct, honest result, not a bug. Re-run this once a real rate has
actually been entered in Jobber for at least one technician's logged
hours; only then does this script's real-number checks below become
meaningful.

Reports, plainly:
  1. Every real JobberTimeSheetEntry.labour_rate currently synced,
     grouped by whether it's a real non-zero value or 0.00/null.
  2. Every technician's real profit_margin_percentage from the real
     get_technician_stats() response, alongside their revenue (the
     Top-Earner-attributed figure this margin is computed against, NOT
     Total Revenue's Paid-invoices-only figure -- confirmed distinct
     populations, see get_technician_stats()'s own comments).
  3. For any technician with a non-null margin, independently
     recomputes it from the same real local data (calculate_job_
     duration_by_user's merged hours x the technician's own real rate,
     summed across their real archived jobs in the window) and confirms
     it matches the endpoint's own number exactly -- same proof pattern
     as every other real verification in this project.
"""
from datetime import timedelta

from django.utils import timezone

from apps.jobber.api.electricians_summary import (
    PERIOD_MONTHS,
    calculate_job_duration_by_user,
    calculate_technician_labor_cost,
    calculate_top_earner,
)
from apps.jobber.api.technician_stats import get_technician_stats
from apps.jobber.models import JobberJob, JobberTimeSheetEntry, JobberUser
from apps.tenants.models import Tenant

TENANT_ID = 4

tenant = Tenant.objects.filter(id=TENANT_ID).first()
print("tenant:", tenant)

entries = JobberTimeSheetEntry.objects.filter(tenant_id=TENANT_ID, is_active=True)
real_rates = entries.exclude(labour_rate__isnull=True).exclude(labour_rate=0)
print(f"\n{entries.count()} real timesheet entries synced, "
      f"{real_rates.count()} with a real non-zero labour_rate.")
for e in real_rates:
    print(f"  entry={e.jobber_id} user_id={e.user_id} labour_rate={e.labour_rate}")

if not real_rates.exists():
    print(
        "\nNo real non-zero labour_rate exists yet in this account -- every "
        "technician below is EXPECTED to show profit_margin_percentage=None. "
        "Re-run this script once a real rate has been entered in Jobber."
    )

data = get_technician_stats(tenant)
print(f"\nconnected: {data.get('connected')}")

period_start = timezone.now() - timedelta(days=30 * PERIOD_MONTHS)
archived_jobs = list(JobberJob.objects.filter(
    tenant_id=TENANT_ID, is_active=True, job_status='archived', completed_at__gte=period_start,
))
revenue_totals = calculate_top_earner(archived_jobs)

for t in data.get('technicians', []):
    print(f"\n=== {t['name']} (user_id={t['user_id']}) ===")
    print(f"revenue (Top-Earner-attributed): {t['revenue']}")
    print(f"profit_margin_percentage: {t['profit_margin_percentage']}")

    if t['profit_margin_percentage'] is None:
        continue

    # Independent recomputation, same real primitives, none re-derived.
    user_id = t['user_id']
    total_cost = 0
    for job in archived_jobs:
        hours_by_user = calculate_job_duration_by_user(job)
        cost = calculate_technician_labor_cost(job, user_id, hours_by_user)
        if cost is not None:
            total_cost += cost

    revenue = revenue_totals.get(user_id, 0.0)
    recomputed = round(((revenue - float(total_cost)) / revenue) * 100, 1) if revenue > 0 else None
    status = "MATCH" if recomputed == t['profit_margin_percentage'] else "MISMATCH"
    print(f"Recomputed labor cost: {total_cost}")
    print(f"Recomputed profit_margin_percentage: {recomputed} -> {status}")

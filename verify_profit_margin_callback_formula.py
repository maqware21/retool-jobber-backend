"""
Real verification (2026-09-06) for the SUPERSEDED profit_margin_percentage
formula -- direct TL decision, no design proposal needed: Jobber's Labour
Cost field is no longer used (most real customers won't have it filled
in). New formula: (revenue - callback_dollars_lost) / revenue x 100, per
technician, reusing revenue_totals (calculate_top_earner) and
_accumulate_technician_callback_stats()'s own output directly -- see
technician_stats.py's current docstring for the full reasoning.

Run via `python manage.py shell < verify_profit_margin_callback_formula.py`.

Reports, plainly, for every real technician:
  1. Their real profit_margin_percentage from the real
     get_technician_stats() response, alongside revenue,
     callback_dollars_lost, and has_unknown_callback_cost.
  2. An INDEPENDENT recomputation from the same real primitives
     (calculate_top_earner() for revenue,
     _accumulate_technician_callback_stats() for callback_dollars_lost --
     neither re-derived, both called fresh here) -- confirms it matches
     the endpoint's own number exactly, same proof pattern as every other
     real verification in this project.

Specifically checks, by name, the two real cases the build was asked to
confirm:
  - Cody (Job #3's real callback, tenant_id=4): revenue=$1200,
    callback_dollars_lost=$0 (known amount) with
    has_unknown_callback_cost=True -- margin should be a real 100% (zero
    KNOWN cost lost), but the response's has_unknown_callback_cost flag
    (which TechnicianCard reuses for the "+"/tooltip) must be True, so
    the frontend renders this as a visibly marked minimum, not a clean
    100%.
  - Any technician with callback_visits_done == 0 this window: margin
    should also show 100% (zero revenue lost to callbacks -- a genuine
    100%, not a coincidence), with has_unknown_callback_cost == False,
    so the frontend renders this one as a CLEAN, unmarked 100%.
"""
from apps.jobber.api.electricians_summary import calculate_top_earner
from apps.jobber.api.technician_stats import (
    PERIOD_MONTHS,
    _accumulate_technician_callback_stats,
    get_technician_stats,
)
from apps.jobber.models import JobberJob
from apps.tenants.models import Tenant
from django.utils import timezone
from dateutil.relativedelta import relativedelta

TENANT_ID = 4

tenant = Tenant.objects.filter(id=TENANT_ID).first()
print("tenant:", tenant)

data = get_technician_stats(tenant)
print(f"connected: {data.get('connected')}")

period_start = timezone.now() - relativedelta(months=PERIOD_MONTHS)
archived_jobs = list(JobberJob.objects.filter(
    tenant_id=TENANT_ID, is_active=True, job_status='archived', completed_at__gte=period_start,
))
revenue_totals = calculate_top_earner(archived_jobs)
callback_stats = _accumulate_technician_callback_stats(archived_jobs)

cody_checked = False
zero_callback_checked = False

for t in data.get('technicians', []):
    user_id = t['user_id']
    revenue = revenue_totals.get(user_id, 0.0)
    cb = callback_stats.get(user_id)
    callback_dollars_lost = cb['callback_dollars_lost'] if cb else 0.0
    has_unknown_callback_cost = cb['has_unknown_callback_cost'] if cb else False

    recomputed = (
        round(((revenue - callback_dollars_lost) / revenue) * 100, 1) if revenue > 0 else None
    )
    status = "MATCH" if recomputed == t['profit_margin_percentage'] else "MISMATCH"

    print(f"\n=== {t['name']} (user_id={user_id}) ===")
    print(f"revenue: {t['revenue']}  callback_dollars_lost: {t['callback_dollars_lost']}  "
          f"has_unknown_callback_cost: {t['has_unknown_callback_cost']}  "
          f"callback_visits_done: {t['callback_visits_done']}")
    print(f"profit_margin_percentage (endpoint): {t['profit_margin_percentage']}")
    print(f"profit_margin_percentage (recomputed independently): {recomputed} -> {status}")

    if 'cody' in t['name'].lower():
        cody_checked = True
        print("  >>> This is Cody's real row.")
        assert t['profit_margin_percentage'] == 100.0, (
            f"Expected Cody's margin to be a real 100% (zero KNOWN cost lost), got {t['profit_margin_percentage']}"
        )
        assert t['has_unknown_callback_cost'] is True, (
            "Expected Cody's has_unknown_callback_cost to be True (his real callback has an unknown cost) "
            f"-- got {t['has_unknown_callback_cost']}"
        )
        print("  >>> CONFIRMED: real 100% margin, but has_unknown_callback_cost=True -- "
              "TechnicianCard must render this as a visibly marked minimum (100%+), not a clean 100%.")

    if t['callback_visits_done'] == 0 and not zero_callback_checked:
        zero_callback_checked = True
        print("  >>> This technician has zero callbacks this window.")
        if revenue > 0:
            assert t['profit_margin_percentage'] == 100.0, (
                f"Expected a clean 100% for zero callbacks, got {t['profit_margin_percentage']}"
            )
            assert t['has_unknown_callback_cost'] is False, (
                f"Expected has_unknown_callback_cost=False for zero callbacks, got {t['has_unknown_callback_cost']}"
            )
            print("  >>> CONFIRMED: clean 100% margin, has_unknown_callback_cost=False -- "
                  "TechnicianCard must render this as an UNMARKED 100%, no '+', no tooltip.")

print(f"\nCody checked: {cody_checked}  Zero-callback technician checked: {zero_callback_checked}")
if not cody_checked:
    print("WARNING: no technician named 'Cody' found in this response -- confirm the real name/roster.")
if not zero_callback_checked:
    print("WARNING: no technician with zero callbacks this window found -- cannot confirm the clean-100% case.")

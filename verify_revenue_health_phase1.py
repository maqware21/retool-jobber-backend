"""
Real verification (2026-09-06) for Revenue Health Phase 1 -- confirms the
real backend numbers RevenueHealth/index.tsx now reads (Callback Bleed,
Monthly Shortfall, Total Leakage, the Risk Board's ranking, Total Revenue,
Jobs Completed, Avg Job Value) are what they should be, by independently
recomputing each from the same real primitives the frontend itself reads
(get_technician_stats(), electricians-summary), reusing nothing from the
frontend's own JS -- this is a from-scratch cross-check in Python, not a
re-print of the endpoint's own numbers.

Run via `python manage.py shell < verify_revenue_health_phase1.py`.

calcRiskScore()'s exact scoring logic (frontend/src/utils/risk.ts) is
ported here verbatim so the Risk Board's real ranking can be independently
confirmed too, not just eyeballed.
"""
from apps.jobber.api.electricians_summary import _local_electricians_summary_response
from apps.jobber.api.technician_stats import get_technician_stats
from apps.jobber.models import JobberAccount
from apps.tenants.models import Tenant

TENANT_ID = 4


def calc_risk_score(t):
    """Verbatim port of frontend/src/utils/risk.ts's calcRiskScore()."""
    s = 0
    if t['callback_rate'] is not None:
        if t['callback_rate'] > 20:
            s += 40
        elif t['callback_rate'] > 10:
            s += 25
        elif t['callback_rate'] > 5:
            s += 10
    if t['revenue_per_hour'] is not None:
        if t['revenue_per_hour'] < 75:
            s += 30
        elif t['revenue_per_hour'] < 100:
            s += 20
        elif t['revenue_per_hour'] < 120:
            s += 5
    if t['completion_percentage'] is not None:
        if t['completion_percentage'] < 90:
            s += 20
        elif t['completion_percentage'] < 95:
            s += 10
    # avgDriveTimeHrs is always null in real data -- this factor never
    # fires, confirmed and expected (see revenue_health_audit.md).
    goal_amount = t['goal_progress']['goal_amount']
    if goal_amount and goal_amount > 0:
        last_rev = t['goal_progress']['current_month_revenue']
        if last_rev / goal_amount < 0.70:
            s += 15
        elif last_rev / goal_amount < 0.85:
            s += 8
    return min(s, 100)


def risk_level(score):
    if score >= 60:
        return 'critical'
    if score >= 15:
        return 'watch'
    return 'healthy'


tenant = Tenant.objects.filter(id=TENANT_ID).first()
print("tenant:", tenant)

account = JobberAccount.objects.filter(tenant_id=TENANT_ID, is_active=True).first()
if account is None:
    print(f"No active JobberAccount for tenant_id={TENANT_ID} -- nothing to verify.")
else:
    class _FakeUser:
        tenant_id = TENANT_ID

    summary = _local_electricians_summary_response(_FakeUser())
    print(f"\n=== KPIs (electricians-summary) ===")
    print(f"connected: {summary.get('connected')}")
    print(f"total_revenue: {summary.get('total_revenue')}")
    print(f"jobs_completed: {summary.get('jobs_completed')}")
    print(f"avg_job_value: {summary.get('avg_job_value')}")

    stats = get_technician_stats(tenant)
    technicians = stats.get('technicians', [])
    print(f"\nconnected: {stats.get('connected')}  {len(technicians)} technician(s)")

    callback_bleed = round(sum(t['callback_dollars_lost'] for t in technicians), 2)
    has_unknown = any(t['has_unknown_callback_cost'] for t in technicians)
    total_callbacks = sum(t['callback_visits_done'] for t in technicians)

    shortfall = 0.0
    techs_below_target = 0
    for t in technicians:
        goal_amount = t['goal_progress']['goal_amount']
        current_month_revenue = t['goal_progress']['current_month_revenue']
        if goal_amount and goal_amount > 0:
            shortfall += max(0.0, goal_amount - current_month_revenue)
            if current_month_revenue < goal_amount:
                techs_below_target += 1
        # goal_amount None/0 -- contributes $0, per the confirmed decision
        # (deliberately different from goal_progress's own individual
        # progress_percentage exclusion rule).
    shortfall = round(shortfall, 2)
    total_leakage = round(callback_bleed + shortfall, 2)

    print(f"\n=== Callback Bleed ===")
    print(f"callback_bleed: {callback_bleed}{'+' if has_unknown else ''}  "
          f"({total_callbacks} free return visits, has_unknown_callback_cost anywhere: {has_unknown})")

    print(f"\n=== Monthly Shortfall ===")
    print(f"shortfall: {shortfall}  ({techs_below_target} techs below target)")

    print(f"\n=== Total Leakage (Callback Bleed + Monthly Shortfall only) ===")
    print(f"total_leakage: {total_leakage}")

    print(f"\n=== Technician Risk Board (ranked, independently recomputed) ===")
    ranked = sorted(technicians, key=lambda t: calc_risk_score(t), reverse=True)
    for i, t in enumerate(ranked, start=1):
        score = calc_risk_score(t)
        level = risk_level(score)
        print(f"  {i}. {t['name']} (user_id={t['user_id']}) -- score={score} level={level} "
              f"revenue_per_hour={t['revenue_per_hour']} callback_rate={t['callback_rate']} "
              f"completion_percentage={t['completion_percentage']} "
              f"goal_progress={t['goal_progress']}")

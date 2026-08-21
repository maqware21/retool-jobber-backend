from apps.alerts.models import AlertRule
from apps.jobber.api.technician_stats import get_technician_stats

# Sort rank for the one required ordering rule (2026-08-21, confirmed
# TL decision): Critical always before Warning, everywhere a
# triggered-alerts list is displayed. Sorted ONCE here, server-side --
# every consumer (AlarmPanel, the technician drawer's Active Alerts
# section) reads this same already-sorted list via GET
# /v1/alerts/triggered/, so neither has to re-sort it itself.
_SEVERITY_SORT_RANK = {'critical': 0, 'warning': 1}


def _actual_value(rule_type, tech, team_avg_revenue):
    """
    The real, already-computed number to compare rule.threshold_value
    against, or None if there's no data this window (never a fabricated
    0 -- same "no data != 0" convention used everywhere else in this
    project). Every branch reads a field get_technician_stats() already
    computed for the Electricians panel's own cards -- nothing here is
    re-derived.

    team_avg_revenue is passed in, not recomputed per rule/technician --
    it's the same value for every TEAM_AVG_REVENUE_PCT check this call
    runs.
    """
    if rule_type == 'monthly_goal_pct':
        return tech['goal_progress']['progress_percentage']
    if rule_type == 'annual_goal_pct':
        return tech['annual_goal_progress']['progress_percentage']
    if rule_type == 'completion_rate_pct':
        return tech['completion_percentage']
    if rule_type == 'revenue_per_hour':
        return tech['revenue_per_hour']
    if rule_type == 'team_avg_revenue_pct':
        if team_avg_revenue <= 0:
            return None
        return round((tech['revenue'] / team_avg_revenue) * 100, 1)
    # A future rule_type with no evaluator branch yet -- skip, don't crash.
    return None


def evaluate_alert_rules(tenant):
    """
    Returns every currently-triggered alert for `tenant`'s active,
    enabled AlertRules.

    Each AlertRule is a COMPANY-WIDE policy (2026-08-21, confirmed TL
    correction), not tied to one named technician -- so for EACH active
    rule, this evaluates EVERY active technician against it and emits
    one triggered entry per technician who crosses the threshold. A
    single rule can legitimately produce MULTIPLE triggered entries in
    one call (one per violating technician) -- intentional, not a bug;
    it can also produce zero if nobody currently violates it.

    Reuses get_technician_stats(tenant) directly -- the SAME numbers
    already shown on the Electricians panel's per-tech cards (revenue,
    completion_percentage, revenue_per_hour, goal_progress,
    annual_goal_progress). Nothing here re-derives any of that math; a
    single call produces every rule's input, for every technician.

    Returns [] both when Jobber isn't connected and when nothing is
    triggered -- deliberately the same shape either way, no separate
    "connected" flag. An empty list is already the correct thing for
    AlarmPanel (renders nothing) and a technician card's alertCount
    ("No open alerts", which is literally true) in both cases. Matches
    this project's Goals endpoints' own convention (no connected
    envelope for customer-owned settings data) rather than the Jobber
    live-proxy/local-sync endpoints' convention (which need `connected`
    because they show a "connect Jobber" prompt state that Alerts has no
    equivalent of).

    Nothing is collapsed or hidden (2026-08-21, confirmed TL decision) --
    every triggered entry is returned. The only ordering guarantee is
    Critical before Warning; within the same severity, entries keep
    whatever order they were generated in (rules ordered by `id`,
    technicians already ordered by name via get_technician_stats()) --
    a stable sort (list.sort(), guaranteed stable) preserves that
    relative order rather than reshuffling it, so repeated calls against
    the same underlying data return the same order every time.
    """
    if tenant is None:
        return []

    # Explicit order_by -- without one, row order from the DB isn't
    # guaranteed deterministic across calls, which would silently break
    # the "stable secondary order" requirement above even though the
    # severity-sort itself would still be correct.
    rules = AlertRule.objects.filter(tenant=tenant, is_active=True, is_enabled=True).order_by('id')
    if not rules:
        return []

    stats = get_technician_stats(tenant)
    technicians = stats['technicians']
    if not technicians:
        return []

    team_revenue_total = sum(t['revenue'] for t in technicians)
    # EVERY active technician counts toward this denominator, INCLUDING
    # $0-revenue ones this window -- confirmed decision, matches the
    # Employees roster's own "seed every real technician, never hide
    # zero-activity ones" convention.
    team_avg_revenue = team_revenue_total / len(technicians)

    triggered = []
    for rule in rules:
        for tech in technicians:
            actual = _actual_value(rule.rule_type, tech, team_avg_revenue)
            if actual is None:
                continue  # no data this window for this technician -- not a trigger, not an error

            if actual < float(rule.threshold_value):
                triggered.append({
                    'rule_id': rule.id,
                    'rule_type': rule.rule_type,
                    'rule_type_display': rule.get_rule_type_display(),
                    'severity': rule.severity,
                    'user_id': tech['user_id'],
                    'user_name': tech['name'],
                    'threshold_value': float(rule.threshold_value),
                    'actual_value': actual,
                })

    # Critical before Warning, everywhere -- sorted ONCE, here. A stable
    # sort (list.sort()'s guarantee) leaves each severity group's
    # internal order exactly as generated above (rule id, then
    # technician name) rather than reshuffling it.
    triggered.sort(key=lambda t: _SEVERITY_SORT_RANK[t['severity']])
    return triggered

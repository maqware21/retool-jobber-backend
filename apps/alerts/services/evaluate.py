from apps.alerts.models import AlertRule
from apps.jobber.api.technician_stats import get_technician_stats


def _actual_value(rule_type, tech, team_avg_revenue):
    """
    The real, already-computed number to compare rule.threshold_value
    against, or None if there's no data this window (never a fabricated
    0 -- same "no data != 0" convention used everywhere else in this
    project). Every branch reads a field get_technician_stats() already
    computed for the Electricians panel's own cards -- nothing here is
    re-derived.

    team_avg_revenue is passed in, not recomputed per rule -- it's the
    same value for every TEAM_AVG_REVENUE_PCT rule this call evaluates.
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
    enabled AlertRules -- each rule whose real, current value is below
    its threshold_value.

    Reuses get_technician_stats(tenant) directly -- the SAME numbers
    already shown on the Electricians panel's per-tech cards (revenue,
    completion_percentage, revenue_per_hour, goal_progress,
    annual_goal_progress). Nothing here re-derives any of that math; a
    single call produces every rule's input.

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
    """
    if tenant is None:
        return []

    rules = AlertRule.objects.filter(
        tenant=tenant, is_active=True, is_enabled=True,
    ).exclude(user__isnull=True).select_related('user')
    if not rules:
        return []

    stats = get_technician_stats(tenant)
    by_user_id = {t['user_id']: t for t in stats['technicians']}

    team_revenue_total = sum(t['revenue'] for t in stats['technicians'])
    # EVERY active technician counts toward this denominator, INCLUDING
    # $0-revenue ones this window -- confirmed decision, matches the
    # Employees roster's own "seed every real technician, never hide
    # zero-activity ones" convention.
    team_avg_revenue = (
        team_revenue_total / len(stats['technicians'])
        if stats['technicians'] else 0
    )

    triggered = []
    for rule in rules:
        tech = by_user_id.get(rule.user_id)
        if tech is None:
            continue  # technician deactivated/removed since the rule was made

        actual = _actual_value(rule.rule_type, tech, team_avg_revenue)
        if actual is None:
            continue  # no data this window -- not a trigger, not an error

        if actual < float(rule.threshold_value):
            triggered.append({
                'rule_id': rule.id,
                'rule_type': rule.rule_type,
                'rule_type_display': rule.get_rule_type_display(),
                'severity': rule.severity,
                'user_id': rule.user_id,
                'user_name': tech['name'],
                'threshold_value': float(rule.threshold_value),
                'actual_value': actual,
            })
    return triggered

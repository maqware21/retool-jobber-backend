DEFAULT_LIMIT = 20
MAX_PAGE_SIZE = 100
MAX_OFFSET = 100
MIN_LIMIT = 1
MIN_OFFSET = 0

# Roles stored as Django Permission objects (codename, name).
# Index positions are used in user_permissions.py — do not reorder.
# Index 0 = admin, Index 1 = customer
USER_PERMISSIONS = [
    ('admin', 'admin'),
    ('customer', 'customer'),
]

# JobberSyncRun.status. Index positions are used directly (e.g.
# JOBBER_SYNC_STATUS[0][0]) — do not reorder.
# Index 0 = running, 1 = success, 2 = partial, 3 = failed.
JOBBER_SYNC_STATUS = [
    ('running', 'running'),
    ('success', 'success'),
    ('partial', 'partial'),
    ('failed', 'failed'),
]

# AlertRule.rule_type. Not position-dependent (evaluate_alert_rules()
# branches on the string key, never an index) -- safe to reorder or
# append to. The label half doubles as the frontend's rule-type dropdown
# text, via GET /v1/alerts/rules/'s rule_types list, so there's one
# source of truth for that copy, not a duplicated frontend constant.
#
# Explicitly NOT included yet: callback-rate and drive-time rule types --
# both genuinely blocked (see PROJECT_CONTEXT.md), not even added as
# disabled placeholders, since a selectable-but-never-evaluated choice
# would silently never fire.
ALERT_RULE_TYPES = [
    ('monthly_goal_pct', 'Monthly goal below X%'),
    ('annual_goal_pct', 'Annual goal below X%'),
    ('completion_rate_pct', 'Completion rate below X%'),
    ('revenue_per_hour', 'Revenue/hr below $X'),
    ('team_avg_revenue_pct', 'Revenue below X% of team average'),
]

# Every rule_type currently supported is per-technician (confirmed
# directly -- none of the 5 above alert on a company-wide metric; #5
# alerts per-technician even though its comparison baseline is a
# company-wide average). Used by AlertRuleSerializer to validate that
# `user` is set for these types, and by evaluate_alert_rules() to skip
# any rule whose type isn't in this set yet. A future company-wide
# rule_type simply wouldn't be added here -- no schema change needed.
ALERT_RULE_TYPES_REQUIRING_USER = {key for key, _ in ALERT_RULE_TYPES}

ALERT_SEVERITY_CHOICES = [
    ('critical', 'critical'),
    ('warning', 'warning'),
]

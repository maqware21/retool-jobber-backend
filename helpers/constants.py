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
# Every rule_type here is a COMPANY-WIDE policy (2026-08-21, confirmed
# TL correction) -- automatically evaluated against EVERY active
# technician by evaluate_alert_rules(), not tied to one named person at
# creation time. (There is no longer an "ALERT_RULE_TYPES_REQUIRING_USER"
# concept -- that belonged to the earlier, incorrect per-technician-rule
# design.)
#
# Explicitly NOT included yet: drive-time rule type -- still genuinely
# blocked (no real data source at all, see PROJECT_CONTEXT.md), not even
# added as a disabled placeholder, since a selectable-but-never-evaluated
# choice would silently never fire.
#
# callback_rate_above_pct (2026-09-03, approved) -- the first rule type
# whose real trigger direction is "above" the threshold, not "below" it
# like every other rule here (a high callback rate is bad; a low value
# everywhere else is bad). See evaluate.py's _RULE_TYPE_DIRECTION for the
# comparison this requires -- do not add a new rule_type here without
# also adding its direction there, or it will silently evaluate with the
# wrong comparison operator.
#
# last_month_goal_pct (2026-09-04, approved) -- a SEPARATE rule type from
# monthly_goal_pct, not a replacement or variant of it. monthly_goal_pct
# evaluates the CURRENT calendar month's progress against a goal that's
# still ongoing; this evaluates the PRIOR calendar month's FINAL result --
# a "missed it" check, not a "how's it going" check. Direction is 'below'
# (see evaluate.py's _RULE_TYPE_DIRECTION) -- same as monthly_goal_pct.
ALERT_RULE_TYPES = [
    ('monthly_goal_pct', 'Monthly goal below X%'),
    ('annual_goal_pct', 'Annual goal below X%'),
    ('completion_rate_pct', 'Completion rate below X%'),
    ('revenue_per_hour', 'Revenue/hr below $X'),
    ('team_avg_revenue_pct', 'Revenue below X% of team average'),
    ('callback_rate_above_pct', 'Callback rate above X%'),
    ('last_month_goal_pct', "Last month's goal below X%"),
]

ALERT_SEVERITY_CHOICES = [
    ('critical', 'critical'),
    ('warning', 'warning'),
]

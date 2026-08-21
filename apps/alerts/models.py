from django.db import models

from helpers.constants import ALERT_RULE_TYPES, ALERT_SEVERITY_CHOICES
from helpers.models import DateModel


class AlertRule(DateModel):
    """
    One customer-configured alert rule. A SINGLE generic model covers all
    rule types (not one model per type) -- every type needs exactly the
    same 4 real fields (technician, threshold, severity, enabled) and
    differs only in which comparison evaluate_alert_rules() runs for it,
    not in shape. Unlike Goals' Monthly/Annual split, nothing Alerts-
    related was already shipped when this was designed, so there was no
    live code protecting a narrower shape -- see PROJECT_CONTEXT.md for
    the full reasoning against separate models per type.

    OUR OWN data, entered directly by the customer -- NOT synced from
    Jobber, never wired into sync_tenant()/ensure_fresh().

    `user` is nullable even though every currently-supported rule_type
    requires one (confirmed: all 5 are per-technician, none company-wide
    today) -- kept nullable so a future company-wide rule_type can use
    user=None without a schema change. Which rule_types require a user
    is a plain Python set (helpers.constants.ALERT_RULE_TYPES_REQUIRING_USER),
    validated in AlertRuleSerializer -- not a DB constraint, same
    "serializer validates, no DB-level check" convention already used
    for goal_amount >= 0 elsewhere in this project.

    is_enabled is a SEPARATE field from DateModel's own is_active,
    deliberately -- is_active stays reserved for this project's existing
    soft-delete convention (AlertRuleDetailView.delete() sets is_active
    False, never a real SQL DELETE, same as every other model here).
    is_enabled is the customer's own on/off toggle for a rule they want
    to keep but pause. Conflating the two would make "pause" and
    "delete" the same action, which they are not.

    severity is customer-chosen per rule, not derived from rule_type or
    threshold_value -- this is how a customer reproduces the old mock
    UI's 2-tier critical/warning pattern (e.g. "below 70% = critical,
    below 85% = warning") with zero hardcoded tier logic: they create
    TWO rules, same rule_type and technician, two different
    threshold_value/severity pairs. Nothing in evaluate_alert_rules()
    branches on "which severity fires at which number" -- the customer
    owns that entirely.

    No uniqueness constraint, on purpose -- unlike Goals' (tenant, month),
    "at most one rule per (type, technician)" is not a real invariant
    here; the 2-tier pattern above depends on allowing duplicates.
    """

    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='alert_rules',
    )
    rule_type = models.CharField(max_length=30, choices=ALERT_RULE_TYPES)
    user = models.ForeignKey(
        'jobber.JobberUser',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='alert_rules',
    )
    # Shared by both percentage-based rule types (goal %, completion %,
    # team-avg %) and the one dollar-based type (revenue/hr) -- the unit
    # is implied by rule_type, not by this field, so one column suffices
    # instead of two near-duplicate ones.
    threshold_value = models.DecimalField(max_digits=12, decimal_places=2)
    severity = models.CharField(max_length=10, choices=ALERT_SEVERITY_CHOICES)
    is_enabled = models.BooleanField(default=True)

    class Meta:
        db_table = 'alert_rules'
        verbose_name = 'alert rule'
        verbose_name_plural = 'alert rules'

    def __str__(self):
        return f"AlertRule(tenant={self.tenant_id}, rule_type={self.rule_type}, user={self.user_id})"

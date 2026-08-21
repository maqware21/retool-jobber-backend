from django.db import models

from helpers.constants import ALERT_RULE_TYPES, ALERT_SEVERITY_CHOICES
from helpers.models import DateModel


class AlertRule(DateModel):
    """
    One customer-configured, COMPANY-WIDE alert policy (2026-08-21,
    confirmed TL correction -- rules are NOT tied to one named
    technician at creation time; see below). A SINGLE generic model
    covers all rule types (not one model per type) -- every type needs
    exactly the same 3 real fields (threshold, severity, enabled) and
    differs only in which comparison evaluate_alert_rules() runs for it,
    not in shape. Unlike Goals' Monthly/Annual split, nothing Alerts-
    related was already shipped when this was designed, so there was no
    live code protecting a narrower shape -- see PROJECT_CONTEXT.md for
    the full reasoning against separate models per type.

    OUR OWN data, entered directly by the customer -- NOT synced from
    Jobber, never wired into sync_tenant()/ensure_fresh().

    NO `user` FIELD, on purpose -- a rule is a policy ("monthly goal
    below 70% = critical"), automatically evaluated against EVERY active
    technician by evaluate_alert_rules(), not a one-time pick of a
    single named person at creation time. This field existed briefly in
    migration 0001 under the original (incorrect) "per-technician rule"
    design and was removed in 0002 before any real row existed in
    production -- a clean schema fix, not a data migration.

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
    TWO rules, same rule_type, two different threshold_value/severity
    pairs, each evaluated against every technician independently.
    Nothing in evaluate_alert_rules() branches on "which severity fires
    at which number" -- the customer owns that entirely.

    No uniqueness constraint, on purpose -- unlike Goals' (tenant, month),
    "at most one rule per type" is not a real invariant here; the 2-tier
    pattern above depends on allowing duplicates.
    """

    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='alert_rules',
    )
    rule_type = models.CharField(max_length=30, choices=ALERT_RULE_TYPES)
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
        return f"AlertRule(tenant={self.tenant_id}, rule_type={self.rule_type}, threshold={self.threshold_value})"

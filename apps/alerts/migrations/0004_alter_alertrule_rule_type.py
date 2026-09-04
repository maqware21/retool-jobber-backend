# Hand-written (this project's migrations are never makemigrations-
# generated and never run by the assistant — see settings_migration_test.py's
# own docstring for why, and verify against that throwaway SQLite setup
# before this is run for real).
#
# choices= is Python/validation-level only (no real DB schema impact),
# but Django's migration state still needs to record it to stay in sync
# with the model. Adds 'last_month_goal_pct' (2026-09-04, approved
# last_month_goal_alert_proposal.md) to AlertRule.rule_type's real
# choices, alongside the 6 already there — confirmed via
# `makemigrations --check --dry-run` against the throwaway SQLite
# settings that this is the ONLY change needed (no other model drift in
# this app). Same exact pattern as 0003_alter_alertrule_rule_type.py,
# which added callback_rate_above_pct the same way.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("alerts", "0003_alter_alertrule_rule_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="alertrule",
            name="rule_type",
            field=models.CharField(
                choices=[
                    ("monthly_goal_pct", "Monthly goal below X%"),
                    ("annual_goal_pct", "Annual goal below X%"),
                    ("completion_rate_pct", "Completion rate below X%"),
                    ("revenue_per_hour", "Revenue/hr below $X"),
                    ("team_avg_revenue_pct", "Revenue below X% of team average"),
                    ("callback_rate_above_pct", "Callback rate above X%"),
                    ("last_month_goal_pct", "Last month's goal below X%"),
                ],
                max_length=30,
            ),
        ),
    ]

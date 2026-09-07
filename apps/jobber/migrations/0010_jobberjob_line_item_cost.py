# Hand-written (this project's migrations are never makemigrations-
# generated and never run by the assistant — see settings_migration_test.py's
# own docstring for why, and verify against that throwaway SQLite setup
# before this is run for real).
#
# Adds JobberJob.line_item_cost for the approved Revenue Composition
# expense/profit split (revenue_composition_expense_profit_proposal.md) —
# Jobber's own real, native jobCosting.lineItemCost, confirmed genuine and
# non-circular (2026-09-07, see PROJECT_CONTEXT.md's own update) — a
# DIFFERENT, working field from labour_cost (already confirmed broken).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobber", "0009_jobbertimesheetentry_labour_rate"),
    ]

    operations = [
        migrations.AddField(
            model_name="jobberjob",
            name="line_item_cost",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
    ]

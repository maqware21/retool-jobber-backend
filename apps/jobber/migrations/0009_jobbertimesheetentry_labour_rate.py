# Hand-written (this project's migrations are never makemigrations-
# generated and never run by the assistant — see settings_migration_test.py's
# own docstring for why, and verify against that throwaway SQLite setup
# before this is run for real).
#
# Adds JobberTimeSheetEntry.labour_rate for the approved Profit Margin
# design (labor_cost_profit_margin_proposal.md) — Jobber's own real,
# native per-entry wage rate (TimeSheetEntry.labourRate), reused directly
# instead of building a new first-party wage field.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobber", "0008_callback_tracking_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="jobbertimesheetentry",
            name="labour_rate",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
    ]

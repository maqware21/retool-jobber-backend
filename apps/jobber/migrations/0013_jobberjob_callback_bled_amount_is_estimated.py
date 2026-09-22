# Hand-written (this project's migrations are never makemigrations-
# generated and never run by the assistant — see settings_migration_test.py's
# own docstring for why, and verify against that throwaway SQLite setup
# before this is run for real).
#
# Adds JobberJob.callback_bled_amount_is_estimated -- distinguishes a
# callback_bled_amount computed from the callback visit's real scheduled
# time (no logged hours existed) from one computed from real logged
# hours, or from callback_bled_amount still being null (unknown). See
# callback_scheduled_time_fallback_proposal.md and the field's own model
# comment for the full reasoning.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobber", "0012_jobbersyncrun_expenses_synced_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="jobberjob",
            name="callback_bled_amount_is_estimated",
            field=models.BooleanField(default=False),
        ),
    ]

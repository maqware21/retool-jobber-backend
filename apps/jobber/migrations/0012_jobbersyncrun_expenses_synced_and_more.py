# Hand-written (this project's migrations are never makemigrations-
# generated and never run by the assistant — see settings_migration_test.py's
# own docstring for why, and verify against that throwaway SQLite setup
# before this is run for real).
#
# Adds JobberSyncRun.timesheet_entries_synced / .expenses_synced
# (2026-09-15, approved manual_sync_and_faster_staleness_proposal.md) —
# closes a real, previously-deliberate gap: these 2 entities were always
# synced but never had their own persisted count column, unlike the 5
# already there. Needed for the new manual "Sync Now" endpoint's real
# response message to honestly report a real expense/timesheet-entry
# count, same reasoning as 0011_jobberexpense.py's own model comment.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobber", "0011_jobberexpense"),
    ]

    operations = [
        migrations.AddField(
            model_name="jobbersyncrun",
            name="timesheet_entries_synced",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="jobbersyncrun",
            name="expenses_synced",
            field=models.IntegerField(default=0),
        ),
    ]

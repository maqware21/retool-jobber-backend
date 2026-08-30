# Hand-written (this project's migrations are never makemigrations-generated
# and never run by the assistant — see settings_migration_test.py's own
# docstring for why, and verify against that throwaway SQLite setup before
# this is run for real).
#
# Adds the 4 fields for the approved Callback Bleed design
# (callback_hours_design.md): JobberJob.first_archived_at (the frozen
# original/callback boundary), JobberTimeSheetEntry.jobber_created_at (the
# real per-entry timestamp the split is computed against —
# TimeSheetEntry.visit confirmed unreliable for this, see
# verify_job1_manual_entry_visit.py), JobberVisit.is_callback (frozen once,
# never re-evaluated), and JobberJob.callback_bled_amount (the frozen dollar
# result, same DecimalField(12, 2) money-field convention as
# total/amount/labour_cost/balance elsewhere in this app).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobber", "0007_jobberuser_expertise_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="jobberjob",
            name="first_archived_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="jobberjob",
            name="callback_bled_amount",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="jobbertimesheetentry",
            name="jobber_created_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="jobbervisit",
            name="is_callback",
            field=models.BooleanField(default=False),
        ),
    ]

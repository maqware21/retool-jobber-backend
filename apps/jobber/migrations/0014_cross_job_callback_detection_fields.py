# Hand-written (this project's migrations are never makemigrations-
# generated and never run by the assistant -- see settings_migration_test.py's
# own docstring for why, and verify against that throwaway SQLite setup
# before this is run for real).
#
# Adds the real schema needed for cross_job_callback_detection.py (see
# cross_job_callback_detection_replacement_plan.md for the full design):
#
#   JobberJob.property_id -- Jobber's own real Property id (EncodedId),
#     a real, exact identity key for Signal 1 (same address). Separate
#     from the existing, pre-flattened `address` display string.
#   JobberJob.cross_job_callback_of -- nullable self-FK to the earlier
#     JobberJob this one is a real callback of. Frozen exactly once.
#   JobberJob.cross_job_callback_resolved -- True once this job's 30-day
#     candidate window has been permanently decided, one way or the
#     other (see that field's own model comment for why this detection
#     needs an explicit resolved state the old system never needed).
#   JobberJob.cross_job_callback_bled_amount /
#   JobberJob.cross_job_callback_bled_amount_is_estimated -- the new
#     system's own dollar figure, deliberately separate fields from the
#     old callback_bled_amount/_is_estimated (2 different detection
#     systems, 2 different owners, never ambiguous which wrote a value).
#   JobberVisit.title / JobberVisit.instructions -- 2 of Signal 3's 4
#     real keyword-match locations. JobberVisit carried no text fields
#     at all before this.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("jobber", "0013_jobberjob_callback_bled_amount_is_estimated"),
    ]

    operations = [
        migrations.AddField(
            model_name="jobberjob",
            name="property_id",
            field=models.CharField(max_length=255, null=True, blank=True),
        ),
        migrations.AddField(
            model_name="jobberjob",
            name="cross_job_callback_resolved",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="jobberjob",
            name="cross_job_callback_bled_amount",
            field=models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True),
        ),
        migrations.AddField(
            model_name="jobberjob",
            name="cross_job_callback_bled_amount_is_estimated",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="jobberjob",
            name="cross_job_callback_of",
            field=models.ForeignKey(
                to="jobber.jobberjob",
                on_delete=django.db.models.deletion.SET_NULL,
                null=True,
                blank=True,
                related_name="cross_job_callbacks",
            ),
        ),
        migrations.AddField(
            model_name="jobbervisit",
            name="title",
            field=models.CharField(max_length=255, null=True, blank=True),
        ),
        migrations.AddField(
            model_name="jobbervisit",
            name="instructions",
            field=models.TextField(null=True, blank=True),
        ),
    ]

# Hand-written (this project's migrations are never makemigrations-
# generated and never run by the assistant -- see settings_migration_test.py's
# own docstring for why, and verify against that throwaway SQLite setup
# before this is run for real).
#
# Adds JobberAccount.pending_resync_marked_at -- the DB-backed coalescing
# marker for background_sync.py's webhook-triggered syncs (see
# cross_job_callback_event_driven_plan.md section 3 and the field's own
# model comment for the full design). A plain nullable timestamp, no
# other schema impact.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobber", "0014_cross_job_callback_detection_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="jobberaccount",
            name="pending_resync_marked_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
    ]

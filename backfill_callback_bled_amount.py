"""
One-off backfill (2026-08-30) -- corrects callback_bled_amount for any
job whose value was computed by the pre-fix version of
detect_and_freeze_callbacks() (before this same round's "empty
callback_by_user -> None, not a fabricated 0.00" fix). Run via
`python manage.py shell < backfill_callback_bled_amount.py`.

Does NOT touch first_archived_at or is_callback -- both are already real,
frozen, correct values (the detection itself was never wrong -- job 3's
visit genuinely IS the callback). Only callback_bled_amount is
recomputed, using the exact same (now-fixed) formula
detect_and_freeze_callbacks() itself uses, and only WRITTEN if it
actually changes -- this is a data correction for the bug's already-
computed output, not a re-run of detection.

Scope: every JobberJob with first_archived_at set and at least one real
is_callback=True visit -- the same population detect_and_freeze_callbacks()
itself computes callback_bled_amount for.
"""
from decimal import Decimal

from apps.jobber.api.electricians_summary import calculate_job_duration_by_user
from apps.jobber.models import JobberJob, JobberVisit


def _to_decimal(value):
    if value is None:
        return None
    return Decimal(str(value))


jobs = JobberJob.objects.filter(first_archived_at__isnull=False, is_active=True)
checked = 0
corrected = 0
for job in jobs:
    if not JobberVisit.objects.filter(job=job, is_callback=True, is_active=True).exists():
        continue
    checked += 1

    original_by_user = calculate_job_duration_by_user(job, created_before=job.first_archived_at)
    callback_by_user = calculate_job_duration_by_user(job, created_at_or_after=job.first_archived_at)
    original_hours = sum(original_by_user.values()) / 3600
    callback_hours = sum(callback_by_user.values()) / 3600

    correct_amount = None
    if callback_by_user and original_hours > 0 and job.total is not None:
        job_rate = job.total / _to_decimal(original_hours)
        correct_amount = job_rate * _to_decimal(callback_hours)

    if job.callback_bled_amount != correct_amount:
        print(
            f"tenant_id={job.tenant_id} job_number={job.job_number}: "
            f"{job.callback_bled_amount!r} -> {correct_amount!r}"
        )
        job.callback_bled_amount = correct_amount
        job.save(update_fields=['callback_bled_amount'])
        corrected += 1

print(f"\nChecked {checked} job(s) with a flagged callback, corrected {corrected}.")

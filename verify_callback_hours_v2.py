"""
End-to-end proof for the approved callback_hours_design.md build
(2026-08-30). Run via `python manage.py shell < verify_callback_hours_v2.py`
AFTER: (1) you've run migration 0008 for real, and (2) a fresh sync has
run for the relevant tenant/account since then (e.g. via ensure_fresh() on
a real request, or by calling sync_tenant(account) directly) -- the new
fields (JobberJob.first_archived_at/callback_bled_amount,
JobberTimeSheetEntry.jobber_created_at, JobberVisit.is_callback) are only
populated going forward from the next sync pass, not backfilled by the
migration itself.

Unlike every earlier verification round in this investigation, this one
needs NO live Jobber API calls at all -- that's the actual point of this
build: once first_archived_at/jobber_created_at/is_callback/
callback_bled_amount are synced, the real answer is sitting in local
tables already. This script only reads them, then independently
RECOMPUTES the same numbers via calculate_job_duration_by_user() (with
its new created_before/created_at_or_after params) to prove
callback_bled_amount is real and correctly derived, not just present.

Checks job_number in (3, 5, 6) across ALL tenants (not hardcoded to one
tenant_id) -- deliberately, since it's not certain in advance which
tenant/account any of these three real job numbers now belongs to for
this check; each result prints its own tenant_id so nothing is silently
assumed.
"""
from apps.jobber.api.electricians_summary import calculate_job_duration_by_user
from apps.jobber.models import JobberJob, JobberVisit

TARGET_JOB_NUMBERS = (3, 5, 6)

candidates = list(JobberJob.objects.filter(job_number__in=TARGET_JOB_NUMBERS, is_active=True))
print(f"Found {len(candidates)} real JobberJob row(s) for job_number in {TARGET_JOB_NUMBERS}:")
for job in candidates:
    print(f"  tenant_id={job.tenant_id} job_number={job.job_number} jobber_id={job.jobber_id} "
          f"job_status={job.job_status!r} first_archived_at={job.first_archived_at}")

ready = [j for j in candidates if j.first_archived_at is not None]
print(f"\n{len(ready)}/{len(candidates)} have first_archived_at set (i.e. have been through at least one "
      "sync pass since migration 0008 while archived).")

if not ready:
    print(
        "\nNone are ready yet. This is expected if no sync has run since the migration was applied "
        "-- trigger one (e.g. hit an endpoint that calls ensure_fresh() for the relevant tenant, or "
        "run sync_tenant(account) directly) and re-run this script."
    )

for job in ready:
    print(f"\n=== tenant_id={job.tenant_id} job_number={job.job_number} (jobber_id={job.jobber_id}) ===")
    print(f"job_status={job.job_status!r}  total={job.total}  first_archived_at={job.first_archived_at}")
    print(f"Stored callback_bled_amount: {job.callback_bled_amount}")

    visits = list(JobberVisit.objects.filter(job=job, is_active=True))
    callback_visits = [v for v in visits if v.is_callback]
    print(f"Visits: {len(visits)} total, {len(callback_visits)} flagged is_callback=True: "
          f"{[v.jobber_id for v in callback_visits]}")

    # --- Independent recomputation, same pattern as every prior verification ---
    original_by_user = calculate_job_duration_by_user(job, created_before=job.first_archived_at)
    callback_by_user = calculate_job_duration_by_user(job, created_at_or_after=job.first_archived_at)
    original_hours = sum(original_by_user.values()) / 3600
    callback_hours = sum(callback_by_user.values()) / 3600

    print(f"\nOriginal hours (entries created before first_archived_at): {original_hours:.4f}h  {original_by_user}")
    print(f"Callback hours (entries created at/after first_archived_at): {callback_hours:.4f}h  {callback_by_user}")

    recomputed_bled = None
    if original_hours > 0 and job.total is not None:
        from decimal import Decimal
        job_rate = job.total / Decimal(str(original_hours))
        recomputed_bled = job_rate * Decimal(str(callback_hours))
        print(f"Job-specific rate (recomputed): {job.total} / {original_hours:.4f}h = ${job_rate:.2f}/hr")
        print(f"Recomputed Callback Bleed: {callback_hours:.4f}h x ${job_rate:.2f}/hr = ${recomputed_bled:.2f}")
    else:
        print("Cannot recompute a rate -- zero original hours or missing Job.total. Real inputs reported as-is.")

    if job.callback_bled_amount is None and recomputed_bled is None:
        print("MATCH (both null -- no real callback amount to compute, consistent with real inputs).")
    elif job.callback_bled_amount is None or recomputed_bled is None:
        print(f"MISMATCH -- stored={job.callback_bled_amount!r} recomputed={recomputed_bled!r}")
    else:
        status = "MATCH" if round(float(job.callback_bled_amount), 2) == round(float(recomputed_bled), 2) else "MISMATCH"
        print(f"Stored={job.callback_bled_amount} vs recomputed={recomputed_bled:.2f} -> {status}")

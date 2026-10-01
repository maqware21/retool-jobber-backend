"""
Real verification, not a dry run -- calls the REAL, newly-wired
detect_cross_job_callbacks() directly against tenant_id=6's real data
(the same test jobs already used in
research_alternative_callback_detection.py), then reports every real
job's post-call state and compares it against that research script's
own already-proven Rule D result (address AND client AND keyword, all
3 together -- the exact rule this production function implements).

This DOES write real, permanent data (cross_job_callback_of /
cross_job_callback_resolved / cross_job_callback_bled_amount /
cross_job_callback_bled_amount_is_estimated) -- it is calling the real
production function sync_tenant() will now call on every pass, not a
read-only check. Expected per the research script's own last real run
(10 jobs): only job #5 (E2) should match, against job #4 (E1); every
other job should resolve to no match (or remain unresolved if still
inside its own 30-day window).

Run via:
    python manage.py shell < verify_cross_job_callback_detection.py
on the SERVER. Delete afterward, same as every other one-off
verification in this project.
"""
from apps.jobber.models import JobberAccount, JobberJob
from apps.jobber.services.cross_job_callback_detection import detect_cross_job_callbacks

TENANT_ID = 6

# From research_alternative_callback_detection.py's own last real run
# against this exact tenant -- Rule D (address AND client AND keyword)
# matched ONLY job #5, against job #4. Nothing else matched under Rule D.
EXPECTED_MATCH = {5: 4}

account = JobberAccount.objects.filter(tenant_id=TENANT_ID, is_active=True).first()
if account is None:
    print(f"No active JobberAccount for tenant_id={TENANT_ID}.")
else:
    tenant = account.tenant
    print(f"=== calling detect_cross_job_callbacks() for real, tenant_id={TENANT_ID} ===\n")
    result = detect_cross_job_callbacks(account, tenant)
    print(f"result = {result}\n")

    jobs = JobberJob.objects.filter(tenant_id=TENANT_ID, is_active=True).order_by('job_number')
    print(f"{'Job':>6} | {'Resolved':>8} | {'Callback of':>12} | {'Bled amount':>14} | Estimated")
    print('-' * 70)
    for job in jobs:
        of_number = job.cross_job_callback_of.job_number if job.cross_job_callback_of else '-'
        print(
            f"{'#' + str(job.job_number):>6} | {str(job.cross_job_callback_resolved):>8} | "
            f"{str(of_number):>12} | {str(job.cross_job_callback_bled_amount):>14} | "
            f"{job.cross_job_callback_bled_amount_is_estimated}"
        )

    print("\n=== comparison against research_alternative_callback_detection.py's Rule D ===")
    all_good = True
    for job in jobs:
        expected_of = EXPECTED_MATCH.get(job.job_number)
        actual_of = job.cross_job_callback_of.job_number if job.cross_job_callback_of else None
        if expected_of is None and actual_of is not None:
            all_good = False
            print(f"  MISMATCH: job #{job.job_number} matched job #{actual_of}, but Rule D found no match for it.")
        elif expected_of is not None and actual_of != expected_of:
            all_good = False
            print(
                f"  MISMATCH: job #{job.job_number} expected to match job #{expected_of} "
                f"(per Rule D), but real result was {actual_of}."
            )
        elif expected_of is not None:
            print(f"  AGREE: job #{job.job_number} matched job #{expected_of}, same as Rule D.")

    print(f"\nOverall: {'MATCHES Rule D exactly' if all_good else 'DOES NOT fully match Rule D -- see mismatches above'}")

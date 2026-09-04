"""
One-off recovery (2026-09-04) -- approved callback_detection_trigger_fix_
proposal.md. Run via `python manage.py shell < recover_stuck_callback_jobs.py`.

Why this is needed: before today's fix, detect_and_freeze_callbacks() was
triggered off first_archived_at's own one-time null->set transition, which
only ever fires ONCE per job's lifetime. Any job that archived cleanly (no
callback), had first_archived_at frozen, THEN reopened and got a genuine
real callback -- with an ordinary sync landing in between the two events --
never got checked at all, permanently, no matter how many syncs ran
afterward. The forward-looking fix in sync.py (job_status transition
detection, decoupled from first_archived_at) only catches this going
forward: a job already stuck in this state, with no FURTHER reopen after
the fix ships, produces no new "transition into archived" for the new
signal to see, so it stays stuck unless checked here explicitly.

This is REAL detection, not amount-recomputation -- unlike
backfill_callback_bled_amount.py (which only recomputes callback_bled_amount
for a job that ALREADY has an is_callback=True visit), this calls
detect_and_freeze_callbacks() itself, live, so it can discover a callback
that was never flagged in the first place.

Candidate selection (local, cheap, no live calls yet): every currently
archived job whose first_archived_at anchor is already set, that has NO
existing real callback flagged at all. Deliberately does NOT re-check a job
that already has one detected callback but might have a second, later one
too -- that's the separate, undecided multi-callback-amount question noted
in detect_and_freeze_callbacks()'s own docstring, not bundled into this
bug-recovery run.

detect_and_freeze_callbacks() is called completely unchanged -- the exact
same function already proven correct for the forward-going case. It safely
no-ops for a genuinely non-callback archived job (checked, correctly
ignored, at the cost of one live call) -- no risk of a false positive.

Cost: one live Query.job(id:) call per candidate job -- the same cheap,
single-job lookup this function already makes day to day, not a full-
account scan.
"""
from apps.jobber.models import JobberAccount, JobberJob, JobberVisit
from apps.jobber.services.sync import detect_and_freeze_callbacks

TENANT_ID = 4

account = JobberAccount.objects.filter(tenant_id=TENANT_ID, is_active=True).first()
if account is None:
    print(f"No active JobberAccount for tenant_id={TENANT_ID} -- nothing to do.")
else:
    tenant = account.tenant

    candidates = list(
        JobberJob.objects.filter(
            tenant=tenant, is_active=True, job_status='archived', first_archived_at__isnull=False,
        ).exclude(
            id__in=JobberVisit.objects.filter(job__tenant=tenant, is_callback=True, is_active=True).values('job_id')
        )
    )

    print(f"tenant_id={TENANT_ID}: {len(candidates)} candidate job(s) -- archived, "
          f"first_archived_at set, no callback currently flagged.")
    for job in candidates:
        print(f"  job_number={job.job_number} jobber_id={job.jobber_id} "
              f"first_archived_at={job.first_archived_at}")

    if not candidates:
        print("\nNothing to check -- no stuck candidates found.")
    else:
        result = detect_and_freeze_callbacks(account, tenant, [job.id for job in candidates])
        print(f"\ndetect_and_freeze_callbacks() result: checked={result['checked']} "
              f"flagged={result['flagged']}")

        if result['flagged']:
            newly_flagged_visits = JobberVisit.objects.filter(
                job_id__in=[job.id for job in candidates], is_callback=True, is_active=True,
            ).select_related('job')
            print("\nReal callback(s) found and frozen just now:")
            for visit in newly_flagged_visits:
                print(f"  job_number={visit.job.job_number} jobber_id={visit.job.jobber_id} "
                      f"callback_bled_amount={visit.job.callback_bled_amount!r}")
        else:
            print("\nNo real callbacks found among these candidates -- every one checked out "
                  "as a genuinely non-callback archived job.")

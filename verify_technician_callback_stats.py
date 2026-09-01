"""
Real verification (2026-08-31) for the new per-technician callback stats
in get_technician_stats() -- callback_visits_done, callback_rate,
callback_dollars_lost, has_unknown_callback_cost. Run via
`python manage.py shell < verify_technician_callback_stats.py`.

Real proof case this exists to check: Job #3 (tenant_id=4)'s real
callback is confirmed is_callback=True with callback_bled_amount=None (no
hours were ever logged on that callback visit -- the 2026-08-30 "no data
!= 0" fix). Whichever technician performed that specific callback visit
should show:
  - callback_visits_done >= 1 (Job #3's callback contributes at least 1;
    reported plainly, not assumed to be exactly 1 in case this real
    account has picked up other real callbacks since the last check)
  - a callback_rate that is exactly callback_visits_done / jobs_completed
    x 100 -- verified by independent recomputation, not just printed
  - callback_dollars_lost that reflects ONLY known amounts -- given Job #3
    is, as of the last real check, the only is_callback=True visit in
    this account, this is expected to be 0.0, reported plainly either way
  - has_unknown_callback_cost = True -- Job #3's unknown-cost callback is
    exactly the real case this flag exists for

Identifies the target technician(s) independently of
get_technician_stats() itself (directly from JobberVisit.assigned_users
on Job #3's real callback visit), so this isn't just trusting the same
code path it's verifying.
"""
from apps.jobber.api.technician_stats import get_technician_stats
from apps.jobber.models import JobberJob, JobberVisit
from apps.tenants.models import Tenant

TENANT_ID = 4
TARGET_JOB_NUMBER = 3

tenant = Tenant.objects.filter(id=TENANT_ID).first()
print("tenant:", tenant)

job3 = JobberJob.objects.filter(tenant_id=TENANT_ID, job_number=TARGET_JOB_NUMBER, is_active=True).first()
print(f"\nJob #{TARGET_JOB_NUMBER}: {job3}")

callback_visits = []
if job3:
    print(
        f"job_status={job3.job_status} first_archived_at={job3.first_archived_at} "
        f"callback_bled_amount={job3.callback_bled_amount}"
    )
    callback_visits = list(JobberVisit.objects.filter(job=job3, is_callback=True, is_active=True))
    print(f"Callback visit(s) on this job: {[v.jobber_id for v in callback_visits]}")
    for v in callback_visits:
        assignees = list(v.assigned_users.all())
        print(f"  visit={v.jobber_id} real assignees: {[(u.id, u.name) for u in assignees]}")

data = get_technician_stats(tenant)
print(f"\nconnected: {data.get('connected')}")

if not job3 or not callback_visits:
    print("\nCannot proceed to the technician-level check -- Job #3 or its callback visit not found as expected.")
else:
    target_user_ids = {u.id for v in callback_visits for u in v.assigned_users.all()}
    if not target_user_ids:
        print("\nJob #3's callback visit has no real assigned_users -- nothing to check against.")
    matched = [t for t in data.get('technicians', []) if t['user_id'] in target_user_ids]
    if not matched:
        print(f"\nNo technician in the response matches expected user_id(s) {target_user_ids} -- real mismatch, report as-is.")

    for t in matched:
        print(f"\n=== {t['name']} (user_id={t['user_id']}) ===")
        print(f"jobs_completed:            {t['jobs_completed']}")
        print(f"callback_visits_done:      {t['callback_visits_done']}")
        print(f"callback_rate:             {t['callback_rate']}")
        print(f"callback_dollars_lost:     {t['callback_dollars_lost']}")
        print(f"has_unknown_callback_cost: {t['has_unknown_callback_cost']}")

        expected_rate = (
            round((t['callback_visits_done'] / t['jobs_completed']) * 100, 1)
            if t['jobs_completed'] > 0 else None
        )

        print("\nChecks:")
        print(f"  [{'PASS' if t['callback_visits_done'] >= 1 else 'FAIL'}] callback_visits_done >= 1")
        print(
            f"  [{'PASS' if t['callback_rate'] == expected_rate else 'FAIL'}] "
            f"callback_rate ({t['callback_rate']}) == recomputed callback_visits_done/jobs_completed*100 ({expected_rate})"
        )
        print(
            f"  [{'PASS' if t['has_unknown_callback_cost'] is True else 'FAIL'}] "
            "has_unknown_callback_cost is True (Job #3's unknown-cost callback)"
        )
        print(
            f"  [INFO] callback_dollars_lost = {t['callback_dollars_lost']} "
            "(expected 0.0 if Job #3 is still the only real is_callback=True visit in this account -- "
            "report the real value either way, don't force it to match if it isn't)"
        )

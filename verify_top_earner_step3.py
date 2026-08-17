"""
Step 3 verification for Top Earner Part A (schema + refactor + pure
functions). Run via `python manage.py shell < verify_top_earner_step3.py`.

Covers:
  3a) calculate_job_duration_seconds() still returns EXACTLY the same
      values as before the refactor, for jobs 1, 2, 5, 6, 7, 12.
  3b) calculate_job_duration_by_user() returns the correct per-person
      breakdown for job 2 specifically (one person, one merged
      18000-second total).
  3d) Syncs the new assigned_users M2M for real, then reports whether the
      connected test account currently has any real visit with 2+
      assignees (informational only, per the task -- not a blocker
      either way).

(Step 3c -- split_job_revenue_among_assignees() against synthetic inputs
-- needs no DB and was already run and confirmed directly; not repeated
here.)
"""
import json

from apps.jobber.models import JobberAccount, JobberJob, JobberVisit
from apps.jobber.api.electricians_summary import calculate_job_duration_seconds, calculate_job_duration_by_user
from apps.jobber.services.sync import sync_tenant

# Standing instruction: always filter test-account lookups by tenant_id=1
# explicitly -- there are 2 real JobberAccount rows in this database
# (tenant_id=1 is the real, data-rich test account; tenant_id=3 belongs to
# a teammate and is nearly empty). Never .first() with no tenant filter.
account = JobberAccount.objects.filter(is_active=True, tenant_id=1).first()
tenant_id = account.tenant_id
print("tenant_id:", tenant_id)

# ── Step 3a: calculate_job_duration_seconds() unchanged after refactor ──────
print("\n=== Step 3a: calculate_job_duration_seconds() -- must match pre-refactor values exactly ===")
print("Expected: job 2 = 18000, jobs 1/5/6/7/12 = their own single entry's final_duration_seconds unchanged.")
EXPECTED = {1: 32400, 2: 18000, 5: 28800, 6: 28800, 7: 28800, 12: 28800}
all_match = True
for job_number in (1, 2, 5, 6, 7, 12):
    job = JobberJob.objects.filter(tenant_id=tenant_id, is_active=True, job_number=job_number).first()
    if job is None:
        print(f"job_number={job_number}: NOT FOUND locally")
        all_match = False
        continue
    result = calculate_job_duration_seconds(job)
    expected = EXPECTED[job_number]
    match = result == expected
    all_match = all_match and match
    print(json.dumps({
        'job_number': job_number,
        'computed': result,
        'expected_from_prior_round': expected,
        'match': match,
    }, default=str))
print("ALL MATCH:" , all_match)

# ── Step 3b: calculate_job_duration_by_user() per-person breakdown ──────────
print("\n=== Step 3b: calculate_job_duration_by_user() for job 2 specifically ===")
print("Expected: exactly ONE user key, with a merged total of 18000 seconds "
      "(the two raw entries -- 14400s and 18000s, same technician, overlapping "
      "04:00 starts -- merged into their union, not summed).")
job2 = JobberJob.objects.filter(tenant_id=tenant_id, is_active=True, job_number=2).first()
if job2 is None:
    print("job_number=2: NOT FOUND locally")
else:
    by_user = calculate_job_duration_by_user(job2)
    print(json.dumps({'per_user_breakdown': by_user, 'key_count': len(by_user)}, indent=2, default=str))
    if len(by_user) == 1:
        only_value = next(iter(by_user.values()))
        print(f"Single user's merged total: {only_value} (expected 18000)")
    else:
        print(f"UNEXPECTED: {len(by_user)} distinct user keys, expected exactly 1 -- investigate.")

# ── Step 3d: sync the new assigned_users M2M for real, report multi-assignee ─
print("\n=== Step 3d: syncing assigned_users M2M for real, then checking for any real multi-assignee visit ===")
run = sync_tenant(account)
print(json.dumps({'sync_status': run.status, 'error_message': run.error_message}, indent=2, default=str))

visits = JobberVisit.objects.filter(tenant_id=tenant_id, is_active=True).prefetch_related('assigned_users').select_related('job')
print(f"\nTotal active JobberVisit rows: {visits.count()}")

any_multi = False
for visit in visits:
    assignees = list(visit.assigned_users.all())
    if len(assignees) > 1:
        any_multi = True
    print(json.dumps({
        'job_number': visit.job.job_number if visit.job else None,
        'visit_jobber_id': visit.jobber_id,
        'assigned_users_count': len(assignees),
        'assigned_users_names': [u.name for u in assignees],
        'assigned_user_single_fk_unchanged': visit.assigned_user.name if visit.assigned_user else 'Unassigned',
    }, default=str))

print("\n--- Step 3d summary ---")
if any_multi:
    print("At least one REAL visit with 2+ assigned_users found locally after sync.")
else:
    print("No visit with more than 1 assigned_users entry found in the current "
          "real synced data -- informational only, not a blocker; the schema "
          "change is worth having regardless per the original instruction.")

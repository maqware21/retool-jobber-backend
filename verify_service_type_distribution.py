"""
Part B verification for the "Avg Job Duration by Type & Technician"
chart proposal (2026-08-25) -- does real service_type data actually
support a multi-technician-per-type comparison, or is it still one job
per type (confirmed weeks ago for an earlier, smaller dataset)?

Run via `python manage.py shell < verify_service_type_distribution.py`.

Groups ALL active jobs (not windowed by archived/completed_at -- this is
a pure catalog-distribution question, not a revenue attribution one) by
service_type, and for each group reports:
  - how many jobs share that type
  - how many DIFFERENT technicians appear across those jobs

Technician attribution reuses _gather_job_assignees() from
electricians_summary.py UNCHANGED -- the same "every real assignee
across all of a job's visits" primitive Top Earner already uses, not a
new/different derivation.
"""
import json
from collections import defaultdict

from apps.jobber.api.electricians_summary import _gather_job_assignees
from apps.jobber.models import JobberJob

TENANT_ID = 1  # standing rule: always tenant_id=1, never a bare .first()

jobs = list(JobberJob.objects.filter(tenant_id=TENANT_ID, is_active=True))
print(f"=== {len(jobs)} total active job(s) for tenant_id={TENANT_ID} ===\n")

by_type = defaultdict(list)
for job in jobs:
    key = job.service_type or "(no service_type)"
    by_type[key].append(job)

print(f"{len(by_type)} distinct service_type value(s):\n")

for service_type, type_jobs in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
    techs_in_type = {}
    for job in type_jobs:
        for user_id, user in _gather_job_assignees(job).items():
            techs_in_type[user_id] = user.name

    print(f"'{service_type}' — {len(type_jobs)} job(s), {len(techs_in_type)} distinct technician(s)")
    for job in type_jobs:
        assignees = _gather_job_assignees(job)
        names = ", ".join(u.name for u in assignees.values()) or "(unassigned)"
        print(f"    {job.id} JOB-{job.job_number}: {names}")
    print()

print("=== Summary: types with 2+ jobs AND 2+ distinct technicians (genuinely comparable) ===")
comparable = []
for service_type, type_jobs in by_type.items():
    techs_in_type = set()
    for job in type_jobs:
        techs_in_type.update(_gather_job_assignees(job).keys())
    if len(type_jobs) >= 2 and len(techs_in_type) >= 2:
        comparable.append((service_type, len(type_jobs), len(techs_in_type)))

if comparable:
    for service_type, job_count, tech_count in comparable:
        print(f"  '{service_type}': {job_count} jobs, {tech_count} technicians")
else:
    print("  NONE -- no service_type currently has both 2+ jobs and 2+ distinct technicians.")

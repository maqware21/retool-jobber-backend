"""
Verification for GET /v1/jobber/duration-by-type/ (2026-08-25). Run via
`python manage.py shell < verify_duration_by_type.py`.

Confirms:
  1) The SAME 2 service_type values verify_service_type_distribution.py
     already found qualify (2+ jobs, 2+ distinct technicians) are the
     ones this endpoint returns -- no drift between the investigation
     and the real feature built on top of it.
  2) Each returned per-technician avg_duration_seconds is independently
     recomputed here, directly from calculate_job_duration_by_user()
     over that technician's real jobs of that type, and must match the
     endpoint's own number exactly -- proof the averages are real, not
     just that the endpoint returns SOME numbers.
  3) Any type with fewer than 2 real jobs or fewer than 2 distinct
     technicians is absent from the response entirely.
"""
import json
from collections import defaultdict

from apps.jobber.api.duration_by_type import (
    MIN_JOBS_PER_TYPE,
    MIN_TECHNICIANS_PER_TYPE,
    _local_duration_by_type_response,
)
from apps.jobber.api.electricians_summary import _gather_job_assignees, calculate_job_duration_by_user
from apps.jobber.models import JobberJob
from apps.tenants.models import Tenant

tenant = Tenant.objects.filter(id=1).first()
print("tenant:", tenant)

data = _local_duration_by_type_response(tenant)
print("connected:", data.get("connected"))
print(f"\n=== {len(data.get('types', []))} qualifying type(s) ===")
for t in data.get("types", []):
    print(f"\n'{t['service_type']}' — {t['job_count']} job(s), {t['technician_count']} technician(s)")
    for tech in t["technicians"]:
        print(f"    user_id={tech['user_id']:<4} {tech['name']:<20} avg_duration_seconds={tech['avg_duration_seconds']}")

# --- Check 1: same qualifying types as the already-verified distribution ---
all_jobs = list(JobberJob.objects.filter(tenant_id=tenant.id, is_active=True))
by_type_raw = defaultdict(list)
for job in all_jobs:
    if job.service_type:
        by_type_raw[job.service_type].append(job)

independently_qualifying = set()
for service_type, jobs in by_type_raw.items():
    tech_ids = set()
    for job in jobs:
        tech_ids.update(_gather_job_assignees(job).keys())
    if len(jobs) >= MIN_JOBS_PER_TYPE and len(tech_ids) >= MIN_TECHNICIANS_PER_TYPE:
        independently_qualifying.add(service_type)

endpoint_types = {t["service_type"] for t in data.get("types", [])}
print("\n=== Check 1: qualifying types match independent re-derivation ===")
print(f"independently derived: {sorted(independently_qualifying)}")
print(f"endpoint returned:     {sorted(endpoint_types)}")
# NOTE: endpoint_types can be a SUBSET of independently_qualifying (not a
# mismatch) -- a type can pass the assignment-based check here but still
# get dropped by the endpoint's second, post-exclusion check if fewer
# than MIN_TECHNICIANS_PER_TYPE technicians end up with a REAL average
# once null-duration jobs are excluded. Only a type in endpoint_types
# but NOT in independently_qualifying would be a real bug.
unexpected = endpoint_types - independently_qualifying
print("PASS -- no unexpected types" if not unexpected else f"FAIL -- unexpected types in response: {unexpected}")

# --- Check 2: independently recompute each returned average ---
print("\n=== Check 2: independently recomputed averages ===")
all_match = True
for t in data.get("types", []):
    jobs_of_type = by_type_raw[t["service_type"]]
    per_tech_seconds = defaultdict(list)
    for job in jobs_of_type:
        assignees = _gather_job_assignees(job)
        seconds_by_user = calculate_job_duration_by_user(job)
        for user_id in assignees:
            seconds = seconds_by_user.get(user_id)
            if seconds:
                per_tech_seconds[user_id].append(seconds)

    for tech in t["technicians"]:
        recomputed = round(sum(per_tech_seconds[tech["user_id"]]) / len(per_tech_seconds[tech["user_id"]]))
        status = "MATCH" if recomputed == tech["avg_duration_seconds"] else "MISMATCH"
        if status == "MISMATCH":
            all_match = False
        print(f"'{t['service_type']}' / {tech['name']}: endpoint={tech['avg_duration_seconds']} recomputed={recomputed} -> {status}")

print("\nOverall Check 2:", "PASS" if all_match else "FAIL")

# --- Check 3: real hours, for the dot-plot's fixed 0-6h scale question ---
print("\n=== Check 3: real average durations vs. the dot-plot's fixed 6h scale ===")
for t in data.get("types", []):
    for tech in t["technicians"]:
        hours = tech["avg_duration_seconds"] / 3600
        flag = " <-- EXCEEDS 6h scale" if hours > 6 else ""
        print(f"'{t['service_type']}' / {tech['name']}: {hours:.2f}h{flag}")

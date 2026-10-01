"""
Verification only, no fix -- read-only. Confirms the REAL, raw
service_type value stored for job_number 6 (F1), 7 (F2), and 8 (F3),
tenant_id=6, and explicitly states whether F3's non-match against
F1/F2 in research_alternative_callback_detection.py's Signal 1/2 output
was because the values were read and genuinely differ, or because F3's
own service_type came back null/empty.

Run via:
    python manage.py shell < verify_f3_service_type.py
on the SERVER. Delete afterward, same as every other one-off
verification in this project.
"""
from apps.jobber.models import JobberJob

TENANT_ID = 6
JOB_NUMBERS = {6: 'F1', 7: 'F2', 8: 'F3'}

jobs = {
    j.job_number: j
    for j in JobberJob.objects.filter(tenant_id=TENANT_ID, job_number__in=JOB_NUMBERS.keys())
}

print("Real, raw service_type per job:\n")
for job_number, label in JOB_NUMBERS.items():
    job = jobs.get(job_number)
    if job is None:
        print(f"  job_number={job_number} ({label}): NOT FOUND")
        continue
    raw = job.service_type
    shown = repr(raw) if raw is not None else 'None'
    print(f"  job_number={job_number} ({label}): service_type = {shown}")

print()

f1 = jobs.get(6)
f2 = jobs.get(7)
f3 = jobs.get(8)

if f3 is None or f1 is None or f2 is None:
    print("Cannot compare -- one or more of F1/F2/F3 not found for this tenant.")
else:
    if not f3.service_type:
        print(
            "REAL ANSWER: F3's own service_type came back null/empty "
            f"({f3.service_type!r}). The safeguard could not be confirmed "
            "from F3's own side at all -- per _find_prior_match()'s own "
            "'no data != assumed' rule, this alone is why F3 didn't match "
            "F1/F2, regardless of what F1/F2's own service_type values are."
        )
    elif f3.service_type == f1.service_type or f3.service_type == f2.service_type:
        print(
            f"REAL ANSWER: F3's service_type ({f3.service_type!r}) is NOT "
            "null -- and it MATCHES at least one of F1/F2. If the research "
            "script still didn't flag this pair, the non-match was NOT "
            "caused by the service_type safeguard -- look at the date/"
            "window or property/client key logic instead."
        )
    else:
        print(
            f"REAL ANSWER: F3's service_type ({f3.service_type!r}) is real "
            f"and non-null, and genuinely DIFFERS from F1's "
            f"({f1.service_type!r}) and F2's ({f2.service_type!r}). The "
            "safeguard correctly read real, differing values -- this was "
            "NOT a null/missing-data case."
        )

"""
Verification only, no design decision made here -- read-only. Reports
the real total for job_number=5 (tenant_id=6, the "Follow-up visit --
no charge" research test job), from TWO sources, not just one:

1. The LOCAL, already-synced JobberJob.total -- but this is NOT
   sufficient on its own: sync.py's own real job mapping coerces total
   via `_to_decimal(node.get('total')) or Decimal('0')`, so a genuine
   Jobber-side null and a genuine Jobber-side 0 are INDISTINGUISHABLE
   once stored locally -- both become Decimal('0') either way. The
   model field itself has no null=True either, confirming this
   coercion is deliberate/required, not an accident.
2. A fresh, live, read-only single-job query straight to Jobber (cheap
   -- same `job(id: $id)` shape _CALLBACK_DETECTION_QUERY already uses
   for a single job), which reports Jobber's OWN raw value, before any
   local coercion -- this is the only way to tell a genuine null from a
   genuine 0, which is exactly what decides which dollar-calculation
   option (Section 3 of cross_job_callback_detection_replacement_plan.md)
   is even viable.

Run via:
    python manage.py shell < verify_job5_total.py
on the SERVER. Delete afterward, same as every other one-off
verification in this project.
"""
from apps.jobber.models import JobberAccount, JobberJob
from apps.jobber.services import client

TENANT_ID = 6
JOB_NUMBER = 5

_RAW_TOTAL_QUERY = """
query ResearchJobTotal($id: EncodedId!) {
  job(id: $id) {
    id
    total
  }
}
"""

job = JobberJob.objects.filter(tenant_id=TENANT_ID, job_number=JOB_NUMBER).first()

if job is None:
    print(f"No JobberJob found for tenant_id={TENANT_ID}, job_number={JOB_NUMBER}.")
else:
    print(f"job.jobber_id = {job.jobber_id}")
    print(f"job.title = {job.title!r}")
    print(f"job.job_status = {job.job_status}")
    print(f"LOCAL job.total (post-coercion) = {job.total!r}")

    account = JobberAccount.objects.filter(tenant_id=TENANT_ID, is_active=True).first()
    if account is None:
        print(f"\nNo active JobberAccount for tenant_id={TENANT_ID} -- cannot make the live call.")
    else:
        data = client.execute(account, _RAW_TOTAL_QUERY, {'id': job.jobber_id})
        raw_total = (data or {}).get('job', {}).get('total')
        print(f"\nLIVE, RAW Jobber job.total (before any local coercion) = {raw_total!r}")

        if raw_total is None:
            print(
                "REAL ANSWER: Jobber's own raw total is NULL -- this job was "
                "never priced/invoiced at all. Locally this gets coerced to "
                "Decimal('0') by sync.py's own mapping, which looks identical "
                "to a real $0 once stored -- the live call above is what "
                "actually distinguishes the two."
            )
        elif float(raw_total) == 0:
            print(
                "REAL ANSWER: Jobber's own raw total is a real, explicit 0 -- "
                "genuinely priced at zero, not null/missing."
            )
        else:
            print(
                f"REAL ANSWER: Jobber's own raw total is real and non-zero: "
                f"{raw_total!r} -- a real line-item price exists on this job "
                "even though it was never invoiced."
            )

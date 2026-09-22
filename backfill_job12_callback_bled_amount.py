"""
URGENT, narrow, one-off correction — clears JUST job_number=12's
(tenant_id=5) real callback_bled_amount / callback_bled_amount_is_
estimated back to null. Confirmed via verify_job12_callback_bled.py's
real output: the scheduled-time fallback (feature/callback-scheduled-
time-fallback, merged to main) produced a genuinely wrong $5,000
estimate from a real but oversized ~25-hour scheduled callback-visit
window ($600 job.total / 3.0 real logged original hours = $200/hr,
x 25 scheduled hours = $5,000) -- not a real logged-hours result, and
not a data-entry mistake on the original-hours side. See
callback_scheduled_time_ceiling_hotfix_proposal.md for the real,
separate code fix that prevents this going forward; this script only
corrects the one already-wrong row.

Does NOT touch is_callback or first_archived_at, and does NOT touch
any other job -- this job's callback detection itself is still real
and correct (it genuinely was a callback); only its dollar ESTIMATE
was wrong. Same narrow, one-off, single-job correction pattern this
project has used before for exactly this kind of formula-produced-a-
wrong-number situation.

Run via:
    python manage.py shell < backfill_job12_callback_bled_amount.py
on the SERVER, against the real database, only after this has been
reviewed and approved. Delete afterward, same as every other one-off
script in this project.
"""
from apps.jobber.models import JobberJob

TENANT_ID = 5
JOB_NUMBER = 12

job = JobberJob.objects.filter(tenant_id=TENANT_ID, job_number=JOB_NUMBER).first()
if job is None:
    print(f"No JobberJob found for tenant_id={TENANT_ID}, job_number={JOB_NUMBER} -- nothing to fix.")
else:
    print(
        f"BEFORE: callback_bled_amount={job.callback_bled_amount}, "
        f"callback_bled_amount_is_estimated={job.callback_bled_amount_is_estimated}, "
        f"is_callback rows on this job: "
        f"{list(job.visits.filter(is_callback=True, is_active=True).values_list('jobber_id', flat=True))}"
    )

    job.callback_bled_amount = None
    job.callback_bled_amount_is_estimated = False
    job.save(update_fields=['callback_bled_amount', 'callback_bled_amount_is_estimated'])
    job.refresh_from_db()

    print(
        f"AFTER:  callback_bled_amount={job.callback_bled_amount}, "
        f"callback_bled_amount_is_estimated={job.callback_bled_amount_is_estimated}"
    )
    print(
        "is_callback / first_archived_at intentionally untouched -- this job's real "
        "callback detection remains correct; only the wrong dollar estimate was cleared "
        "back to the honest 'unknown' state it should have stayed in."
    )

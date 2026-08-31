"""
Read-only re-check (2026-08-30) -- does Job #3 (tenant_id=4) still qualify
as a real callback under the NEW CALLBACK_WINDOW_DAYS rule? Run via
`python manage.py shell < verify_callback_window_reeval.py`.

Why this script exists rather than just re-syncing: per the freeze-once
property (documented in detect_and_freeze_callbacks()'s own docstring),
that function only ever runs for a job whose first_archived_at was JUST
set this pass. Job #3's first_archived_at is already frozen from an
earlier sync, so it will NEVER be reprocessed through the normal sync
pipeline -- the new window logic literally cannot "re-run" against it.
This script applies the SAME window arithmetic detect_and_freeze_
callbacks() now uses, read-only, directly against Job #3's real live
visit timing, to answer the specific question asked: does it still
qualify. It does NOT write is_callback/callback_bled_amount/
first_archived_at -- those stay exactly as already frozen, per the
documented "does not retroactively affect anything already detected"
property. If this script's answer disagrees with what's already stored,
that's expected and fine (the old frozen value predates PART A and was
correct under the pre-window rule) -- not something this script corrects.

Reuses CALLBACK_WINDOW_DAYS directly from sync.py (not a re-typed
literal) and the exact same "last completed visit before the reopen"
logic detect_and_freeze_callbacks() uses.
"""
from datetime import timedelta

from apps.jobber.models import JobberAccount, JobberJob
from apps.jobber.services.client import JobberAPIError, execute
from apps.jobber.services.sync import CALLBACK_WINDOW_DAYS, _to_datetime

TENANT_ID = 4
TARGET_JOB_NUMBER = 3

_VISIT_TIMING_QUERY = """
query GetJobVisitsForWindowReeval($id: EncodedId!) {
  job(id: $id) {
    id
    jobNumber
    visits(first: 25) {
      nodes {
        id
        createdAt
        completedAt
        invoice { id }
      }
    }
  }
}
"""

print("CALLBACK_WINDOW_DAYS:", CALLBACK_WINDOW_DAYS)

account = JobberAccount.objects.filter(tenant_id=TENANT_ID, is_active=True).first()
print("account:", account)

local_job = JobberJob.objects.filter(tenant_id=TENANT_ID, job_number=TARGET_JOB_NUMBER, is_active=True).first()
print(f"local JobberJob: {local_job}  is_callback-affected visits already frozen, untouched by this script.")

if account is None or local_job is None:
    print("Cannot proceed -- missing account or local job row.")
else:
    try:
        data = execute(account, _VISIT_TIMING_QUERY, {'id': local_job.jobber_id})
    except JobberAPIError as exc:
        print(f"Jobber API error: {exc}")
        data = None

    job = (data or {}).get('job') if data else None
    if job is None:
        print("Live job(id=...) returned null.")
    else:
        visits_raw = (job.get('visits') or {}).get('nodes') or []
        print(f"\n{len(visits_raw)} real visit(s) pulled live for job #{job.get('jobNumber')}.")
        for v in sorted(visits_raw, key=lambda v: v.get('createdAt') or ''):
            print(f"  id={v.get('id')} createdAt={v.get('createdAt')} completedAt={v.get('completedAt')} invoice={v.get('invoice')}")

        if not visits_raw:
            print("No visits -- cannot evaluate.")
        else:
            latest_visit_raw = max(visits_raw, key=lambda v: v.get('createdAt') or '')
            latest_visit_id = latest_visit_raw.get('id')
            has_invoice = latest_visit_raw.get('invoice') is not None

            print(f"\ncreatedAt-latest visit: id={latest_visit_id}, invoice present: {has_invoice}")

            if has_invoice:
                print("RESULT: NOT a callback -- the createdAt-latest visit has a real invoice.")
            else:
                reopen_at = _to_datetime(latest_visit_raw.get('createdAt'))
                completed_before_reopen = [
                    v for v in visits_raw
                    if v.get('id') != latest_visit_id
                    and v.get('completedAt')
                    and reopen_at is not None
                    and _to_datetime(v['completedAt']) < reopen_at
                ]
                if not completed_before_reopen:
                    print(
                        "RESULT: would NOT be flagged -- no other visit has a real completedAt "
                        "before the reopen, so the window can't be confirmed."
                    )
                else:
                    last_completed_visit = max(completed_before_reopen, key=lambda v: v['completedAt'])
                    last_completed_at = _to_datetime(last_completed_visit['completedAt'])
                    gap = reopen_at - last_completed_at
                    print(f"Gap between last completed visit and the reopen: {gap}")
                    if gap > timedelta(days=CALLBACK_WINDOW_DAYS):
                        print(
                            f"RESULT: would NOT be flagged -- {gap} exceeds the "
                            f"{CALLBACK_WINDOW_DAYS}-day window."
                        )
                    else:
                        print(
                            f"RESULT: STILL QUALIFIES as a real callback under the new rule -- "
                            f"{gap} is within the {CALLBACK_WINDOW_DAYS}-day window."
                        )

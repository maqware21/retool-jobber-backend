"""
Step 1 verification for "Top Earner": does the connected test account
currently have any REAL Visit with more than one assignedUsers entry?

Run via `python manage.py shell < verify_multi_assignee_visits.py`.

Live GraphQL call (visits.assignedUsers isn't fully captured locally today
— JobberVisit.assigned_user only ever stores the FIRST assignee, which is
exactly the gap this script exists to check the real-world impact of), so
this asks Jobber directly, per real archived job, for every visit and its
full assignedUsers list (first: 5, matching what _SYNC_JOBS_QUERY already
requests) -- not just the first assignee.

Reports, per job: every visit's real assignedUsers count and names. Flags
any visit with 2+ assignees plainly, since that's the exact case that
would currently go silently wrong (only the first person stored/credited).
"""
import json

from apps.jobber.models import JobberAccount, JobberJob
from apps.jobber.services import client

# Standing instruction: always filter test-account lookups by tenant_id=1
# explicitly -- there are 2 real JobberAccount rows in this database
# (tenant_id=1 is the real, data-rich test account; tenant_id=3 belongs to
# a teammate and is nearly empty). Never .first() with no tenant filter.
account = JobberAccount.objects.filter(is_active=True, tenant_id=1).first()
tenant_id = account.tenant_id
print("tenant_id:", tenant_id)

jobs = JobberJob.objects.filter(tenant_id=tenant_id, is_active=True).order_by('job_number')
print(f"Checking all {jobs.count()} locally-known jobs' visits live for multi-assignee cases:")

_VISITS_CHECK_QUERY = """
query GetJobVisitsCheck($id: EncodedId!) {
  job(id: $id) {
    id
    jobNumber
    visits(first: 10) {
      nodes {
        id
        assignedUsers(first: 5) {
          nodes {
            id
            name { full }
          }
        }
      }
    }
  }
}
"""

any_multi_assignee_found = False
for job in jobs:
    data = client.execute(account, _VISITS_CHECK_QUERY, {'id': job.jobber_id})
    live_job = (data or {}).get('job') or {}
    visits = (live_job.get('visits') or {}).get('nodes') or []

    visit_summaries = []
    for visit in visits:
        assigned = (visit.get('assignedUsers') or {}).get('nodes') or []
        names = [(u.get('name') or {}).get('full') for u in assigned]
        if len(assigned) > 1:
            any_multi_assignee_found = True
        visit_summaries.append({
            'visit_id': visit.get('id'),
            'assignee_count': len(assigned),
            'assignee_names': names,
        })

    print(json.dumps({
        'job_number': job.job_number,
        'visit_count': len(visits),
        'visits': visit_summaries,
    }, indent=2, default=str))

print("\n--- Summary ---")
if any_multi_assignee_found:
    print("At least one REAL visit with 2+ assignedUsers was found -- the "
          "single-assignee-FK gap is live-confirmed as a real, current "
          "problem, not just a theoretical one.")
else:
    print("No visit with more than 1 assignee found in the current real "
          "test data -- the gap is real per the schema (assignedUsers is a "
          "list/connection, confirmed) but not yet demonstrated with real "
          "multi-assignee data in this account.")

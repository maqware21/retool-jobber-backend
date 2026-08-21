"""
Live verification for the Callback R&D task: does Job.customFields
actually return real values for the 3 real Job custom fields (Callback
Reason, Callback Count, Callback) set up on a real job, using the same
CustomFieldUnion inline-fragment pattern already proven for
User.customFields?

Run via `python manage.py shell < verify_job_custom_fields_live.py`.
"""
import json

from apps.jobber.models import JobberAccount
from apps.jobber.services import client

account = JobberAccount.objects.filter(is_active=True, tenant_id=1).first()
print("tenant_id:", account.tenant_id if account else None)

query = """
query VerifyJobCustomFields($first: Int!, $after: String) {
  jobs(first: $first, after: $after) {
    nodes {
      id
      jobNumber
      title
      jobStatus
      customFields {
        ... on CustomFieldText { label valueText }
        ... on CustomFieldNumeric { label valueNumeric }
        ... on CustomFieldTrueFalse { label valueTrueFalse }
        ... on CustomFieldDropdown { label valueDropdown }
        ... on CustomFieldLink { label valueLink { text url } }
        ... on CustomFieldArea { label valueArea { length width } }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

data = client.execute(account, query, {'first': 25, 'after': None})
jobs = ((data or {}).get('jobs') or {}).get('nodes') or []
print(f"\n=== {len(jobs)} real jobs pulled ===")
for job in jobs:
    cfs = job.get('customFields') or []
    has_callback = any('callback' in (cf.get('label') or '').lower() for cf in cfs)
    marker = " <-- HAS CALLBACK FIELDS" if has_callback else ""
    print(f"JOB-{job.get('jobNumber')} ({job.get('jobStatus')}) title={job.get('title')!r}{marker}")
    if has_callback:
        print(json.dumps(cfs, indent=2))

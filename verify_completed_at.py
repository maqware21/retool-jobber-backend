"""
Step 1 verification for the "Jobs Completed" cutover: does Job.completedAt
exist and reliably reflect a usable "job finished" date, or does it track
something else (e.g. when invoicing cleared, which the user's own research
says may be a different date than when the work was actually done)?

Run via `python manage.py shell < verify_completed_at.py`.

Picks real archived JobberJob rows already synced locally for tenant_id=1
(no guessing at which jobs are archived -- reads the real local job_status
field), then makes LIVE GraphQL calls (completedAt is not synced yet) for:
  - 2-3 archived jobs that DO have a locally-synced linked invoice
  - 1 archived job that does NOT have a linked invoice, if one exists

For the ones with a linked invoice, cross-checks the live completedAt
against that job's invoice's issued_date -- already synced locally, no
new query needed for that half of the comparison.
"""
import json

from apps.jobber.models import JobberAccount, JobberJob
from apps.jobber.services import client

account = JobberAccount.objects.filter(is_active=True).first()
tenant_id = account.tenant_id
print("tenant_id:", tenant_id)

archived_jobs = JobberJob.objects.filter(tenant_id=tenant_id, is_active=True, job_status='archived')
print(f"\nTotal locally-synced archived jobs for this tenant: {archived_jobs.count()}")

with_invoice = [j for j in archived_jobs if j.invoices.filter(is_active=True).exists()]
without_invoice = [j for j in archived_jobs if not j.invoices.filter(is_active=True).exists()]
print(f"  - with at least one locally-synced linked invoice: {len(with_invoice)}")
print(f"  - with NO locally-synced linked invoice: {len(without_invoice)}")

_COMPLETED_AT_QUERY = """
query GetJobCompletedAt($id: EncodedId!) {
  job(id: $id) {
    id
    jobNumber
    jobStatus
    completedAt
    createdAt
  }
}
"""


def fetch_completed_at(jobber_id):
    data = client.execute(account, _COMPLETED_AT_QUERY, {'id': jobber_id})
    return (data or {}).get('job') or {}


print("\n=== Archived jobs WITH a linked invoice (up to 3) — live completedAt vs. local invoice issued_date ===")
for job in with_invoice[:3]:
    live = fetch_completed_at(job.jobber_id)
    invoices = list(job.invoices.filter(is_active=True).order_by('issued_date'))
    print(json.dumps({
        'job_number': job.job_number,
        'jobber_id': job.jobber_id,
        'live_jobStatus': live.get('jobStatus'),
        'live_completedAt': live.get('completedAt'),
        'live_createdAt': live.get('createdAt'),
        'local_start_at (job.start_at)': str(job.start_at),
        'linked_invoices': [
            {'invoice_number': inv.invoice_number, 'issued_date': str(inv.issued_date), 'status_display': inv.status_display}
            for inv in invoices
        ],
    }, indent=2, default=str))

print("\n=== Archived job(s) with NO linked invoice (up to 1) — live completedAt ===")
if not without_invoice:
    print("None found in this tenant's currently-synced archived jobs — nothing to check for this case yet.")
else:
    job = without_invoice[0]
    live = fetch_completed_at(job.jobber_id)
    print(json.dumps({
        'job_number': job.job_number,
        'jobber_id': job.jobber_id,
        'live_jobStatus': live.get('jobStatus'),
        'live_completedAt': live.get('completedAt'),
        'live_createdAt': live.get('createdAt'),
        'local_start_at (job.start_at)': str(job.start_at),
    }, indent=2, default=str))

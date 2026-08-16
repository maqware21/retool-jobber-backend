"""
Verification script for Step 2 (the sync engine). Run on the server on
branch feature/jobber-local-sync (migrations from Step 2's makemigrations
already applied), via `python manage.py shell < verify_sync_tenant.py`.

Calls the real sync_tenant() against the real connected test account and
prints the real JobberSyncRun result plus row counts in each of the 5 new
tables afterward -- not a reconstruction.
"""
import json

from apps.jobber.models import (
    JobberAccount,
    JobberClient,
    JobberInvoice,
    JobberJob,
    JobberSyncRun,
    JobberUser,
    JobberVisit,
)
from apps.jobber.services.sync import sync_tenant

account = JobberAccount.objects.filter(is_active=True, tenant_id=1).first()
print("tenant_id:", account.tenant_id)

run = sync_tenant(account)

print("\n--- JobberSyncRun result ---")
print(json.dumps({
    'id': run.id,
    'status': run.status,
    'started_at': str(run.started_at),
    'finished_at': str(run.finished_at),
    'claimed_at': str(run.claimed_at),
    'duration_seconds': run.duration_seconds,
    'is_stuck': run.is_stuck,
    'error_message': run.error_message,
    'clients_synced': run.clients_synced,
    'users_synced': run.users_synced,
    'jobs_synced': run.jobs_synced,
    'visits_synced': run.visits_synced,
    'invoices_synced': run.invoices_synced,
}, indent=2, default=str))

print("\n--- Row counts in local tables (tenant scoped) ---")
tenant_id = account.tenant_id
print(json.dumps({
    'JobberClient': JobberClient.objects.filter(tenant_id=tenant_id).count(),
    'JobberClient (active)': JobberClient.objects.filter(tenant_id=tenant_id, is_active=True).count(),
    'JobberUser': JobberUser.objects.filter(tenant_id=tenant_id).count(),
    'JobberUser (active)': JobberUser.objects.filter(tenant_id=tenant_id, is_active=True).count(),
    'JobberJob': JobberJob.objects.filter(tenant_id=tenant_id).count(),
    'JobberJob (active)': JobberJob.objects.filter(tenant_id=tenant_id, is_active=True).count(),
    'JobberVisit': JobberVisit.objects.filter(tenant_id=tenant_id).count(),
    'JobberVisit (active)': JobberVisit.objects.filter(tenant_id=tenant_id, is_active=True).count(),
    'JobberInvoice': JobberInvoice.objects.filter(tenant_id=tenant_id).count(),
    'JobberInvoice (active)': JobberInvoice.objects.filter(tenant_id=tenant_id, is_active=True).count(),
    'JobberSyncRun (all attempts)': JobberSyncRun.objects.filter(tenant_id=tenant_id).count(),
}, indent=2, default=str))

print("\n--- Sample rows, so field values can be eyeballed against real Jobber data ---")
print("Clients:")
for c in JobberClient.objects.filter(tenant_id=tenant_id):
    print(json.dumps({'jobber_id': c.jobber_id, 'name': c.name, 'tags_display': c.tags_display, 'is_active': c.is_active}, default=str))

print("Users:")
for u in JobberUser.objects.filter(tenant_id=tenant_id):
    print(json.dumps({'jobber_id': u.jobber_id, 'name': u.name, 'is_account_admin': u.is_account_admin, 'is_account_owner': u.is_account_owner}, default=str))

print("Jobs:")
for j in JobberJob.objects.filter(tenant_id=tenant_id):
    print(json.dumps({
        'jobber_id': j.jobber_id, 'job_number': j.job_number, 'title': j.title,
        'client': j.client.name if j.client else None,
        'status_display': j.status_display, 'service_type': j.service_type,
        'total': str(j.total), 'labour_duration_seconds': j.labour_duration_seconds,
        'labour_duration_hours': j.labour_duration_hours, 'labour_cost': str(j.labour_cost) if j.labour_cost is not None else None,
        'jobber_created_at': str(j.jobber_created_at), 'start_at': str(j.start_at), 'address': j.address,
    }, default=str))

print("Visits:")
for v in JobberVisit.objects.filter(tenant_id=tenant_id):
    print(json.dumps({'jobber_id': v.jobber_id, 'job': v.job.title if v.job else None, 'assigned_user_name': v.assigned_user_name}, default=str))

print("Invoices:")
for inv in JobberInvoice.objects.filter(tenant_id=tenant_id):
    print(json.dumps({
        'jobber_id': inv.jobber_id, 'invoice_number': inv.invoice_number,
        'client': inv.client.name if inv.client else None,
        'amount': str(inv.amount), 'balance': str(inv.balance) if inv.balance is not None else None,
        'issued_date': str(inv.issued_date), 'due_date': str(inv.due_date),
        'status_display': inv.status_display,
        'linked_jobs': [jj.job_number for jj in inv.jobs.all()],
    }, default=str))

print("\n--- Idempotency check: run sync_tenant() again immediately, confirm no duplicate rows ---")
run2 = sync_tenant(account)
print(json.dumps({
    'second_run_status': run2.status,
    'JobberClient count after 2nd run': JobberClient.objects.filter(tenant_id=tenant_id).count(),
    'JobberJob count after 2nd run': JobberJob.objects.filter(tenant_id=tenant_id).count(),
    'JobberSyncRun total attempts now': JobberSyncRun.objects.filter(tenant_id=tenant_id).count(),
}, indent=2, default=str))

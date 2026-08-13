"""
Diagnostic: why did verify_electricians_total_revenue.py find 0 total
JobberInvoice rows for tenant_id=3? Run via
`python manage.py shell < diagnose_zero_invoices.py`.

Lists every JobberAccount row (in case .first() picked the wrong one),
and for each, the JobberSyncRun history and current entity row counts --
this will show directly whether tenant_id=3 has ever had a sync run at
all, or whether a different tenant is the one with real data.
"""
import json

from apps.jobber.models import (
    JobberAccount,
    JobberClient,
    JobberInvoice,
    JobberJob,
    JobberSyncRun,
    JobberUser,
)

print("--- Every JobberAccount row ---")
for acc in JobberAccount.objects.all().order_by('id'):
    print(json.dumps({
        'account_id': acc.id,
        'tenant_id': acc.tenant_id,
        'business_name': acc.tenant.business_name,
        'jobber_account_id': acc.jobber_account_id,
        'is_active': acc.is_active,
        'created_at': str(acc.created_at),
        'updated_at': str(acc.updated_at),
    }, default=str))

print("\n--- JobberSyncRun history + entity row counts, per tenant that has a JobberAccount ---")
for acc in JobberAccount.objects.all().order_by('id'):
    tenant_id = acc.tenant_id
    runs = JobberSyncRun.objects.filter(tenant_id=tenant_id).order_by('-started_at')
    print(f"\ntenant_id={tenant_id} ({acc.tenant.business_name}):")
    print(json.dumps({
        'total_sync_runs': runs.count(),
        'most_recent_run': (
            {
                'status': runs.first().status,
                'started_at': str(runs.first().started_at),
                'finished_at': str(runs.first().finished_at),
                'error_message': runs.first().error_message,
                'clients_synced': runs.first().clients_synced,
                'users_synced': runs.first().users_synced,
                'jobs_synced': runs.first().jobs_synced,
                'invoices_synced': runs.first().invoices_synced,
            } if runs.exists() else None
        ),
        'JobberClient rows (active)': JobberClient.objects.filter(tenant_id=tenant_id, is_active=True).count(),
        'JobberUser rows (active)': JobberUser.objects.filter(tenant_id=tenant_id, is_active=True).count(),
        'JobberJob rows (active)': JobberJob.objects.filter(tenant_id=tenant_id, is_active=True).count(),
        'JobberInvoice rows (active)': JobberInvoice.objects.filter(tenant_id=tenant_id, is_active=True).count(),
        'JobberInvoice rows (total, any is_active)': JobberInvoice.objects.filter(tenant_id=tenant_id).count(),
    }, indent=2, default=str))

"""
Real verification (2026-09-07) -- confirms the SYNC_JOBS_PAGE_SIZE fix
actually resolves the URGENT, project-wide sync failure (not just the
isolated fetch_jobs_for_sync() call diagnose_sync_jobs_query_cost.py
already checked). Run via `python manage.py shell < verify_sync_tenant_full.py`.

Triggers one real, full sync_tenant() call (entities=None -- everything,
the exact same call ensure_fresh() makes for a stale request) against
tenant_id=4 and reports the real JobberSyncRun result: status, per-entity
counts, and error_message if any. Must show status='success' or
'partial' -- NEVER 'failed' -- to confirm this account's sync is
genuinely working again, end to end.
"""
from apps.jobber.models import JobberAccount
from apps.jobber.services.sync import sync_tenant

TENANT_ID = 4

account = JobberAccount.objects.filter(tenant_id=TENANT_ID, is_active=True).first()
print("account:", account)

if account is None:
    print(f"No active JobberAccount for tenant_id={TENANT_ID} -- cannot sync.")
else:
    run = sync_tenant(account)
    print(f"\nJobberSyncRun: status={run.status} started_at={run.started_at} finished_at={run.finished_at}")
    print(
        f"clients_synced={run.clients_synced} users_synced={run.users_synced} "
        f"jobs_synced={run.jobs_synced} visits_synced={run.visits_synced} "
        f"invoices_synced={run.invoices_synced}"
    )
    print(f"error_message={run.error_message!r}")
    if run.status == 'failed':
        print("\nSTILL FAILING -- the fix did not resolve this.")
    else:
        print(f"\nCONFIRMED: real sync completed with status={run.status!r} (not 'failed').")

"""
Real verification (2026-09-15) for the manual "Sync Now" endpoint --
approved manual_sync_and_faster_staleness_proposal.md.

Run via `python manage.py shell < verify_sync_now.py`.

Calls the REAL, unmodified sync_tenant(account) directly against
tenant_id=5 (the same function JobberSyncNowView itself calls) and
prints the real JobberSyncRun result -- status, real per-entity counts
including the 2 new columns (timesheet_entries_synced/expenses_synced),
and the exact real message the frontend's buildSyncNowMessage() would
construct from this same data, so the real message can be confirmed
directly, not just the raw numbers.
"""
from apps.jobber.models import JobberAccount
from apps.jobber.services.sync import sync_tenant
from apps.tenants.models import Tenant

TENANT_ID = 5


def build_sync_now_message(run):
    parts = []
    if run.jobs_synced > 0:
        parts.append(f"{run.jobs_synced} job{'s' if run.jobs_synced != 1 else ''}")
    if run.clients_synced > 0:
        parts.append(f"{run.clients_synced} client{'s' if run.clients_synced != 1 else ''}")
    if run.users_synced > 0:
        parts.append(f"{run.users_synced} user{'s' if run.users_synced != 1 else ''}")
    if run.visits_synced > 0:
        parts.append(f"{run.visits_synced} visit{'s' if run.visits_synced != 1 else ''}")
    if run.invoices_synced > 0:
        parts.append(f"{run.invoices_synced} invoice{'s' if run.invoices_synced != 1 else ''}")
    if run.timesheet_entries_synced > 0:
        parts.append(f"{run.timesheet_entries_synced} timesheet entr{'ies' if run.timesheet_entries_synced != 1 else 'y'}")
    if run.expenses_synced > 0:
        parts.append(f"{run.expenses_synced} expense{'s' if run.expenses_synced != 1 else ''}")

    summary = f"Synced: {', '.join(parts)}" if parts else "Nothing new to sync"
    if run.status == 'success':
        return summary
    if run.status == 'partial':
        return f"{summary} (partial — some data may still be stale)"
    return f"Sync failed{f': {run.error_message}' if run.error_message else ''}"


tenant = Tenant.objects.filter(id=TENANT_ID).first()
print("tenant:", tenant)

account = JobberAccount.objects.filter(tenant_id=TENANT_ID, is_active=True).first()
print("account:", account)

if account is None:
    print(f"No active JobberAccount for tenant_id={TENANT_ID} -- cannot verify anything below.")
else:
    print("\nCalling the real, unmodified sync_tenant(account) directly...")
    run = sync_tenant(account)

    print(f"\n=== Real JobberSyncRun result ===")
    print(f"status: {run.status}")
    print(f"started_at: {run.started_at}")
    print(f"finished_at: {run.finished_at}")
    print(f"error_message: {run.error_message!r}")
    print(f"clients_synced: {run.clients_synced}")
    print(f"users_synced: {run.users_synced}")
    print(f"jobs_synced: {run.jobs_synced}")
    print(f"visits_synced: {run.visits_synced}")
    print(f"invoices_synced: {run.invoices_synced}")
    print(f"timesheet_entries_synced: {run.timesheet_entries_synced}")
    print(f"expenses_synced: {run.expenses_synced}")

    print(f"\n=== Real message the frontend would build from this ===")
    print(build_sync_now_message(run))

"""
Forced-failure test for the sync engine's wall-clock ceiling and
ensure_fresh()'s retry-once-on-PARTIAL behavior. Run via
`python manage.py shell < test_partial_sync.py`.

Part 1 temporarily drops SYNC_WALL_CLOCK_CEILING to 1 second and runs a
real sync_tenant() against the real connected account -- confirms it comes
back PARTIAL (not a crash, not SUCCESS). This is a real, timing-dependent
call against the real Jobber API, so it's very likely but not
mathematically guaranteed to trip within 1s -- see the note printed if it
doesn't.

Part 2 confirms ensure_fresh(require_complete=True) genuinely retries
sync_tenant() exactly once when the first attempt comes back PARTIAL, by
counting new JobberSyncRun rows created (2 = it retried once, 1 = it
didn't). This part deliberately uses a 0-second ceiling instead of 1 --
not what was asked for Part 1, but Part 2 needs a GUARANTEED cutoff on
every attempt (both the initial call and the retry) to reliably prove the
retry branch runs, rather than depending on network timing luck twice in a
row. A priming sync_tenant() call first guarantees the tenant's most
recent run is already PARTIAL before ensure_fresh() is even called, so
Part 2's result doesn't depend on how Part 1 happened to turn out.

Restores the real ceiling (whatever it was before this script ran, i.e.
25s) in a finally block, regardless of outcome.
"""
import json
from datetime import timedelta

from apps.jobber.models import JobberAccount, JobberSyncRun
from apps.jobber.services import sync

account = JobberAccount.objects.filter(is_active=True).first()
tenant = account.tenant
print("tenant_id:", tenant.id)

ORIGINAL_CEILING = sync.SYNC_WALL_CLOCK_CEILING
print("original SYNC_WALL_CLOCK_CEILING:", ORIGINAL_CEILING)

try:
    # ── Part 1: force a PARTIAL sync_tenant() run directly, 1s ceiling ───────
    sync.SYNC_WALL_CLOCK_CEILING = timedelta(seconds=1)
    print("\n=== Part 1: sync_tenant() with a 1s ceiling (full sync, all entities) ===")
    run = sync.sync_tenant(account)
    print(json.dumps({
        'status': run.status,
        'clients_synced': run.clients_synced,
        'users_synced': run.users_synced,
        'jobs_synced': run.jobs_synced,
        'visits_synced': run.visits_synced,
        'invoices_synced': run.invoices_synced,
        'error_message': run.error_message,
    }, indent=2, default=str))

    if run.status == 'partial':
        print("CONFIRMED: run came back PARTIAL, not a crash, not SUCCESS.")
    elif run.status == 'success':
        print("This particular run completed within 1s anyway (small account, fast "
              "network right now) -- not a failure of the mechanism, just bad luck for "
              "this specific timing. Re-run this script if you want to see Part 1 trip; "
              "Part 2 below does not depend on this outcome either way.")
    else:
        print(f"UNEXPECTED status: {run.status!r} -- inspect error_message above.")

    # ── Part 2: confirm ensure_fresh(require_complete=True) retries exactly
    # once when PARTIAL, then stops. 0s ceiling so both the initial attempt
    # and the retry are deterministically cut off -- see docstring above. ───
    sync.SYNC_WALL_CLOCK_CEILING = timedelta(seconds=0)

    # Prime the tenant's most recent JobberSyncRun to a known PARTIAL state,
    # deterministically, so the force_resync check inside ensure_fresh()
    # below doesn't depend on Part 1's outcome.
    sync.sync_tenant(account, entities=['clients'])

    print("\n=== Part 2: ensure_fresh(require_complete=True) retry check (0s ceiling) ===")
    runs_before = JobberSyncRun.objects.filter(tenant=tenant).count()
    result = sync.ensure_fresh(tenant, entities=None, require_complete=True)
    runs_after = JobberSyncRun.objects.filter(tenant=tenant).count()
    new_runs = runs_after - runs_before

    print(json.dumps({
        'sync_status': result['sync_status'],
        'sync_warning': result['sync_warning'],
        'new_JobberSyncRun_rows_created': new_runs,
    }, indent=2, default=str))

    if new_runs == 2 and result['sync_status'] == 'partial':
        print("CONFIRMED: ensure_fresh() attempted sync_tenant() twice (initial + exactly "
              "one retry), and accepted PARTIAL after the retry rather than looping again.")
    elif new_runs == 1:
        print("Only 1 new run -- the first attempt likely wasn't PARTIAL this time, or "
              "something short-circuited before the retry branch. Check sync_status above.")
    else:
        print(f"UNEXPECTED: {new_runs} new run(s), sync_status={result['sync_status']!r} -- investigate.")

finally:
    sync.SYNC_WALL_CLOCK_CEILING = ORIGINAL_CEILING
    print(f"\nSYNC_WALL_CLOCK_CEILING restored to {sync.SYNC_WALL_CLOCK_CEILING}")

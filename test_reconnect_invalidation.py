"""
Test for the reconnect-invalidation fix in oauth.py's _link_account().
Run via `python manage.py shell < test_reconnect_invalidation.py`.

1. Confirms the real connected tenant currently has active local rows
   (fails loudly if it doesn't -- run a real sync first, e.g. via
   verify_sync_tenant.py, before running this).
2. Simulates a reconnect by calling JobberCallbackView()._link_account()
   again directly, with a fake token payload (no real OAuth code exchange
   needed to exercise this). The real tokens are saved beforehand and
   restored right after, since _link_account() -> store_tokens() would
   otherwise overwrite them with the fake ones on disk, and step 4 below
   needs real working credentials to do a genuine sync.
3. Confirms all 5 entity types show 0 ACTIVE rows immediately after --
   the rows still exist (is_active=False), just deactivated, not deleted.
4. Calls sync_tenant() right after (with real tokens restored) and
   confirms active rows come back.

Does not touch git, does not affect JobberSyncRun bootstrap logic --
purely exercises _link_account()'s new is_reconnect branch.
"""
import json

from apps.jobber.api.oauth import JobberCallbackView
from apps.jobber.models import (
    JobberAccount,
    JobberClient,
    JobberInvoice,
    JobberJob,
    JobberUser,
    JobberVisit,
)
from apps.jobber.services.sync import sync_tenant

ENTITY_MODELS = {
    'JobberClient': JobberClient,
    'JobberUser': JobberUser,
    'JobberJob': JobberJob,
    'JobberVisit': JobberVisit,
    'JobberInvoice': JobberInvoice,
}


def active_counts(tenant_id):
    return {name: model.objects.filter(tenant_id=tenant_id, is_active=True).count() for name, model in ENTITY_MODELS.items()}


def total_counts(tenant_id):
    return {name: model.objects.filter(tenant_id=tenant_id).count() for name, model in ENTITY_MODELS.items()}


account = JobberAccount.objects.filter(is_active=True).first()
tenant = account.tenant
user = tenant.users.first()
print("tenant_id:", tenant.id, "user_id:", user.id)

# ── Step 1: confirm existing active local rows ───────────────────────────────
print("\n=== Step 1: active row counts BEFORE simulated reconnect ===")
before = active_counts(tenant.id)
print(json.dumps(before, indent=2))

if not any(before.values()):
    print("STOP: no active local rows exist yet for this tenant -- run a real "
          "sync first (e.g. `python manage.py shell < verify_sync_tenant.py`) "
          "before running this test, otherwise step 3 below proves nothing.")
else:
    # Preserve the REAL tokens -- _link_account() -> store_tokens() below
    # will overwrite them with fake ones; step 4 needs the real ones back
    # to actually hit Jobber's API for a genuine sync.
    real_tokens = {
        'access_token': account.access_token,
        'refresh_token': account.refresh_token,
        'token_type': account.token_type,
        'scope': account.scope,
        'expires_at': account.expires_at,
    }

    # ── Step 2: simulate a reconnect ─────────────────────────────────────────
    print("\n=== Step 2: simulating a reconnect (calling _link_account() again) ===")
    fake_token_data = {
        'access_token': 'fake-access-token-for-this-test',
        'refresh_token': 'fake-refresh-token-for-this-test',
        'token_type': 'bearer',
        'scope': account.scope,
        'expires_in': 3600,
    }
    view = JobberCallbackView()
    view._link_account(user, fake_token_data)
    print("_link_account() completed (account now holds fake tokens -- restored below).")

    # ── Step 3: confirm all 5 entity types show 0 active rows ───────────────
    print("\n=== Step 3: active row counts IMMEDIATELY AFTER reconnect ===")
    after_reconnect = active_counts(tenant.id)
    print(json.dumps(after_reconnect, indent=2))
    if all(count == 0 for count in after_reconnect.values()):
        print("CONFIRMED: all 5 entity types show 0 active rows right after reconnect.")
    else:
        print("UNEXPECTED: at least one entity type still shows active rows -- investigate.")

    print("\n(sanity check -- rows still exist, just is_active=False, not deleted:)")
    print(json.dumps(total_counts(tenant.id), indent=2))

    # Restore the real tokens before Step 4 needs to hit the real Jobber API.
    account.refresh_from_db()
    account.access_token = real_tokens['access_token']
    account.refresh_token = real_tokens['refresh_token']
    account.token_type = real_tokens['token_type']
    account.scope = real_tokens['scope']
    account.expires_at = real_tokens['expires_at']
    account.save(update_fields=['access_token', 'refresh_token', 'token_type', 'scope', 'expires_at', 'updated_at'])
    print("\nReal tokens restored onto the account.")

    # ── Step 4: sync_tenant() right after brings them all back ──────────────
    print("\n=== Step 4: sync_tenant() immediately after reconnect ===")
    run = sync_tenant(account)
    print(json.dumps({'status': run.status, 'error_message': run.error_message}, indent=2, default=str))

    after_sync = active_counts(tenant.id)
    print("\n=== Active row counts AFTER sync_tenant() ===")
    print(json.dumps(after_sync, indent=2))
    if all(count > 0 for count in after_sync.values()):
        print("CONFIRMED: sync_tenant() right after the reconnect brought all 5 entity types back.")
    else:
        print("At least one entity type is still 0 -- check run.status above. A PARTIAL "
              "run can legitimately leave some entities at 0 if it didn't finish, or if "
              "this test account genuinely has 0 real records of that type.")

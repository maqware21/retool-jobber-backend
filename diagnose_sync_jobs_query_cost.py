"""
URGENT real diagnostic (2026-09-07) -- project-wide sync failure.
Verification only, no production code changed by this script.

Confirms whether reducing _SYNC_JOBS_QUERY's page size (the `first`
value fetch_jobs_for_sync()/fetch_all_pages_bounded() use) brings a
real request back under Jobber's 10000 query-cost ceiling -- the
current fixed size (25, FETCH_ALL_PAGE_SIZE's default) is now
rejecting outright (requestedQueryCost=10405, actualQueryCost=0),
which means ALL entities this account syncs have been stale since
2026-09-03, not just Revenue Composition.

Run via `python manage.py shell < diagnose_sync_jobs_query_cost.py`.

Calls the EXACT SAME real fetch_jobs_for_sync() (client.py, unchanged)
directly, once at the CURRENT page size and at several smaller
candidate sizes. The real requestedQueryCost for each is visible in the
throttle-status line _log_throttle_status() already logs on EVERY
response -- even a rejected/THROTTLED one. Safe to test multiple sizes
with zero real cost impact: a query rejected for exceeding the cost
ceiling costs 0 against the real bucket (confirmed by the account's own
already-seen actualQueryCost=0 on the failing request) -- it's refused
before execution, not charged and then refunded.

Does NOT change _SYNC_JOBS_QUERY, FETCH_ALL_PAGE_SIZE, or any other
production code.
"""
from apps.jobber.models import JobberAccount
from apps.jobber.services import client
from apps.jobber.services.client import JobberAPIError

TENANT_ID = 4
CANDIDATE_SIZES = [25, 15, 10, 5]

account = JobberAccount.objects.filter(tenant_id=TENANT_ID, is_active=True).first()
print("account:", account)

if account is None:
    print(f"No active JobberAccount for tenant_id={TENANT_ID} -- cannot query live.")
else:
    for first in CANDIDATE_SIZES:
        print(f"\n--- first={first} (real requestedQueryCost is in the throttle-status log line above each result) ---")
        try:
            page = client.fetch_jobs_for_sync(account, first=first, after=None)
            print(f"  SUCCESS at first={first} -- {len(page.get('nodes') or [])} real job node(s) returned.")
        except JobberAPIError as exc:
            print(f"  FAILED at first={first}: {exc}")

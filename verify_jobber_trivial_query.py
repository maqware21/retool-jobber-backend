"""
Diagnostic (2026-08-28): does the SIMPLEST possible real Jobber query
succeed against tenant_id=4 right now, or does it hit the same THROTTLED
error the callback-bleed verification did?

Incident this tests: verify_callback_bleed.py's job-lookup query (a
single Job node with nested visits(25) and timeSheetEntries(100)) failed
twice in a row with Jobber's GraphQL query-cost THROTTLED error --
*while* the SAME response's own throttleStatus reported a FULL bucket
(currentlyAvailable == maximumAvailable == 10000). That's a real,
unexplained contradiction under Jobber's own documented rule ("throttled
when requestedQueryCost > currentlyAvailable") -- a full bucket shouldn't
throttle a query unless that one query's cost is implausibly enormous.

_log_throttle_status() in apps/jobber/services/client.py now also logs
requestedQueryCost/actualQueryCost (2026-08-28 fix, this same round) --
so whichever way this diagnostic comes out, the real Django log will show
the actual cost Jobber computed for THIS trivial query, not just the
bucket state.

This script runs the smallest possible real query --
`{ account { id name } }`, already defined as client.py's own
_ACCOUNT_QUERY -- via the same execute() every other call goes through,
completely unmodified. Two possible outcomes, both real answers:
  - SUCCEEDS: today's job-lookup failure was specific to that query's own
    cost/structure (nested connections), not an account-wide condition.
  - FAILS the same way: something account-level is wrong right now (the
    bucket genuinely exhausted despite what it reports, or the DDoS-layer
    limiter, now separately detected per the same round's execute() fix),
    not the job-lookup query's fault specifically.

Run via `python manage.py shell < verify_jobber_trivial_query.py` --
check the real Django log output alongside this script's own prints for
the new requestedQueryCost/actualQueryCost/HTTP-429 detail.
"""
from apps.jobber.models import JobberAccount
from apps.jobber.services.client import JobberAPIError, _ACCOUNT_QUERY, execute

TENANT_ID = 4

account = JobberAccount.objects.filter(tenant_id=TENANT_ID, is_active=True).first()
print("account:", account)

if account is None:
    print(f"No active JobberAccount for tenant_id={TENANT_ID} -- cannot query live.")
else:
    print(f"\nRunning the simplest possible real query ({_ACCOUNT_QUERY.strip()}) against tenant_id={TENANT_ID}...")
    try:
        data = execute(account, _ACCOUNT_QUERY)
        print("\nSUCCEEDED.")
        print("account data:", (data or {}).get('account'))
        print(
            "\nConclusion: a trivial query works right now -- today's job-lookup "
            "failure looks specific to that query's own cost/structure (nested "
            "visits/timeSheetEntries connections), not an account-wide condition. "
            "Check the Django log output above/alongside this for the real "
            "requestedQueryCost/actualQueryCost this trivial query reported."
        )
    except JobberAPIError as exc:
        print(f"\nFAILED: {exc}")
        print(
            "\nConclusion: even the simplest possible query fails right now -- "
            "this points at something account-level (the cost bucket genuinely "
            "exhausted despite what it reports, or the DDoS-layer request-count "
            "limiter), not the job-lookup query's own cost/structure. Check the "
            "Django log output above for whether this was the query-cost THROTTLED "
            "path or the new distinct HTTP 429 log line -- both are now logged "
            "separately as of this round's execute() fix."
        )

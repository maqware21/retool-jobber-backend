"""
Real verification (2026-09-06) -- Revenue Health Phase 2, Part B,
deliberately different test case. Verification only, no building yet.

Job #7 (tenant_id=4) is brand new, status Upcoming -- NOT assumed to
already have a local JobberJob row (this project's sync only captures
what it's already pulled; a job created after the last sync may simply
not exist locally yet). Searches for it LIVE by job number instead of
assuming a local row exists -- the same 2-step pattern already
established in this project for exactly this situation: a cheap
id+jobNumber search across every real job first, THEN one detailed call
using the real id that search returns. Jobber's schema has no "find job
by jobNumber" lookup -- only Query.job(id:) accepts a real id, so the
number itself must be searched for first.

Run via `python manage.py shell < verify_job7_lineitem_cost.py`.

Real, independently-confirmed expected answer (read directly off
Jobber's own UI, given directly, not guessed): cost Rs 400, profit
Rs 600, margin 60% -- for one line item, Unit Cost 200 x Unit Price 500,
quantity 2 (cost 200*2=400, price 500*2=1000, profit 1000-400=600,
margin 600/1000=60%). This script reports the LIVE API's own values
plainly, for direct comparison against that -- it does not assume they
match.
"""
from apps.jobber.models import JobberAccount
from apps.jobber.services import client
from apps.jobber.services.client import JobberAPIError, execute

TENANT_ID = 4
TARGET_JOB_NUMBER = 7

# Step 1 -- cheap search: id + jobNumber ONLY, no nested detail at all,
# paginated across every real job via the existing fetch_all_pages()
# helper. Deliberately NOT the heavier _JOBS_QUERY (title, lineItems,
# client tags, etc.) -- avoids the exact real cost mistake already found
# and fixed earlier in this project (a full-account job search that
# pulled full visit+timesheet detail per job hit 28330/10000 query-cost
# points; this search only ever costs 2 cheap scalar fields per job).
_JOB_SEARCH_QUERY = """
query SearchJobsByNumber($first: Int!, $after: String) {
  jobs(first: $first, after: $after) {
    nodes {
      id
      jobNumber
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

# Step 2 -- targeted detail call, via Query.job(id:), using the real id
# the search above returns. Requests ONLY jobCosting's 5 named fields --
# nothing else.
_JOB_DETAIL_QUERY = """
query GetJobCostingDetail($id: EncodedId!) {
  job(id: $id) {
    id
    jobNumber
    jobCosting {
      lineItemCost
      expenseCost
      totalCost
      profitAmount
      profitPercentage
    }
  }
}
"""


def _search_jobs(account, first=25, after=None):
    data = execute(account, _JOB_SEARCH_QUERY, {'first': first, 'after': after})
    return (data or {}).get('jobs') or {}


account = JobberAccount.objects.filter(tenant_id=TENANT_ID, is_active=True).first()
print("account:", account)

if account is None:
    print(f"No active JobberAccount for tenant_id={TENANT_ID} -- cannot query live.")
else:
    try:
        nodes = client.fetch_all_pages(_search_jobs, account, 'search_jobs_by_number')
    except JobberAPIError as exc:
        print(f"Jobber API error during search: {exc}")
        nodes = []

    print(f"{len(nodes)} real job(s) found via live search.")
    match = next((n for n in nodes if n.get('jobNumber') == TARGET_JOB_NUMBER), None)

    if match is None:
        print(f"\nJob #{TARGET_JOB_NUMBER} not found via live search across {len(nodes)} real jobs.")
    else:
        print(f"\nJob #{TARGET_JOB_NUMBER} found live -- real id={match['id']}")
        try:
            data = execute(account, _JOB_DETAIL_QUERY, {'id': match['id']})
        except JobberAPIError as exc:
            print(f"Jobber API error fetching detail: {exc}")
        else:
            job = (data or {}).get('job')
            if job is None:
                print(f"Live job(id=...) returned null for id={match['id']}.")
            else:
                costing = job.get('jobCosting') or {}
                print(f"\n=== Job #{job.get('jobNumber')} -- real jobCosting values ===")
                print(f"lineItemCost:     {costing.get('lineItemCost')!r}")
                print(f"expenseCost:      {costing.get('expenseCost')!r}")
                print(f"totalCost:        {costing.get('totalCost')!r}")
                print(f"profitAmount:     {costing.get('profitAmount')!r}")
                print(f"profitPercentage: {costing.get('profitPercentage')!r}")
                print("\nExpected (from Jobber's own UI, given directly): cost=400 profit=600 margin=60%")

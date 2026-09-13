"""
Real verification (2026-09-14) -- Cost Breakdown Dynamic Categories,
follow-up. Verification only, no code changes.

Context: the prior round confirmed Expense has NO accounting-code field
anywhere in the schema, and job_number=6's real expense's title/
description don't mention "Equipment" either -- the categorization
concept this whole design depended on may not be exposed via this API
at all. This script separately confirms Query.expenses' own real shape
regardless of that finding, since it's still relevant to know whether
an account-wide expense total/list is even cheaply fetchable at all.

Run via `python manage.py shell < verify_query_expenses_shape_and_cost.py`.

Reports:
  1. Query.expenses' real arguments (introspected directly from the
     Query type's own field definition, not guessed) -- is it a plain
     account-wide connection, or does it require a job/other filter
     argument? Does it accept any real date-range filter args?
  2. If account-wide: fetches the account's REAL, COMPLETE expense list
     this way (paginated via the same fetch_all_pages() helper already
     used everywhere else in this project) and prints every real
     expense (title, description, total, date) plainly, plus the real
     total count and sum -- confirms job #6's $600 LED Fixture expense
     appears in this list, and shows what else is really there.
  3. The real query cost of fetching ALL of it this way (from the
     throttle-status log line _log_throttle_status() already prints
     for every response) -- reports plainly whether this is cheap
     enough to sync regularly like every other entity, or needs the
     same page-size care _SYNC_JOBS_QUERY needed.
"""
from apps.jobber.models import JobberAccount
from apps.jobber.services import client
from apps.jobber.services.client import JobberAPIError, execute

TENANT_ID = 5

account = JobberAccount.objects.filter(tenant_id=TENANT_ID, is_active=True).first()
print("account:", account)

if account is None:
    print(f"No active JobberAccount for tenant_id={TENANT_ID} -- cannot query live.")
else:
    print("=== 1. Query.expenses' real arguments (introspected directly) ===")
    try:
        data = execute(account, """
            query IntrospectQueryExpensesArgs {
              __type(name: "Query") {
                fields {
                  name
                  args {
                    name
                    description
                    type { name kind ofType { name kind ofType { name kind } } }
                  }
                }
              }
            }
        """)
        query_fields = ((data or {}).get('__type') or {}).get('fields') or []
        expenses_field = next((f for f in query_fields if f['name'] == 'expenses'), None)
        print(f"  Query.expenses real field definition: {expenses_field}")
    except JobberAPIError as exc:
        print(f"  Jobber API error introspecting Query.expenses' args: {exc}")

    print("\n=== 2. Real, account-wide expense list via Query.expenses ===")

    def _fetch_expenses_page(account_arg, first=25, after=None):
        data = execute(account_arg, """
            query GetAllExpenses($first: Int!, $after: String) {
              expenses(first: $first, after: $after) {
                nodes {
                  id
                  title
                  description
                  date
                  total
                  linkedJob { id jobNumber }
                }
                pageInfo { hasNextPage endCursor }
              }
            }
        """, {'first': first, 'after': after})
        return (data or {}).get('expenses') or {'nodes': [], 'pageInfo': {'hasNextPage': False, 'endCursor': None}}

    try:
        nodes = client.fetch_all_pages(_fetch_expenses_page, account, 'fetch_all_expenses_diagnostic')
        print(f"  {len(nodes)} REAL expense(s) across the WHOLE account:")
        total_sum = 0.0
        for n in nodes:
            job_ref = n.get('linkedJob') or {}
            print(f"    title={n.get('title')!r} description={n.get('description')!r} "
                  f"total={n.get('total')} date={n.get('date')} "
                  f"linkedJob=job_number={job_ref.get('jobNumber')}")
            total_sum += n.get('total') or 0
        print(f"\n  Real sum of ALL real expenses' total: {total_sum}")
        job6_expense = [n for n in nodes if (n.get('linkedJob') or {}).get('jobNumber') == 6]
        print(f"  Job #6's $600 LED Fixture expense present in this list? "
              f"{'YES' if any(e.get('total') == 600.0 for e in job6_expense) else 'NOT FOUND'}")
    except JobberAPIError as exc:
        print(f"  FAILED fetching all expenses: {exc}")

    print("\n=== 3. Real query cost -- see the throttle-status log lines above this print, "
          "each shows requestedQueryCost/actualQueryCost for that real page fetched ===")

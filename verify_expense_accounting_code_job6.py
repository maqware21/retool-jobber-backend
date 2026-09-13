"""
Real schema + real data verification (2026-09-14) -- Cost Breakdown
Dynamic Categories proposal, STEP 1. Verification only, no code
changes.

Confirms, from Jobber's own GraphQL schema directly (not assumed):
  1. Whether Job exposes a real connection to its own linked Expenses
     (checked via introspection on the Job type -- avoids guessing the
     field name).
  2. Whether Query exposes a root-level way to fetch Expenses at all
     (checked via introspection on Query, same as the earlier,
     since-removed verify_expense_and_lineitem_category_schema.py --
     re-run fresh here rather than assumed still valid).
  3. The real Expense type's own fields + schema-authored descriptions
     -- specifically the accounting-code field's real name and whether
     a separate cost/amount field exists alongside it.
  4. Real, live data: job_number=6 (tenant_id=5) is CONFIRMED to have a
     real expense with a real 'Equipment' accounting code entered --
     this fetches that job's real, live expense(s) directly (via
     whichever real path steps 1/2 establish) and prints the accounting
     code field's real value and the real cost/amount value, plainly.

Uses the same cheap, targeted-lookup discipline already established in
this project (a single real job's linked data, not a full-account
scan) -- job_number=6 is looked up locally first for its real jobber_id,
matching the existing 2-step pattern (cheap local lookup, then one
targeted live call).

Run via `python manage.py shell < verify_expense_accounting_code_job6.py`.
"""
from apps.jobber.models import JobberAccount, JobberJob
from apps.jobber.services.client import JobberAPIError, execute

TENANT_ID = 5
JOB_NUMBER = 6

_JOB_TYPE_INTROSPECTION_QUERY = """
query IntrospectJobType {
  __type(name: "Job") {
    fields {
      name
    }
  }
}
"""

_QUERY_ROOT_INTROSPECTION_QUERY = """
query IntrospectQueryRoot {
  __type(name: "Query") {
    fields {
      name
    }
  }
}
"""

_EXPENSE_TYPE_INTROSPECTION_QUERY = """
query IntrospectExpenseType {
  __type(name: "Expense") {
    name
    fields {
      name
      description
      type { name kind ofType { name kind } }
    }
  }
}
"""

account = JobberAccount.objects.filter(tenant_id=TENANT_ID, is_active=True).first()
print("account:", account)

if account is None:
    print(f"No active JobberAccount for tenant_id={TENANT_ID} -- cannot query live.")
else:
    job6 = JobberJob.objects.filter(tenant_id=TENANT_ID, is_active=True, job_number=JOB_NUMBER).first()
    print(f"Job (job_number={JOB_NUMBER}): {job6}")

    print("\n=== 1. Does Job expose a real connection to its own linked Expenses? ===")
    job_expense_field = None
    try:
        data = execute(account, _JOB_TYPE_INTROSPECTION_QUERY)
        field_names = [f['name'] for f in ((data or {}).get('__type') or {}).get('fields', [])]
        expense_related = [n for n in field_names if 'expense' in n.lower()]
        print(f"  {len(field_names)} total Job fields. Expense-related: {expense_related}")
        if expense_related:
            job_expense_field = expense_related[0]
    except JobberAPIError as exc:
        print(f"  Jobber API error during Job type introspection: {exc}")

    print("\n=== 2. Does Query expose a root-level way to fetch Expenses? ===")
    query_expense_field = None
    try:
        data = execute(account, _QUERY_ROOT_INTROSPECTION_QUERY)
        field_names = [f['name'] for f in ((data or {}).get('__type') or {}).get('fields', [])]
        expense_related = [n for n in field_names if 'expense' in n.lower()]
        print(f"  {len(field_names)} total root Query fields. Expense-related: {expense_related}")
        if expense_related:
            query_expense_field = expense_related[0]
    except JobberAPIError as exc:
        print(f"  Jobber API error during Query root introspection: {exc}")

    print("\n=== 3. Real Expense type fields + descriptions (accounting code + cost/amount) ===")
    try:
        data = execute(account, _EXPENSE_TYPE_INTROSPECTION_QUERY)
        type_info = (data or {}).get('__type')
        if type_info is None:
            print("  __type(name: \"Expense\") returned null -- no type by this exact name.")
        else:
            for f in (type_info.get('fields') or []):
                marker = ""
                if any(kw in f['name'].lower() for kw in ('categ', 'account', 'code', 'cost', 'amount', 'total')):
                    marker = "  <-- possible categorization/cost field"
                print(f"  {f['name']}: {f.get('description') or '(no description)'}{marker}")
    except JobberAPIError as exc:
        print(f"  Jobber API error during Expense type introspection: {exc}")

    print(f"\n=== 4. Real, live expense data for job_number={JOB_NUMBER} ===")
    if job6 is None:
        print(f"  No local JobberJob row for job_number={JOB_NUMBER} -- cannot look up its real id.")
    elif job_expense_field:
        try:
            data = execute(
                account,
                f"""
                query GetJobExpenses($id: EncodedId!) {{
                  job(id: $id) {{
                    id
                    jobNumber
                    {job_expense_field}(first: 10) {{
                      nodes {{
                        id
                        total
                        description
                        linkedAccountingCode {{ id name }}
                      }}
                    }}
                  }}
                }}
                """,
                {'id': job6.jobber_id},
            )
            job_live = (data or {}).get('job')
            nodes = ((job_live or {}).get(job_expense_field) or {}).get('nodes') or []
            print(f"  {len(nodes)} real expense(s) via Job.{job_expense_field}: {nodes}")
        except JobberAPIError as exc:
            print(f"  FAILED querying Job.{job_expense_field} with a guessed shape: {exc}")
            print("  Re-check step 1's real field list above and step 3's real Expense fields "
                  "to construct the correct query by hand -- this guessed shape "
                  "(total/description/linkedAccountingCode{id name}) may not match the real schema.")
    elif query_expense_field:
        try:
            data = execute(
                account,
                f"""
                query GetRealExpenses {{
                  {query_expense_field}(first: 25) {{
                    nodes {{
                      id
                      total
                      description
                      linkedAccountingCode {{ id name }}
                    }}
                  }}
                }}
                """,
            )
            nodes = ((data or {}).get(query_expense_field) or {}).get('nodes') or []
            matching = [n for n in nodes]
            print(f"  {len(nodes)} real expense(s) via root Query.{query_expense_field} "
                  f"(NOT job-filtered -- Job had no expense connection per step 1, "
                  f"so this is every real expense on the account): {matching}")
        except JobberAPIError as exc:
            print(f"  FAILED querying Query.{query_expense_field} with a guessed shape: {exc}")
            print("  Re-check step 2's real field list above and step 3's real Expense fields "
                  "to construct the correct query by hand.")
    else:
        print("  No Expense-related field found on either Job or Query in steps 1/2 -- "
              "cannot fetch real data without knowing the real field name first.")

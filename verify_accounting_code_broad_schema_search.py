"""
Real, broad schema search (2026-09-14) -- Cost Breakdown Dynamic
Categories, STEP 1 follow-up. Verification only, no code changes.

Real finding just confirmed: Jobber's own `Expense` type has NO
accounting-code field at all -- its complete real field list is title,
description, date, total, enteredBy, paidBy, reimbursableTo, linkedJob,
createdAt, updatedAt, id. The "Equipment" categorization must live
somewhere else in the real schema (or, possibly, isn't a structured
field at all -- see the direct real-data check at the end of this
script).

Does NOT guess a third time -- searches broadly and directly:
  1. A full schema type-name search for anything with "account" or
     "code" in its OWN type name (__schema { types { name } }).
  2. Job's COMPLETE real field list (not just the expense-related
     subset already seen) -- checks for a code/account field living
     directly on Job, separate from its Expenses.
  3. The real type LineItem resolves to (Job.lineItems' own field
     type) -- introspected fully, checking for anything beyond the
     already-confirmed category (PRODUCT/SERVICE only).
  4. If any promising type name surfaces in step 1, introspects that
     type's own real fields directly.
  5. Direct, real data: refetches job_number=6's real expense(s) using
     ONLY the fields step 3 of the prior script actually confirmed
     exist on Expense (title, description, date, total, linkedJob,
     enteredBy, paidBy, reimbursableTo, createdAt, updatedAt) -- prints
     every one plainly. If "Equipment" turns out to be sitting in
     title/description as free text rather than a structured field,
     this step shows that directly, which is itself a real, valid,
     reportable answer (not a failure) -- it would mean this project's
     aggregation needs to key off that real field instead of a
     nonexistent accounting-code entity.

Run via `python manage.py shell < verify_accounting_code_broad_schema_search.py`.
"""
from apps.jobber.models import JobberAccount, JobberJob
from apps.jobber.services.client import JobberAPIError, execute

TENANT_ID = 5
JOB_NUMBER = 6

account = JobberAccount.objects.filter(tenant_id=TENANT_ID, is_active=True).first()
print("account:", account)

if account is None:
    print(f"No active JobberAccount for tenant_id={TENANT_ID} -- cannot query live.")
else:
    job6 = JobberJob.objects.filter(tenant_id=TENANT_ID, is_active=True, job_number=JOB_NUMBER).first()
    print(f"Job (job_number={JOB_NUMBER}): {job6}")

    print("\n=== 1. Full schema type-name search for 'account'/'code' ===")
    matching_type_names = []
    try:
        data = execute(account, """
            query FullSchemaTypeNames {
              __schema { types { name kind } }
            }
        """)
        all_types = ((data or {}).get('__schema') or {}).get('types') or []
        matching = [
            t for t in all_types
            if ('account' in t['name'].lower() or 'code' in t['name'].lower())
            and not t['name'].startswith('__')
        ]
        print(f"  {len(all_types)} total real types in the schema. Matching (by TYPE NAME): "
              f"{[(t['name'], t['kind']) for t in matching]}")
        matching_type_names = [t['name'] for t in matching]
    except JobberAPIError as exc:
        print(f"  Jobber API error during full schema type search: {exc}")

    print("\n=== 2. Job's COMPLETE real field list (not just expense-related) ===")
    try:
        data = execute(account, """
            query FullJobFields {
              __type(name: "Job") { fields { name } }
            }
        """)
        job_fields = [f['name'] for f in ((data or {}).get('__type') or {}).get('fields', [])]
        code_like = [n for n in job_fields if 'code' in n.lower() or 'account' in n.lower()]
        print(f"  All {len(job_fields)} real Job fields: {job_fields}")
        print(f"  Code/account-like Job fields: {code_like}")
    except JobberAPIError as exc:
        print(f"  Jobber API error during full Job field listing: {exc}")

    print("\n=== 3. The real type Job.lineItems resolves to -- full fields ===")
    try:
        data = execute(account, """
            query JobLineItemsFieldType {
              __type(name: "Job") {
                fields(includeDeprecated: true) {
                  name
                  type { name kind ofType { name kind ofType { name kind } } }
                }
              }
            }
        """)
        job_fields_typed = ((data or {}).get('__type') or {}).get('fields') or []
        line_items_field = next((f for f in job_fields_typed if f['name'] == 'lineItems'), None)
        print(f"  Job.lineItems field type info: {line_items_field}")

        # Resolve down through LIST/NON_NULL wrappers to the real named type.
        def _unwrap(t):
            while t and t.get('name') is None and t.get('ofType'):
                t = t['ofType']
            return t.get('name') if t else None

        line_item_conn_type_name = _unwrap(line_items_field['type']) if line_items_field else None
        print(f"  Resolved connection type name: {line_item_conn_type_name}")
    except JobberAPIError as exc:
        print(f"  Jobber API error resolving Job.lineItems' type: {exc}")
        line_item_conn_type_name = None

    if line_item_conn_type_name:
        try:
            data = execute(account, f"""
                query IntrospectLineItemConnection {{
                  __type(name: "{line_item_conn_type_name}") {{ fields {{ name }} }}
                }}
            """)
            conn_fields = [f['name'] for f in ((data or {}).get('__type') or {}).get('fields', [])]
            print(f"  {line_item_conn_type_name}'s real fields: {conn_fields}")
        except JobberAPIError as exc:
            print(f"  Jobber API error introspecting {line_item_conn_type_name}: {exc}")

    print("\n=== 4. Introspecting any type name matches from step 1 ===")
    if not matching_type_names:
        print("  No type names matched 'account'/'code' in step 1 -- nothing to introspect here.")
    for type_name in matching_type_names:
        try:
            data = execute(account, f"""
                query IntrospectMatchedType {{
                  __type(name: "{type_name}") {{
                    name
                    fields {{ name description }}
                  }}
                }}
            """)
            type_info = (data or {}).get('__type')
            print(f"  {type_name}: {type_info}")
        except JobberAPIError as exc:
            print(f"  Jobber API error introspecting {type_name}: {exc}")

    print(f"\n=== 5. Real, direct data: job_number={JOB_NUMBER}'s real expense(s), "
          f"CONFIRMED real fields only ===")
    if job6 is None:
        print(f"  No local JobberJob row for job_number={JOB_NUMBER}.")
    else:
        try:
            data = execute(account, """
                query GetJobExpensesConfirmedFields($id: EncodedId!) {
                  job(id: $id) {
                    id
                    jobNumber
                    expenses(first: 10) {
                      nodes {
                        id
                        title
                        description
                        date
                        total
                        createdAt
                        updatedAt
                        enteredBy { id name { full } }
                        paidBy { id name { full } }
                        reimbursableTo { id name { full } }
                      }
                    }
                  }
                }
            """, {'id': job6.jobber_id})
            job_live = (data or {}).get('job')
            nodes = ((job_live or {}).get('expenses') or {}).get('nodes') or []
            print(f"  {len(nodes)} real expense(s) on job_number={JOB_NUMBER}:")
            for n in nodes:
                print(f"    {n}")
        except JobberAPIError as exc:
            print(f"  FAILED: {exc}")

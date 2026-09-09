"""
Real schema verification (2026-09-09) -- Labor Cost proposal, STEP 1.
Confirms, from Jobber's own GraphQL schema directly (not assumed, and
not just Jobber's help-center documentation, which explains the PRODUCT
UX -- Manage Team's per-employee "labour cost" $/hr default, with a
manually-added time entry's own "Employee cost per hour" field able to
override it for that one entry -- but not the underlying schema/field
names actually used), whether TimeSheetEntry.labourRate (already
confirmed real and synced, see verify_labour_rate_field.py) IS the one
underlying stored field for both cases, or whether a genuinely SEPARATE
team-level field exists elsewhere in the schema (e.g. on User).

Run via `python manage.py shell < verify_labour_rate_default_vs_override_schema.py`.

Three independent, cheap, live checks:
  1. Introspect TimeSheetEntry's own fields, including each field's
     `description` string -- Jobber's schema may directly document the
     default/override relationship in labourRate's own description text,
     which would settle this directly from the schema itself.
  2. Introspect User's fields -- checks for any separate rate/cost/wage
     field that might represent the Manage Team default independently
     of TimeSheetEntry.
  3. For one real, already-synced user with real time entries, prints
     that user's real TimeSheetEntry.labourRate values across ALL their
     entries side by side -- if every one of a single user's entries
     shows the SAME real rate (never overridden), that's consistent with
     "inherits the team default"; if some entries genuinely differ,
     that's direct, real evidence of a per-entry override actually being
     used, not just theoretically possible.
"""
from apps.jobber.models import JobberAccount, JobberTimeSheetEntry
from apps.jobber.services.client import JobberAPIError, execute

TENANT_ID = 4

_TIMESHEET_ENTRY_INTROSPECTION_QUERY = """
query IntrospectTimeSheetEntryType {
  __type(name: "TimeSheetEntry") {
    name
    fields {
      name
      description
      type { name kind ofType { name kind } }
    }
  }
}
"""

_USER_INTROSPECTION_QUERY = """
query IntrospectUserType {
  __type(name: "User") {
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
    print("\n=== 1. Introspection: TimeSheetEntry's real fields + descriptions ===")
    try:
        data = execute(account, _TIMESHEET_ENTRY_INTROSPECTION_QUERY)
        type_info = (data or {}).get('__type') or {}
        for f in (type_info.get('fields') or []):
            marker = "  <-- labourRate" if f['name'].lower() == 'labourrate' else ""
            print(f"  {f['name']}: {f.get('description') or '(no description)'}{marker}")
    except JobberAPIError as exc:
        print(f"  Jobber API error during TimeSheetEntry introspection: {exc}")

    print("\n=== 2. Introspection: User's real fields + descriptions (checking for a separate team-level rate) ===")
    try:
        data = execute(account, _USER_INTROSPECTION_QUERY)
        type_info = (data or {}).get('__type') or {}
        rate_like = [
            f for f in (type_info.get('fields') or [])
            if any(kw in f['name'].lower() for kw in ('rate', 'cost', 'wage', 'labour', 'labor'))
        ]
        if rate_like:
            print(f"  {len(rate_like)} rate/cost/wage-like field(s) found on User:")
            for f in rate_like:
                print(f"    {f['name']}: {f.get('description') or '(no description)'}")
        else:
            print("  No rate/cost/wage-like field found on User -- no separate team-level field visible here.")
    except JobberAPIError as exc:
        print(f"  Jobber API error during User introspection: {exc}")

    print("\n=== 3. Real data: does any ONE user's own entries ever show a DIFFERENT labourRate? ===")
    from django.db.models import Count

    users_with_multiple_entries = (
        JobberTimeSheetEntry.objects.filter(tenant_id=TENANT_ID, is_active=True, user__isnull=False)
        .values('user_id', 'user__name')
        .annotate(entry_count=Count('id'))
        .filter(entry_count__gt=1)
    )
    if not users_with_multiple_entries:
        print("  No real user has more than 1 real, locally-synced timesheet entry yet -- nothing to compare.")
    for row in users_with_multiple_entries:
        rates = list(
            JobberTimeSheetEntry.objects.filter(
                tenant_id=TENANT_ID, is_active=True, user_id=row['user_id'],
            ).values_list('labour_rate', flat=True)
        )
        distinct_rates = set(rates)
        print(f"  {row['user__name']} (user_id={row['user_id']}): {len(rates)} real entries, "
              f"rates={rates} -> {'SAME rate every time' if len(distinct_rates) <= 1 else 'DIFFERENT rates seen -- real override in use'}")

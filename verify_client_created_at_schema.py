"""
Real schema verification (2026-09-09) -- Revenue Health "New Customers"
proposal, STEP 1. This exact check was flagged as needed during the
earlier Revenue Health audit (revenue_health_audit.md) but never
actually run -- confirming it now, not assuming either way.

Run via `python manage.py shell < verify_client_created_at_schema.py`.

Two independent checks, both live, both cheap (single-object lookups,
no full-account scan):
  1. A real GraphQL introspection query against Client -- lists every
     real field Jobber's own schema defines on that type, definitively
     answering whether createdAt (or anything similarly named) exists,
     regardless of what check 2 below returns.
  2. A direct query requesting client(id:) { createdAt } for one real,
     already-synced client -- if the field exists, this shows its real,
     current value (or null); if it doesn't exist, Jobber's GraphQL
     server rejects it with a schema validation error at the top-level
     `errors` array (logged in full by execute() itself, cost=0 --
     rejected before execution, not charged and refunded).
"""
from apps.jobber.models import JobberAccount, JobberClient
from apps.jobber.services.client import JobberAPIError, execute

TENANT_ID = 4

_CLIENT_TYPE_INTROSPECTION_QUERY = """
query IntrospectClientType {
  __type(name: "Client") {
    name
    fields {
      name
      type {
        name
        kind
        ofType { name kind }
      }
    }
  }
}
"""

account = JobberAccount.objects.filter(tenant_id=TENANT_ID, is_active=True).first()
print("account:", account)

if account is None:
    print(f"No active JobberAccount for tenant_id={TENANT_ID} -- cannot query live.")
else:
    print("\n=== 1. Introspection: every real field on Jobber's Client type ===")
    try:
        data = execute(account, _CLIENT_TYPE_INTROSPECTION_QUERY)
        type_info = (data or {}).get('__type')
        if type_info is None:
            print("  __type(name: \"Client\") returned null -- unexpected, investigate.")
        else:
            field_names = [f['name'] for f in (type_info.get('fields') or [])]
            print(f"  {len(field_names)} real field(s) on Client: {sorted(field_names)}")
            has_created_at = any('createdat' in name.lower() for name in field_names)
            print(f"\n  Does a createdAt-like field exist on Client? {has_created_at}")
    except JobberAPIError as exc:
        print(f"  Jobber API error during introspection: {exc}")

    print("\n=== 2. Direct field request: client(id:) { createdAt } for one real client ===")
    local_client = JobberClient.objects.filter(tenant_id=TENANT_ID, is_active=True).first()
    print(f"  local client picked: {local_client}")
    if local_client is None:
        print("  No locally-synced JobberClient found -- cannot run this check.")
    else:
        try:
            data = execute(
                account,
                """
                query GetClientCreatedAt($id: EncodedId!) {
                  client(id: $id) {
                    id
                    name
                    createdAt
                  }
                }
                """,
                {'id': local_client.jobber_id},
            )
            client = (data or {}).get('client')
            print(f"  SUCCESS -- real client.createdAt = {client.get('createdAt')!r}" if client else "  null client returned.")
        except JobberAPIError as exc:
            print(f"  FAILED: {exc} -- see the logged error detail above for the exact reason "
                  f"(e.g. a real GraphQL schema validation error naming the invalid field).")

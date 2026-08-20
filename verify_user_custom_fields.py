"""
Part B, Step 2 verification: do real "Expertise"/"Experience" Team
custom field values actually come back for real technicians? Run via
`python manage.py shell < verify_user_custom_fields.py`.

No relation to any existing sync logic -- this is a standalone, one-off
live query against 2-3 real JobberUser rows, purely informational. No
code changes made by this script; nothing written to the database.

customFields is a UNION (CustomFieldUnion -- confirmed against the
schema, not assumed): [CustomFieldArea | CustomFieldDropdown |
CustomFieldLink | CustomFieldNumeric | CustomFieldText |
CustomFieldTrueFalse]. GraphQL unions expose NO common field without an
inline fragment per concrete type, so this query spreads all 6 --
Jobber doesn't tell us up front which concrete type "Expertise"/
"Experience" were configured as.
"""
import json

from apps.jobber.models import JobberAccount, JobberUser
from apps.jobber.services import client

# Standing instruction: always filter test-account lookups by tenant_id=1
# explicitly.
account = JobberAccount.objects.filter(is_active=True, tenant_id=1).first()
tenant_id = account.tenant_id
print("tenant_id:", tenant_id)

users = JobberUser.objects.filter(tenant_id=tenant_id, is_active=True).order_by('name')[:3]
print(f"Checking customFields live for {users.count()} real technicians:")

_CUSTOM_FIELDS_QUERY = """
query GetUserCustomFieldsCheck($id: EncodedId!) {
  user(id: $id) {
    id
    name { full }
    customFields {
      ... on CustomFieldText { label valueText }
      ... on CustomFieldDropdown { label valueDropdown }
      ... on CustomFieldNumeric { label valueNumeric unit }
      ... on CustomFieldArea { label valueArea { length width } unit }
      ... on CustomFieldLink { label valueLink { text url } }
      ... on CustomFieldTrueFalse { label valueTrueFalse }
    }
  }
}
"""

for user in users:
    data = client.execute(account, _CUSTOM_FIELDS_QUERY, {'id': user.jobber_id})
    live_user = (data or {}).get('user') or {}
    custom_fields = live_user.get('customFields') or []
    print(json.dumps({
        'local_name': user.name,
        'live_name': (live_user.get('name') or {}).get('full'),
        'custom_fields_raw': custom_fields,
    }, indent=2, default=str))

    # Specifically surface Expertise/Experience if present, so the real
    # shape/value is unambiguous in the output, not just buried in the
    # raw list above.
    for field in custom_fields:
        label = field.get('label')
        if label in ('Expertise', 'Experience'):
            print(f"  -> FOUND '{label}':", {k: v for k, v in field.items() if k != 'label'})

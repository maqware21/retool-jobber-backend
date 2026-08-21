"""
Verification for the Critical-before-Warning sort in
evaluate_alert_rules() (2026-08-21, confirmed TL decision: sort once,
server-side; nothing collapsed/hidden). Run via
`python manage.py shell < verify_alert_sort_order.py`.

Confirms, against real data:
  1) No Warning entry appears before any Critical entry anywhere in the
     returned list (the actual requirement -- global partition, not
     just "each technician's own alerts are in the right relative
     order").
  2) Re-running the same call produces the SAME order twice in a row
     (the "deterministic, doesn't jump around" requirement) -- calls
     evaluate_alert_rules() twice and diffs the two lists.
  3) Prints the full ordered list, and specifically highlights any
     technician who appears in both a Critical and a Warning entry, so
     you can see directly that their Critical entry is positioned
     before their Warning entry.
"""
import json

from apps.alerts.services.evaluate import evaluate_alert_rules
from apps.tenants.models import Tenant

tenant = Tenant.objects.filter(id=1).first()
print("tenant:", tenant)

triggered = evaluate_alert_rules(tenant)
print(f"\n=== {len(triggered)} triggered alert(s), in returned order ===")
for i, t in enumerate(triggered):
    print(f"[{i}] severity={t['severity']:<8} user={t['user_name']!r:<25} rule={t['rule_type_display']!r} "
          f"actual={t['actual_value']} threshold={t['threshold_value']}")

# --- Check 1: no warning appears before any critical, globally ---
severities = [t['severity'] for t in triggered]
first_warning_idx = next((i for i, s in enumerate(severities) if s == 'warning'), None)
last_critical_idx = max((i for i, s in enumerate(severities) if s == 'critical'), default=-1)

print("\n=== Check 1: global severity partition ===")
if first_warning_idx is None or last_critical_idx == -1:
    print("Only one severity present (or none triggered) -- partition check not applicable, trivially fine.")
elif first_warning_idx > last_critical_idx:
    print(f"PASS -- first warning at index {first_warning_idx}, last critical at index {last_critical_idx}.")
else:
    print(f"FAIL -- a warning at index {first_warning_idx} appears before a critical at index {last_critical_idx}.")

# --- Check 2: deterministic across repeated calls ---
triggered_again = evaluate_alert_rules(tenant)
same_order = triggered == triggered_again
print("\n=== Check 2: repeated call produces identical order ===")
print("PASS" if same_order else "FAIL -- order changed between two calls against the same data.")

# --- Check 3: highlight any technician in both a Critical and Warning entry ---
by_user = {}
for i, t in enumerate(triggered):
    by_user.setdefault(t['user_id'], []).append((i, t['severity']))

print("\n=== Check 3: technicians appearing in both severities ===")
found_mixed = False
for user_id, entries in by_user.items():
    sevs = {s for _, s in entries}
    if 'critical' in sevs and 'warning' in sevs:
        found_mixed = True
        crit_idx = min(i for i, s in entries if s == 'critical')
        warn_idx = min(i for i, s in entries if s == 'warning')
        name = next(t['user_name'] for t in triggered if t['user_id'] == user_id)
        status = 'PASS' if crit_idx < warn_idx else 'FAIL'
        print(f"{name!r}: critical at index {crit_idx}, warning at index {warn_idx} -> {status}")

if not found_mixed:
    print("No technician currently triggers both a Critical and a Warning rule -- "
          "create one more rule/lower a threshold to exercise this specific case directly.")

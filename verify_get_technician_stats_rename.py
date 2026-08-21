"""
Regression check for the _local_technician_stats_response(user) ->
get_technician_stats(tenant) rename (Alerts module, Step 2). Confirms
the rename+widened-signature produced byte-for-byte the SAME real
numbers as before -- not a new finding, a check against already-proven
values from prior rounds.

Run via `python manage.py shell < verify_get_technician_stats_rename.py`.

Calls get_technician_stats(tenant) directly with a real Tenant instance
(tenant_id=1, per standing instruction) -- the exact same call shape
JobberTechnicianStatsView.get() now uses (request.user.tenant) -- and
prints Waseem Farhad's full entry for direct comparison against the
already-confirmed reference values:
  revenue: 1850000.0 (formatted as $1,850,000)
  jobs_completed: 3
  team_revenue_share_percentage: 15.6
  goal_progress.progress_percentage: ~93 (monthly)
  annual_goal_progress.progress_percentage: ~46 (on a $4,000,000 annual goal)
"""
import json

from apps.jobber.api.technician_stats import get_technician_stats
from apps.tenants.models import Tenant

tenant = Tenant.objects.filter(id=1).first()
print("tenant:", tenant)

data = get_technician_stats(tenant)
print("connected:", data.get('connected'))
print("last_synced_at:", data.get('last_synced_at'))

waseem = next((t for t in data.get('technicians', []) if 'Waseem' in (t.get('name') or '')), None)

if waseem is None:
    print("\nNo technician matching 'Waseem' found -- full technicians list:")
    print(json.dumps(data.get('technicians', []), indent=2))
else:
    print("\n=== Waseem Farhad's full entry ===")
    print(json.dumps(waseem, indent=2))

    print("\n=== Reference check ===")
    checks = [
        ('revenue', waseem.get('revenue'), 1850000.0),
        ('jobs_completed', waseem.get('jobs_completed'), 3),
        ('team_revenue_share_percentage', waseem.get('team_revenue_share_percentage'), 15.6),
    ]
    for label, actual, expected in checks:
        status = 'MATCH' if actual == expected else 'MISMATCH'
        print(f"{label}: actual={actual!r} expected={expected!r} -> {status}")

    goal_pct = (waseem.get('goal_progress') or {}).get('progress_percentage')
    annual_pct = (waseem.get('annual_goal_progress') or {}).get('progress_percentage')
    annual_goal_amount = (waseem.get('annual_goal_progress') or {}).get('goal_amount')
    print(f"goal_progress.progress_percentage: actual={goal_pct!r} expected=~93")
    print(f"annual_goal_progress.goal_amount: actual={annual_goal_amount!r} expected=4000000.0")
    print(f"annual_goal_progress.progress_percentage: actual={annual_pct!r} expected=~46")

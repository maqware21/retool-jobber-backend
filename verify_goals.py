"""
Goals feature verification. Run via `python manage.py shell < verify_goals.py`.

Tests the model layer + serializer validation directly against the real
connected test account (tenant_id=1, per standing instruction), the same
way prior verification scripts in this project test pure logic before
trusting the HTTP layer. Uses a throwaway month (2099-01) so it can never
collide with a real goal someone has actually set, and deletes every row
it creates at the end -- nothing is left behind in the database.

Covers:
  1) TeamGoal.create() / .fetch() -- create, read back, upsert (update),
     and the (tenant, month) unique constraint actually blocking a dupe.
  2) TechnicianGoal.create() / .fetch() -- same, plus the roster-listing
     query shape (fetch(tenant_id=, month=) with no user_id -> queryset).
  3) TeamGoalSerializer / TechnicianGoalWriteSerializer -- negative
     goal_amount rejected, 'YYYY-MM' month accepted, 'YYYY-MM-01' month
     rejected, cross-tenant user_id rejected.
  4) The ACTUAL code path a real POST runs -- TeamGoalSerializer(instance=
     existing or None, data=..., context=...) and
     TechnicianGoalWriteSerializer likewise, called twice against the same
     month: once with instance=None (create), once with instance=<the row
     just created> (update) -- confirming it updates in place rather than
     trying (and failing) to insert a second row. This is what sections
     1/2 above did NOT prove -- those called the model's create()/a raw
     .save() directly, not the serializer the view actually invokes.
"""
from datetime import date
from decimal import Decimal

from apps.goals.models import TeamGoal, TechnicianGoal
from apps.goals.serializers.team_goal import TeamGoalSerializer
from apps.goals.serializers.technician_goal import TechnicianGoalWriteSerializer
from apps.jobber.models import JobberAccount, JobberUser

TEST_MONTH = date(2099, 1, 1)  # a month nobody has a real goal for
SERIALIZER_TEST_MONTH = date(2099, 3, 1)  # separate month for section 4, so it can't collide with 1-3

account = JobberAccount.objects.filter(is_active=True, tenant_id=1).first()
tenant = account.tenant
print("tenant_id:", tenant.id)

technician = JobberUser.objects.filter(tenant=tenant, is_active=True).first()
if technician is None:
    print("No JobberUser found for this tenant -- technician-goal checks will be skipped.")
print("technician:", technician.name if technician else None, "id:", technician.id if technician else None)

# Clean slate: remove any leftover rows from a prior run of this script.
TeamGoal.objects.filter(tenant=tenant, month__in=[TEST_MONTH, SERIALIZER_TEST_MONTH]).delete()
if technician:
    TechnicianGoal.objects.filter(tenant=tenant, user=technician, month__in=[TEST_MONTH, SERIALIZER_TEST_MONTH]).delete()

print("\n=== 1) TeamGoal.create() / .fetch() / upsert / unique constraint ===")
created = TeamGoal.create({"tenant_id": tenant.id, "month": TEST_MONTH, "goal_amount": Decimal("50000.00")})
print("created:", created is not None, "goal_amount:", created.goal_amount if created else None)

fetched = TeamGoal.fetch(tenant_id=tenant.id, month=TEST_MONTH)
print("fetched back:", fetched is not None, "matches created:", fetched.id == created.id if fetched and created else False)

# Upsert (what the view does on a 2nd POST for the same month): fetch, then update in place.
fetched.goal_amount = Decimal("60000.00")
fetched.save()
refetched = TeamGoal.fetch(tenant_id=tenant.id, month=TEST_MONTH)
print("after upsert, goal_amount:", refetched.goal_amount, "(expected 60000.00)")

# The unique constraint should block a second raw INSERT for the same (tenant, month).
dupe = TeamGoal.create({"tenant_id": tenant.id, "month": TEST_MONTH, "goal_amount": Decimal("1.00")})
print("duplicate raw create() blocked (returns None):", dupe is None)
print("row count for this tenant/month (should stay 1):", TeamGoal.objects.filter(tenant=tenant, month=TEST_MONTH).count())

if technician:
    print("\n=== 2) TechnicianGoal.create() / .fetch() / roster-shape query ===")
    created_t = TechnicianGoal.create({
        "tenant_id": tenant.id, "user_id": technician.id, "month": TEST_MONTH, "goal_amount": Decimal("20000.00"),
    })
    print("created:", created_t is not None)

    single = TechnicianGoal.fetch(tenant_id=tenant.id, user_id=technician.id, month=TEST_MONTH)
    print("fetch(tenant_id=, user_id=, month=) returns a single instance:", single is not None and single.id == created_t.id)

    roster_qs = TechnicianGoal.fetch(tenant_id=tenant.id, month=TEST_MONTH)
    print(
        "fetch(tenant_id=, month=) with no user_id returns a queryset, not .first():",
        hasattr(roster_qs, "count"),
        "-- count:", roster_qs.count(),
    )

print("\n=== 3) Serializer validation ===")
neg = TeamGoalSerializer(data={"month": "2099-02", "goal_amount": "-1.00"})
print("negative goal_amount rejected:", not neg.is_valid(), neg.errors if not neg.is_valid() else None)

bad_month = TeamGoalSerializer(data={"month": "2099-02-01", "goal_amount": "100.00"})
print("'YYYY-MM-01' month rejected:", not bad_month.is_valid(), bad_month.errors if not bad_month.is_valid() else None)

good = TeamGoalSerializer(data={"month": "2099-02", "goal_amount": "100.00"})
print("'YYYY-MM' month accepted:", good.is_valid())

if technician:
    other_tenant_user = JobberUser.objects.exclude(tenant=tenant).filter(is_active=True).first()
    if other_tenant_user:
        cross_tenant = TechnicianGoalWriteSerializer(
            data={"user": other_tenant_user.id, "month": "2099-02", "goal_amount": "100.00"},
            context={"tenant_id": tenant.id},
        )
        print(
            "cross-tenant user_id rejected:",
            not cross_tenant.is_valid(),
            cross_tenant.errors if not cross_tenant.is_valid() else None,
        )
    else:
        print("No other tenant's JobberUser found locally -- cross-tenant check skipped (not a failure).")

print("\n=== 4) Serializer-level create()/update() -- the actual code a real POST runs ===")
month_str = SERIALIZER_TEST_MONTH.strftime("%Y-%m")

# First call: no existing row -> instance=None -> serializer.create().
existing = TeamGoal.fetch(tenant_id=tenant.id, month=SERIALIZER_TEST_MONTH)
s1 = TeamGoalSerializer(instance=existing, data={"month": month_str, "goal_amount": "70000.00"}, context={"tenant_id": tenant.id})
s1.is_valid(raise_exception=True)
saved1 = s1.save()
print("TeamGoalSerializer create (instance was None):", saved1.goal_amount, "-- row count:", TeamGoal.objects.filter(tenant=tenant, month=SERIALIZER_TEST_MONTH).count())

# Second call, same month: existing row found -> instance=<row> -> serializer.update(), not a 2nd insert.
existing = TeamGoal.fetch(tenant_id=tenant.id, month=SERIALIZER_TEST_MONTH)
s2 = TeamGoalSerializer(instance=existing, data={"month": month_str, "goal_amount": "80000.00"}, context={"tenant_id": tenant.id})
s2.is_valid(raise_exception=True)
saved2 = s2.save()
print(
    "TeamGoalSerializer update (instance was the existing row):",
    saved2.goal_amount, "(expected 80000.00) -- same row:", saved2.id == saved1.id,
    "-- row count still 1:", TeamGoal.objects.filter(tenant=tenant, month=SERIALIZER_TEST_MONTH).count() == 1,
)

if technician:
    existing_t = TechnicianGoal.fetch(tenant_id=tenant.id, user_id=technician.id, month=SERIALIZER_TEST_MONTH)
    t1 = TechnicianGoalWriteSerializer(
        instance=existing_t,
        data={"user": technician.id, "month": month_str, "goal_amount": "30000.00"},
        context={"tenant_id": tenant.id},
    )
    t1.is_valid(raise_exception=True)
    tsaved1 = t1.save()
    print("TechnicianGoalWriteSerializer create (instance was None):", tsaved1.goal_amount, "-- row count:", TechnicianGoal.objects.filter(tenant=tenant, user=technician, month=SERIALIZER_TEST_MONTH).count())

    existing_t = TechnicianGoal.fetch(tenant_id=tenant.id, user_id=technician.id, month=SERIALIZER_TEST_MONTH)
    t2 = TechnicianGoalWriteSerializer(
        instance=existing_t,
        data={"user": technician.id, "month": month_str, "goal_amount": "40000.00"},
        context={"tenant_id": tenant.id},
    )
    t2.is_valid(raise_exception=True)
    tsaved2 = t2.save()
    print(
        "TechnicianGoalWriteSerializer update (instance was the existing row):",
        tsaved2.goal_amount, "(expected 40000.00) -- same row:", tsaved2.id == tsaved1.id,
        "-- row count still 1:", TechnicianGoal.objects.filter(tenant=tenant, user=technician, month=SERIALIZER_TEST_MONTH).count() == 1,
    )

# Clean up everything this script created.
TeamGoal.objects.filter(tenant=tenant, month__in=[TEST_MONTH, SERIALIZER_TEST_MONTH]).delete()
if technician:
    TechnicianGoal.objects.filter(tenant=tenant, user=technician, month__in=[TEST_MONTH, SERIALIZER_TEST_MONTH]).delete()
print("\nCleanup done -- no rows left behind by this script.")

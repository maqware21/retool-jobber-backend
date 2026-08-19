"""
Annual Goals (Part A) verification. Run via
`python manage.py shell < verify_annual_goals.py`.

Same structure as verify_goals.py for the monthly tables -- tests the
model layer, the actual serializer create()/update() round trip (the
real code path a POST runs), and validation, against tenant_id=1. Uses a
throwaway year (2099) so it can never collide with a real annual goal,
and deletes every row it creates at the end.

Covers:
  1) TeamAnnualGoal.create() / .fetch() -- create, read back, upsert
     (update), and the (tenant, year) unique constraint blocking a dupe.
  2) TechnicianAnnualGoal.create() / .fetch() -- same, plus the
     roster-shape query (fetch(tenant_id=, year=) with no user_id ->
     queryset).
  3) TeamAnnualGoalSerializer / TechnicianAnnualGoalWriteSerializer --
     negative goal_amount rejected, 'YYYY' year accepted, 'YYYY-01'
     rejected.
  4) The ACTUAL code path a real POST runs -- Serializer(instance=
     existing or None, data=..., context=...) called twice against the
     same year: once with instance=None (create), once with instance=
     <the row just created> (update).
"""
from datetime import date
from decimal import Decimal

from apps.goals.models import TeamAnnualGoal, TechnicianAnnualGoal
from apps.goals.serializers.team_annual_goal import TeamAnnualGoalSerializer
from apps.goals.serializers.technician_annual_goal import TechnicianAnnualGoalWriteSerializer
from apps.jobber.models import JobberAccount, JobberUser

TEST_YEAR = date(2099, 1, 1)  # a year nobody has a real annual goal for

account = JobberAccount.objects.filter(is_active=True, tenant_id=1).first()
tenant = account.tenant
print("tenant_id:", tenant.id)

technician = JobberUser.objects.filter(tenant=tenant, is_active=True).first()
print("technician:", technician.name if technician else None, "id:", technician.id if technician else None)

# Clean slate: remove any leftover rows from a prior run of this script.
TeamAnnualGoal.objects.filter(tenant=tenant, year=TEST_YEAR).delete()
if technician:
    TechnicianAnnualGoal.objects.filter(tenant=tenant, user=technician, year=TEST_YEAR).delete()

print("\n=== 1) TeamAnnualGoal.create() / .fetch() / upsert / unique constraint ===")
created = TeamAnnualGoal.create({"tenant_id": tenant.id, "year": TEST_YEAR, "goal_amount": Decimal("600000.00")})
print("created:", created is not None, "goal_amount:", created.goal_amount if created else None)

fetched = TeamAnnualGoal.fetch(tenant_id=tenant.id, year=TEST_YEAR)
print("fetched back:", fetched is not None, "matches created:", fetched.id == created.id if fetched and created else False)

fetched.goal_amount = Decimal("720000.00")
fetched.save()
refetched = TeamAnnualGoal.fetch(tenant_id=tenant.id, year=TEST_YEAR)
print("after upsert, goal_amount:", refetched.goal_amount, "(expected 720000.00)")

dupe = TeamAnnualGoal.create({"tenant_id": tenant.id, "year": TEST_YEAR, "goal_amount": Decimal("1.00")})
print("duplicate raw create() blocked (returns None):", dupe is None)
print("row count for this tenant/year (should stay 1):", TeamAnnualGoal.objects.filter(tenant=tenant, year=TEST_YEAR).count())

if technician:
    print("\n=== 2) TechnicianAnnualGoal.create() / .fetch() / roster-shape query ===")
    created_t = TechnicianAnnualGoal.create({
        "tenant_id": tenant.id, "user_id": technician.id, "year": TEST_YEAR, "goal_amount": Decimal("240000.00"),
    })
    print("created:", created_t is not None)

    single = TechnicianAnnualGoal.fetch(tenant_id=tenant.id, user_id=technician.id, year=TEST_YEAR)
    print("fetch(tenant_id=, user_id=, year=) returns a single instance:", single is not None and single.id == created_t.id)

    roster_qs = TechnicianAnnualGoal.fetch(tenant_id=tenant.id, year=TEST_YEAR)
    print(
        "fetch(tenant_id=, year=) with no user_id returns a queryset, not .first():",
        hasattr(roster_qs, "count"),
        "-- count:", roster_qs.count(),
    )

print("\n=== 3) Serializer validation ===")
neg = TeamAnnualGoalSerializer(data={"year": "2100", "goal_amount": "-1.00"})
print("negative goal_amount rejected:", not neg.is_valid(), neg.errors if not neg.is_valid() else None)

bad_year = TeamAnnualGoalSerializer(data={"year": "2100-01", "goal_amount": "100.00"})
print("'YYYY-01' year rejected:", not bad_year.is_valid(), bad_year.errors if not bad_year.is_valid() else None)

good = TeamAnnualGoalSerializer(data={"year": "2100", "goal_amount": "100.00"})
print("'YYYY' year accepted:", good.is_valid())

print("\n=== 4) Serializer-level create()/update() -- the actual code a real POST runs ===")
SERIALIZER_TEST_YEAR = date(2101, 1, 1)
year_str = SERIALIZER_TEST_YEAR.strftime("%Y")

existing = TeamAnnualGoal.fetch(tenant_id=tenant.id, year=SERIALIZER_TEST_YEAR)
s1 = TeamAnnualGoalSerializer(instance=existing, data={"year": year_str, "goal_amount": "800000.00"}, context={"tenant_id": tenant.id})
s1.is_valid(raise_exception=True)
saved1 = s1.save()
print("TeamAnnualGoalSerializer create (instance was None):", saved1.goal_amount)

existing = TeamAnnualGoal.fetch(tenant_id=tenant.id, year=SERIALIZER_TEST_YEAR)
s2 = TeamAnnualGoalSerializer(instance=existing, data={"year": year_str, "goal_amount": "900000.00"}, context={"tenant_id": tenant.id})
s2.is_valid(raise_exception=True)
saved2 = s2.save()
print(
    "TeamAnnualGoalSerializer update (instance was the existing row):",
    saved2.goal_amount, "(expected 900000.00) -- same row:", saved2.id == saved1.id,
    "-- row count still 1:", TeamAnnualGoal.objects.filter(tenant=tenant, year=SERIALIZER_TEST_YEAR).count() == 1,
)

if technician:
    existing_t = TechnicianAnnualGoal.fetch(tenant_id=tenant.id, user_id=technician.id, year=SERIALIZER_TEST_YEAR)
    t1 = TechnicianAnnualGoalWriteSerializer(
        instance=existing_t,
        data={"user": technician.id, "year": year_str, "goal_amount": "300000.00"},
        context={"tenant_id": tenant.id},
    )
    t1.is_valid(raise_exception=True)
    tsaved1 = t1.save()
    print("TechnicianAnnualGoalWriteSerializer create (instance was None):", tsaved1.goal_amount)

    existing_t = TechnicianAnnualGoal.fetch(tenant_id=tenant.id, user_id=technician.id, year=SERIALIZER_TEST_YEAR)
    t2 = TechnicianAnnualGoalWriteSerializer(
        instance=existing_t,
        data={"user": technician.id, "year": year_str, "goal_amount": "400000.00"},
        context={"tenant_id": tenant.id},
    )
    t2.is_valid(raise_exception=True)
    tsaved2 = t2.save()
    print(
        "TechnicianAnnualGoalWriteSerializer update (instance was the existing row):",
        tsaved2.goal_amount, "(expected 400000.00) -- same row:", tsaved2.id == tsaved1.id,
        "-- row count still 1:", TechnicianAnnualGoal.objects.filter(tenant=tenant, user=technician, year=SERIALIZER_TEST_YEAR).count() == 1,
    )

# Clean up everything this script created.
TeamAnnualGoal.objects.filter(tenant=tenant, year__in=[TEST_YEAR, SERIALIZER_TEST_YEAR]).delete()
if technician:
    TechnicianAnnualGoal.objects.filter(tenant=tenant, user=technician, year__in=[TEST_YEAR, SERIALIZER_TEST_YEAR]).delete()
print("\nCleanup done -- no rows left behind by this script.")

from django.db import IntegrityError, models

from helpers.models import DateModel


class TeamGoal(DateModel):
    """
    One whole-team monthly revenue goal, entered directly by the customer
    -- this is OUR OWN data, NOT synced from Jobber. Plain CRUD; never
    wired into sync_tenant()/ensure_fresh().

    One row per (tenant, month) -- see the unique constraint below.
    """
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='team_goals',
    )
    month = models.DateField()  # always the 1st of the month
    goal_amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        db_table = 'team_goals'
        verbose_name = 'team goal'
        verbose_name_plural = 'team goals'
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'month'], name='unique_team_goal_tenant_month'),
        ]

    def __str__(self):
        return f"TeamGoal(tenant={self.tenant_id}, month={self.month})"

    @classmethod
    def create(cls, validated_data):
        try:
            return cls.objects.create(**validated_data)
        except IntegrityError:
            # The real exception a duplicate (tenant, month) raises --
            # anything else is a genuine bug and must propagate to the
            # view's own try/except -> validator_errors(), which logs it,
            # rather than being silently swallowed here.
            return None

    @classmethod
    def fetch(cls, tenant_id=None, month=None, is_active=True):
        """
        fetch(tenant_id=, month=) -> a single instance or None (enough to
        identify exactly one row). Any narrower call -> a queryset.
        """
        qs = cls.objects.all()
        if is_active is not None:
            qs = qs.filter(is_active=is_active)
        if tenant_id is not None:
            qs = qs.filter(tenant_id=tenant_id)
        if month is not None:
            qs = qs.filter(month=month)
            return qs.first()
        return qs


class TechnicianGoal(DateModel):
    """
    One technician's monthly revenue goal, entered directly by the
    customer -- OUR OWN data, NOT synced from Jobber. References the
    already-synced JobberUser roster; plain CRUD, never wired into
    sync_tenant()/ensure_fresh().

    One row per (tenant, user, month) -- see the unique constraint below.
    """
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='technician_goals',
    )
    user = models.ForeignKey(
        'jobber.JobberUser',
        on_delete=models.CASCADE,
        related_name='monthly_goals',
    )
    month = models.DateField()  # always the 1st of the month
    goal_amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        db_table = 'technician_goals'
        verbose_name = 'technician goal'
        verbose_name_plural = 'technician goals'
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'user', 'month'], name='unique_technician_goal_tenant_user_month'),
        ]

    def __str__(self):
        return f"TechnicianGoal(tenant={self.tenant_id}, user={self.user_id}, month={self.month})"

    @classmethod
    def create(cls, validated_data):
        try:
            return cls.objects.create(**validated_data)
        except IntegrityError:
            # The real exception a duplicate (tenant, user, month) raises
            # -- anything else is a genuine bug and must propagate to the
            # view's own try/except -> validator_errors(), which logs it,
            # rather than being silently swallowed here.
            return None

    @classmethod
    def fetch(cls, tenant_id=None, user_id=None, month=None, is_active=True):
        """
        fetch(tenant_id=, user_id=, month=) -> a single instance or None
        (enough to identify exactly one row, e.g. an upsert lookup). Any
        narrower call -- e.g. fetch(tenant_id=, month=) for the whole
        roster's goals in one month -- returns a queryset.
        """
        qs = cls.objects.all()
        if is_active is not None:
            qs = qs.filter(is_active=is_active)
        if tenant_id is not None:
            qs = qs.filter(tenant_id=tenant_id)
        if user_id is not None:
            qs = qs.filter(user_id=user_id)
        if month is not None:
            qs = qs.filter(month=month)
        if user_id is not None and month is not None:
            return qs.first()
        return qs


class TeamAnnualGoal(DateModel):
    """
    One whole-team ANNUAL revenue goal, entered directly by the customer
    -- OUR OWN data, NOT synced from Jobber, NOT derived from
    TeamGoal (monthly x 12 was explicitly rejected by TL -- this is a
    genuinely independent figure the customer sets separately). Plain
    CRUD; never wired into sync_tenant()/ensure_fresh().

    One row per (tenant, year) -- see the unique constraint below. Exact
    mirror of TeamGoal's shape, `month` swapped for `year`.
    """
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='team_annual_goals',
    )
    year = models.DateField()  # always Jan 1 of that year
    # Same DecimalField(12, 2) as the monthly shape -- confirmed
    # deliberately, not carried forward by default (2026-08-19): 12
    # max_digits/2 decimal_places allows up to $9,999,999,999.99, the
    # same money-field convention already used for Job.total/
    # Invoice.amount/TeamGoal.goal_amount/TechnicianGoal.goal_amount
    # project-wide. An annual figure is naturally larger than a monthly
    # one (roughly up to ~12x), but nowhere near this field's ceiling for
    # any realistic business at this project's scale -- no case for
    # widening it just because the period is longer.
    goal_amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        db_table = 'team_annual_goals'
        verbose_name = 'team annual goal'
        verbose_name_plural = 'team annual goals'
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'year'], name='unique_team_annual_goal_tenant_year'),
        ]

    def __str__(self):
        return f"TeamAnnualGoal(tenant={self.tenant_id}, year={self.year})"

    @classmethod
    def create(cls, validated_data):
        try:
            return cls.objects.create(**validated_data)
        except IntegrityError:
            # The real exception a duplicate (tenant, year) raises --
            # anything else is a genuine bug and must propagate to the
            # view's own try/except -> validator_errors(), which logs it.
            return None

    @classmethod
    def fetch(cls, tenant_id=None, year=None, is_active=True):
        """
        fetch(tenant_id=, year=) -> a single instance or None. Any
        narrower call -> a queryset. Exact mirror of TeamGoal.fetch().
        """
        qs = cls.objects.all()
        if is_active is not None:
            qs = qs.filter(is_active=is_active)
        if tenant_id is not None:
            qs = qs.filter(tenant_id=tenant_id)
        if year is not None:
            qs = qs.filter(year=year)
            return qs.first()
        return qs


class TechnicianAnnualGoal(DateModel):
    """
    One technician's ANNUAL revenue goal, entered directly by the
    customer -- OUR OWN data, NOT synced from Jobber, NOT derived from
    TechnicianGoal (monthly x 12 was explicitly rejected by TL).
    References the already-synced JobberUser roster; plain CRUD, never
    wired into sync_tenant()/ensure_fresh().

    One row per (tenant, user, year) -- see the unique constraint below.
    Exact mirror of TechnicianGoal's shape, `month` swapped for `year`.
    """
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='technician_annual_goals',
    )
    user = models.ForeignKey(
        'jobber.JobberUser',
        on_delete=models.CASCADE,
        related_name='annual_goals',
    )
    year = models.DateField()  # always Jan 1 of that year
    # Same DecimalField(12, 2) as the monthly shape -- see the identical,
    # deliberate note on TeamAnnualGoal.goal_amount above.
    goal_amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        db_table = 'technician_annual_goals'
        verbose_name = 'technician annual goal'
        verbose_name_plural = 'technician annual goals'
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'user', 'year'], name='unique_technician_annual_goal_tenant_user_year'),
        ]

    def __str__(self):
        return f"TechnicianAnnualGoal(tenant={self.tenant_id}, user={self.user_id}, year={self.year})"

    @classmethod
    def create(cls, validated_data):
        try:
            return cls.objects.create(**validated_data)
        except IntegrityError:
            # The real exception a duplicate (tenant, user, year) raises
            # -- anything else is a genuine bug and must propagate to the
            # view's own try/except -> validator_errors(), which logs it.
            return None

    @classmethod
    def fetch(cls, tenant_id=None, user_id=None, year=None, is_active=True):
        """
        fetch(tenant_id=, user_id=, year=) -> a single instance or None.
        Any narrower call -- e.g. fetch(tenant_id=, year=) for the whole
        roster's annual goals -- returns a queryset. Exact mirror of
        TechnicianGoal.fetch().
        """
        qs = cls.objects.all()
        if is_active is not None:
            qs = qs.filter(is_active=is_active)
        if tenant_id is not None:
            qs = qs.filter(tenant_id=tenant_id)
        if user_id is not None:
            qs = qs.filter(user_id=user_id)
        if year is not None:
            qs = qs.filter(year=year)
        if user_id is not None and year is not None:
            return qs.first()
        return qs

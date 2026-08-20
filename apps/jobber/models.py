from datetime import timedelta

from django.db import models
from django.utils import timezone

from helpers.constants import JOBBER_SYNC_STATUS
from helpers.models import DateModel

# Refresh a little before the real expiry so an in-flight request never races
# the token going stale.
TOKEN_EXPIRY_LEEWAY = timedelta(seconds=60)

# A RUNNING JobberSyncRun whose lock heartbeat (claimed_at, falling back to
# started_at) is older than this is treated as orphaned — the worker that
# claimed it almost certainly died mid-sync (a recycle is routine, not
# exotic), not one still legitimately in flight. See JobberSyncRun.is_stuck.
SYNC_RUN_STALE_AFTER = timedelta(minutes=5)


class JobberAccount(DateModel):
    """
    The OAuth link between one VoltPro Tenant and one connected Jobber account.

    Holds the access/refresh tokens issued by Jobber's OAuth 2.0 flow. One row
    per tenant — a tenant re-connecting overwrites the same row (see
    ``store_tokens``). Tokens are never exposed through the API.
    """

    tenant = models.OneToOneField(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='jobber_account',
    )

    # Jobber's own account/company identifier (filled once we query the API).
    jobber_account_id = models.CharField(max_length=255, null=True, blank=True)

    access_token = models.TextField()
    refresh_token = models.TextField()
    token_type = models.CharField(max_length=40, default='bearer')
    # Space-separated scopes actually granted by the Jobber admin.
    scope = models.TextField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'jobber_accounts'
        verbose_name = 'jobber account'
        verbose_name_plural = 'jobber accounts'

    def __str__(self):
        return f"JobberAccount(tenant={self.tenant_id}, account={self.jobber_account_id})"

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def is_expired(self):
        """True when the access token is missing an expiry or is within the leeway window."""
        if not self.expires_at:
            return True
        return timezone.now() >= (self.expires_at - TOKEN_EXPIRY_LEEWAY)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def store_tokens(self, token_data):
        """
        Persist a token payload from Jobber's token endpoint.

        Handles both the initial code exchange and refreshes. Refresh Token
        Rotation is mandatory for our app, so Jobber always returns a new
        refresh token on every refresh. The ``if`` guard is defensive — it
        protects against the (invalid) state of rotation being temporarily
        disabled, not normal behaviour.
        """
        self.access_token = token_data['access_token']
        if token_data.get('refresh_token'):
            self.refresh_token = token_data['refresh_token']
        self.token_type = token_data.get('token_type', 'bearer')
        if token_data.get('scope'):
            self.scope = token_data['scope']

        expires_in = token_data.get('expires_in')
        if expires_in:
            self.expires_at = timezone.now() + timedelta(seconds=int(expires_in))

        self.save()
        return self


class JobberClient(DateModel):
    """
    Local mirror of one Jobber Client, populated and refreshed by the sync
    engine (Phase 2). A full sync pass no longer seeing a previously-synced
    jobber_id sets is_active=False rather than deleting the row.
    """

    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='jobber_clients',
    )
    jobber_id = models.CharField(max_length=255, db_index=True)
    name = models.CharField(max_length=255)
    # Comma-joined tag labels — reuses _client_tags_display()'s exact
    # derivation verbatim. Free-text, not a fixed taxonomy.
    tags_display = models.CharField(max_length=255, null=True, blank=True)
    synced_at = models.DateTimeField()

    class Meta:
        db_table = 'jobber_clients'
        verbose_name = 'jobber client'
        verbose_name_plural = 'jobber clients'
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'jobber_id'], name='unique_jobber_client_tenant_jobber_id'),
        ]

    def __str__(self):
        return f"JobberClient(tenant={self.tenant_id}, jobber_id={self.jobber_id})"


class JobberUser(DateModel):
    """
    Local mirror of one Jobber User/Technician, populated and refreshed by
    the sync engine.
    """

    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='jobber_users',
    )
    jobber_id = models.CharField(max_length=255, db_index=True)
    name = models.CharField(max_length=255)
    # User.phone.friendly — confirmed real in Jobber's schema (2026-08-19),
    # not previously synced. Nullable: a real user can have no phone on
    # file in Jobber at all, not just "not yet synced."
    phone = models.CharField(max_length=50, null=True, blank=True)
    # From User.customFields (a GraphQL UNION -- see _USERS_QUERY's own
    # comment), matched by label after trimming whitespace (confirmed
    # live, 2026-08-20: this account's real "Expertise" label has a
    # trailing space). Both nullable -- a tenant without these exact
    # Team custom fields configured (wrong label, wrong type, or simply
    # never set up) gets a clean null, never a crash; confirmed real for
    # THIS account only, not guaranteed for a future tenant.
    expertise = models.CharField(max_length=255, null=True, blank=True)
    # FloatField, not Decimal -- deliberately NOT the DecimalField(12, 2)
    # money-field convention used elsewhere in this project: years of
    # experience isn't a financial figure needing exact decimal
    # arithmetic, and Jobber's own source field (CustomFieldNumeric.
    # valueNumeric) is itself a Float, confirmed live ("4.0 Years", not
    # necessarily always a whole number).
    experience_years = models.FloatField(null=True, blank=True)
    # Already fetched live today via _USERS_QUERY but not exposed anywhere.
    # Kept here for the still-open "filter admins/owners from the roster?"
    # question (see PROJECT_CONTEXT.md) — not resolved by this model.
    is_account_admin = models.BooleanField(default=False)
    is_account_owner = models.BooleanField(default=False)
    synced_at = models.DateTimeField()

    class Meta:
        db_table = 'jobber_users'
        verbose_name = 'jobber user'
        verbose_name_plural = 'jobber users'
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'jobber_id'], name='unique_jobber_user_tenant_jobber_id'),
        ]

    def __str__(self):
        return f"JobberUser(tenant={self.tenant_id}, jobber_id={self.jobber_id})"


class JobberJob(DateModel):
    """
    Local mirror of one Jobber Job, populated and refreshed by the sync
    engine.
    """

    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='jobber_jobs',
    )
    client = models.ForeignKey(
        JobberClient,
        on_delete=models.CASCADE,
        related_name='jobs',
    )
    jobber_id = models.CharField(max_length=255, db_index=True)
    # Real int from Jobber — the same field the sort-bug fix on the
    # live-proxy jobs.py endpoint already established (id is a display
    # string like "JOB-10" and sorts wrong past single digits).
    job_number = models.IntegerField(null=True, blank=True)
    title = models.CharField(max_length=255)
    # Reuses the "instructions or title" fallback already proven live.
    description = models.TextField(null=True, blank=True)
    job_status = models.CharField(max_length=50)
    # Reuses _humanize_status() verbatim.
    status_display = models.CharField(max_length=50)
    # Reuses _job_service_type() verbatim — free-text, not a fixed taxonomy,
    # same caveat already documented for the live endpoints.
    service_type = models.CharField(max_length=255, null=True, blank=True)
    # DecimalField, not float — a stored financial figure, unlike the
    # live-proxy code's display-only JSON float pass-through.
    total = models.DecimalField(max_digits=12, decimal_places=2)
    jobber_created_at = models.DateTimeField(null=True, blank=True)
    start_at = models.DateTimeField(null=True, blank=True)
    # Reuses _format_address() verbatim.
    address = models.CharField(max_length=500, null=True, blank=True)
    # From Jobber's jobCosting { labourDuration labourCost } — added now
    # rather than in a later migration, for the Electricians "Avg Job
    # Duration" card. labourDuration is a Seconds int scalar; labour_cost
    # gets the same float-to-Decimal treatment as total.
    labour_duration_seconds = models.IntegerField(null=True, blank=True)
    labour_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    # From Jobber's own completedAt field (ISO8601DateTime, nullable in the
    # schema). Confirmed via live cross-check (2026-08-16, 3 real archived
    # jobs): this tracks when the INVOICING loop closes (invoice
    # created/sent), NOT when the physical work was done — completedAt
    # landed 10-23 seconds before each job's own invoice was issued, and
    # about a full day after job.start_at. That's the correct field for
    # "Jobs Completed" regardless (archived = completed, confirmed from 3
    # separate angles — direct testing, Jobber's own docs, Jobber's support
    # bot — see PROJECT_CONTEXT.md), it just doesn't mean "finished on-site
    # that day." Nullable here because Jobber's own schema nulls it, and
    # specifically because an archived job with NO linked invoice at all
    # (skip-invoicing config, or a cancelled job — a real, valid case, not
    # an error) may not populate it — untested in this project's real data
    # as of 2026-08-16 (no such job existed in the test account at the
    # time), so treated defensively rather than assumed safe. See
    # sync.py's sync_jobs() and electricians_summary.py for how a null
    # value here is handled (excluded, not defaulted to another date).
    completed_at = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField()

    class Meta:
        db_table = 'jobber_jobs'
        verbose_name = 'jobber job'
        verbose_name_plural = 'jobber jobs'
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'jobber_id'], name='unique_jobber_job_tenant_jobber_id'),
        ]

    def __str__(self):
        return f"JobberJob(tenant={self.tenant_id}, jobber_id={self.jobber_id})"

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def labour_duration_hours(self):
        if self.labour_duration_seconds is None:
            return None
        return round(self.labour_duration_seconds / 3600, 2)


class JobberVisit(DateModel):
    """
    Local mirror of one Jobber Visit. A Job's assignee is a Visit's
    assignee, not the Job's own field — confirmed by the live-proxy's own
    _first_assignee, which reads job.visits[0].assignedUsers. Storing this
    as a real table (instead of a denormalized name string on Job) is the
    actual upgrade this phase enables.
    """

    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='jobber_visits',
    )
    job = models.ForeignKey(
        JobberJob,
        on_delete=models.CASCADE,
        related_name='visits',
    )
    # Nullable — "Unassigned" becomes null instead of a string.
    #
    # ADDITIVE-ONLY, deliberately not replaced (2026-08-17): this field and
    # assigned_user_name below are consumed by LIVE, SHIPPED production
    # code — JobberJobsView.get() (the real /v1/jobber/jobs/ endpoint,
    # already cut over to local reads) reads assigned_user_name via
    # jobs.py's _local_first_assignee() for the real "Assigned To" column
    # customers see today. Changing this field's meaning or removing it
    # would be a regression risk to already-shipped behavior, not just an
    # internal refactor — so it stays completely untouched, same values,
    # same "first assignee, 'Unassigned' fallback" semantics, forever
    # (or until that live consumer is deliberately migrated off it).
    # assigned_users (plural, below) is the new, separate, additive field
    # for anything that needs EVERY assignee, not just the first.
    assigned_user = models.ForeignKey(
        JobberUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='visits',
    )
    # New (2026-08-17), for Top Earner's per-technician revenue split —
    # Jobber's own schema has always supported multiple assignees per visit
    # (Visit.assignedUsers is a UserConnection, confirmed against the
    # schema, not assumed) and assignedUsers(first: 5) is already being
    # fetched by the sync-only query for every visit; today's sync just
    # discards everything past the first entry. This field captures all of
    # them instead, at zero new Jobber query cost. related_name is
    # 'assigned_visits', not 'visits' — assigned_user above already owns
    # that reverse accessor name on JobberUser, and the two need to coexist
    # without colliding.
    assigned_users = models.ManyToManyField(
        JobberUser,
        related_name='assigned_visits',
        blank=True,
    )
    jobber_id = models.CharField(max_length=255, db_index=True)
    synced_at = models.DateTimeField()

    class Meta:
        db_table = 'jobber_visits'
        verbose_name = 'jobber visit'
        verbose_name_plural = 'jobber visits'
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'jobber_id'], name='unique_jobber_visit_tenant_jobber_id'),
        ]

    def __str__(self):
        return f"JobberVisit(tenant={self.tenant_id}, jobber_id={self.jobber_id})"

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def assigned_user_name(self):
        return self.assigned_user.name if self.assigned_user else 'Unassigned'


class JobberInvoice(DateModel):
    """
    Local mirror of one Jobber Invoice, populated and refreshed by the sync
    engine.
    """

    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='jobber_invoices',
    )
    client = models.ForeignKey(
        JobberClient,
        on_delete=models.CASCADE,
        related_name='invoices',
    )
    # An invoice can reference zero or multiple jobs — reuses the exact
    # insight _format_job_refs() already proved live. Not a single FK.
    jobs = models.ManyToManyField(JobberJob, related_name='invoices', blank=True)
    jobber_id = models.CharField(max_length=255, db_index=True)
    # Jobber returns this as a STRING, unlike Job.jobNumber — via _safe_int().
    invoice_number = models.IntegerField(null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    # Genuinely distinct from amount — a partially paid invoice has
    # balance < amount.
    balance = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    # Raw; a genuinely null issued_date on a real draft invoice is already
    # confirmed against live data, not hypothetical.
    issued_date = models.DateTimeField(null=True, blank=True)
    due_date = models.DateTimeField(null=True, blank=True)
    invoice_status = models.CharField(max_length=50)
    # Reuses _STATUS_DISPLAY_MAP/_status_display() verbatim, including the
    # already-resolved "Draft excluded from billed totals" decision.
    status_display = models.CharField(max_length=50)
    synced_at = models.DateTimeField()

    class Meta:
        db_table = 'jobber_invoices'
        verbose_name = 'jobber invoice'
        verbose_name_plural = 'jobber invoices'
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'jobber_id'], name='unique_jobber_invoice_tenant_jobber_id'),
        ]

    def __str__(self):
        return f"JobberInvoice(tenant={self.tenant_id}, jobber_id={self.jobber_id})"


class JobberTimeSheetEntry(DateModel):
    """
    Local mirror of one Jobber TimeSheetEntry, populated and refreshed by
    the sync engine. Motivated by a confirmed finding (2026-08-16):
    Job.jobCosting.labourDuration does NOT reflect real logged time — 6 of
    13 real archived jobs have genuine TimeSheetEntry records (real
    technicians, real non-zero finalDuration) while jobCosting reports 0
    for all of them. "Avg Job Duration" needs this real entity, not a
    derived jobCosting field.

    Like JobberVisit, there is no viable standalone root-level query for
    this — Query.timeSheetEntries exists but its own description is "All
    timesheet entries for users on a given day" and its filter type
    (TimeSheetEntriesFilterAttributes) has no job filter field at all
    (confirmed against the schema, not assumed). The only path is
    Job.timeSheetEntries(first, after), pulled per-job from the same job
    nodes the sync already has in memory — identical situation to Visits.

    Known real case, NOT yet handled by this model/sync step (Part B, not
    built here): the SAME (job, user) pair can have multiple entries whose
    time ranges genuinely overlap (confirmed real, not hypothetical — a
    technician's own stop/restart mistake produced two overlapping entries
    for one job in this project's real test data). Storing every raw entry
    here, unmerged, is deliberate — this table is meant to hold Jobber's
    real entries as they are; any overlap-merging or duration aggregation
    happens downstream, over these rows, not by editing/dropping rows here.
    """

    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='jobber_timesheet_entries',
    )
    job = models.ForeignKey(
        JobberJob,
        on_delete=models.CASCADE,
        related_name='timesheet_entries',
    )
    # Nullable — same reasoning as JobberVisit.assigned_user: an entry
    # whose user record isn't locally synced yet (or was later removed)
    # shouldn't orphan the entry itself.
    user = models.ForeignKey(
        JobberUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='timesheet_entries',
    )
    jobber_id = models.CharField(max_length=255, db_index=True)
    # From finalDuration: Seconds! (confirmed non-null in the schema) — the
    # "stopped" duration for a completed entry, not the live in-progress
    # `duration` field, which is the wrong one for a historical record.
    final_duration_seconds = models.IntegerField()
    # startAt is technically non-null in the schema (ISO8601DateTime!), but
    # stored nullable here anyway — every other synced datetime in this
    # project is nullable defensively, and this is the one field genuinely
    # meant to distinguish "no data yet" from a real Jobber value.
    started_at = models.DateTimeField(null=True, blank=True)
    # endAt IS genuinely nullable in the schema — an entry with a currently
    # running timer has no endAt yet.
    ended_at = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField()

    class Meta:
        db_table = 'jobber_timesheet_entries'
        verbose_name = 'jobber timesheet entry'
        verbose_name_plural = 'jobber timesheet entries'
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'jobber_id'], name='unique_jobber_timesheet_entry_tenant_jobber_id'),
        ]

    def __str__(self):
        return f"JobberTimeSheetEntry(tenant={self.tenant_id}, jobber_id={self.jobber_id})"


class JobberSyncRun(models.Model):
    """
    One row per sync attempt for a tenant (not one mutable row per tenant —
    FR-308 needs history). Does double duty as both the FR-308 audit trail
    and the concurrency lock: claiming a row IS starting a sync run, via
    select_for_update() in the sync engine (not built in this step).

    Deliberately does NOT inherit DateModel — its is_active toggle doesn't
    mean anything for a historical run record; a past run isn't "inactive,"
    it already finished. One conscious, stated exception to this app's
    base-class convention, not a silent departure from it.
    """

    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='jobber_sync_runs',
    )
    status = models.CharField(max_length=10, choices=JOBBER_SYNC_STATUS, default=JOBBER_SYNC_STATUS[0][0])
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    # The lock heartbeat the concurrency guard uses to tell a genuinely
    # in-flight sync apart from one whose worker died mid-run.
    claimed_at = models.DateTimeField(null=True, blank=True)
    # Same "log the real exception message" convention JobberAPIError
    # already establishes.
    error_message = models.TextField(null=True, blank=True)
    # Per-entity breakdown of what this run actually synced, so "which
    # entity types are stale right now" is answerable without re-deriving
    # it from the entity tables themselves.
    clients_synced = models.IntegerField(default=0)
    users_synced = models.IntegerField(default=0)
    jobs_synced = models.IntegerField(default=0)
    visits_synced = models.IntegerField(default=0)
    invoices_synced = models.IntegerField(default=0)

    class Meta:
        db_table = 'jobber_sync_runs'
        verbose_name = 'jobber sync run'
        verbose_name_plural = 'jobber sync runs'
        ordering = ['-started_at']

    def __str__(self):
        return f"JobberSyncRun(tenant={self.tenant_id}, status={self.status}, started_at={self.started_at})"

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def duration_seconds(self):
        if not self.finished_at:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def is_stuck(self):
        """True when this run is still RUNNING but its lock heartbeat is stale — the worker that claimed it almost certainly died mid-sync."""
        if self.status != JOBBER_SYNC_STATUS[0][0]:
            return False
        heartbeat = self.claimed_at or self.started_at
        if not heartbeat:
            return True
        return timezone.now() >= (heartbeat + SYNC_RUN_STALE_AFTER)

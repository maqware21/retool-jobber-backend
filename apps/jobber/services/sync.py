"""
Jobber local-table sync engine (Phase 2 local-sync design doc, §2-§4).

sync_tenant() is the single entry point: runs one sync attempt for one
tenant's JobberAccount, in dependency order (Clients + Users, then Jobs,
then Visits, then Invoices), inside a bounded wall-clock ceiling, recording
one JobberSyncRun row per attempt and using that same row as the
cross-process concurrency lock (select_for_update()).

ensure_fresh(tenant, entities, require_complete) is the stale-request
trigger built on top of sync_tenant() — it's what a view calls before
reading local tables. Not built here (a later step): actually wiring any
view to call it and read local data instead of Jobber live — that's a
separate, explicit cutover step once the local-read paths built alongside
the live ones have been compared and approved.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.jobber.models import (
    JobberAccount,
    JobberClient,
    JobberInvoice,
    JobberJob,
    JobberSyncRun,
    JobberTimeSheetEntry,
    JobberUser,
    JobberVisit,
)
from apps.jobber.services import client
from helpers.constants import JOBBER_SYNC_STATUS

logger = logging.getLogger(__name__)

# Whole-sync wall-clock ceiling, per the design doc's §3/FR-307 section — a
# proposed default, not yet measured against real data volume beyond the one
# connected test tenant. Checked before starting each new page fetch inside
# fetch_all_pages_bounded(), not mid-page.
SYNC_WALL_CLOCK_CEILING = timedelta(seconds=25)

ALL_ENTITIES = ('clients', 'users', 'jobs', 'visits', 'invoices', 'timesheet_entries')

# ensure_fresh()'s staleness threshold — the design doc's proposed default
# (20 minutes, inside the 15-30 min window already flagged elsewhere in this
# codebase), not yet measured against real usage.
STALENESS_THRESHOLD = timedelta(minutes=20)

ENTITY_MODELS = {
    'clients': JobberClient,
    'users': JobberUser,
    'jobs': JobberJob,
    'visits': JobberVisit,
    'invoices': JobberInvoice,
    'timesheet_entries': JobberTimeSheetEntry,
}


def _to_decimal(value):
    """
    Convert a raw Jobber JSON number to Decimal via str() first — never
    Decimal(value) directly on a float. Decimal(19.99) bakes that float's
    own binary representation error into the result (Decimal('19.9899999999
    999948...')); Decimal(str(19.99)) does not. This is precisely the
    floating-point risk the design doc's total/amount/balance
    float-to-Decimal reasoning is about — declaring the column DecimalField
    doesn't prevent it by itself, this conversion is what does.
    """
    if value is None:
        return None
    return Decimal(str(value))


def _to_datetime(value):
    """
    Jobber returns ISO8601 datetime strings. The live-proxy views pass these
    straight through untouched (fine — they just relay JSON to the
    frontend), but a DateTimeField column needs a real datetime object, not
    a raw string, to store correctly. parse_datetime() handles the trailing
    "Z" Jobber uses for UTC.
    """
    if not value:
        return None
    return parse_datetime(value)


def _claim_run(tenant):
    """
    select_for_update() the tenant's most recent JobberSyncRun row inside a
    short transaction — claiming a row IS starting a sync run (design doc's
    concurrency guard). The actual Jobber calls happen OUTSIDE this
    transaction; this only decides whether to proceed, then commits
    immediately.

    Returns the freshly-created RUNNING row this caller should update when
    done, or None if another process already holds a non-stale lock (that
    caller should proceed against whatever's locally there now — see the
    design doc's concurrency-guard section on this being an accepted trade,
    not an oversight).
    """
    with transaction.atomic():
        latest = (
            JobberSyncRun.objects.select_for_update()
            .filter(tenant=tenant)
            .order_by('-started_at')
            .first()
        )
        if latest is not None and latest.status == JOBBER_SYNC_STATUS[0][0]:
            if not latest.is_stuck:
                return None
            # The process that claimed this row almost certainly died
            # mid-sync (a worker recycle is routine, not exotic) — reclaim
            # by closing out the orphaned row as FAILED and starting a new
            # attempt. JobberSyncRun is one row per attempt, not one
            # mutable row per tenant, so the orphaned row stays as an
            # honest historical record rather than being overwritten.
            latest.status = JOBBER_SYNC_STATUS[3][0]
            latest.finished_at = timezone.now()
            latest.error_message = (
                'Reclaimed: RUNNING lock heartbeat went stale — the worker that claimed it likely died mid-sync.'
            )
            latest.save(update_fields=['status', 'finished_at', 'error_message'])

        return JobberSyncRun.objects.create(
            tenant=tenant,
            status=JOBBER_SYNC_STATUS[0][0],
            claimed_at=timezone.now(),
        )


def sync_clients(account, tenant, deadline):
    """Pull every Client, upsert, deactivate vanished ones (if this pull was complete)."""
    # Imported here, not at module level: accounts.py now imports
    # ensure_fresh from this module (Step 3), and this module imports
    # _client_tags_display from accounts.py (Step 2) — a genuine circular
    # import between the two. Deferring to call time breaks the cycle
    # without giving up the "import and reuse, don't rewrite" reuse this
    # was written for; by the time this function actually runs, both
    # modules have finished loading.
    from apps.jobber.api.accounts import _client_tags_display

    nodes, complete = client.fetch_all_pages_bounded(client.fetch_clients, account, 'fetch_clients', deadline)
    seen_ids = set()
    count = 0
    for node in nodes:
        jobber_id = node.get('id')
        if not jobber_id:
            continue
        seen_ids.add(jobber_id)
        JobberClient.objects.update_or_create(
            tenant=tenant,
            jobber_id=jobber_id,
            defaults={
                'name': node.get('name') or '',
                'tags_display': _client_tags_display(node),
                'synced_at': timezone.now(),
                'is_active': True,
            },
        )
        count += 1

    if complete:
        JobberClient.objects.filter(tenant=tenant, is_active=True).exclude(jobber_id__in=seen_ids).update(is_active=False)

    return {'count': count, 'complete': complete}


def _extract_custom_field(custom_fields, label, value_key):
    """
    custom_fields: the raw list from GetUsers' customFields selection --
    each item is whatever our query's inline fragments matched for that
    item's real type (a 'label' plus one value key), or an item with no
    usable keys at all if its real type didn't match any fragment we
    spread (see the comment on _USERS_QUERY).

    label is matched via .strip() equality, NOT an exact match --
    confirmed live (verify_user_custom_fields.py, 2026-08-20) that this
    real account's "Expertise" field has a trailing space in its actual
    label ("Expertise "). Hardcoding that exact string would only work by
    accident for this one account and break the moment anyone re-types
    the field name without the trailing space.

    Returns None if genuinely absent -- a tenant without this exact
    custom field configured at all (wrong label, wrong type, or simply
    never set up) gets a clean null here, never a crash.
    """
    for field in custom_fields or []:
        if (field.get('label') or '').strip() == label:
            return field.get(value_key)
    return None


def sync_users(account, tenant, deadline):
    """Pull every User, upsert, deactivate vanished ones (if this pull was complete)."""
    nodes, complete = client.fetch_all_pages_bounded(client.fetch_users, account, 'fetch_users', deadline)
    seen_ids = set()
    count = 0
    for node in nodes:
        jobber_id = node.get('id')
        if not jobber_id:
            continue
        seen_ids.add(jobber_id)
        custom_fields = node.get('customFields') or []
        JobberUser.objects.update_or_create(
            tenant=tenant,
            jobber_id=jobber_id,
            defaults={
                'name': (node.get('name') or {}).get('full') or '',
                'phone': (node.get('phone') or {}).get('friendly') or None,
                'expertise': _extract_custom_field(custom_fields, 'Expertise', 'valueText'),
                'experience_years': _extract_custom_field(custom_fields, 'Experience', 'valueNumeric'),
                'is_account_admin': bool(node.get('isAccountAdmin')),
                'is_account_owner': bool(node.get('isAccountOwner')),
                'synced_at': timezone.now(),
                'is_active': True,
            },
        )
        count += 1

    if complete:
        JobberUser.objects.filter(tenant=tenant, is_active=True).exclude(jobber_id__in=seen_ids).update(is_active=False)

    return {'count': count, 'complete': complete}


def sync_jobs(account, tenant, deadline, clients_complete):
    """
    Pull every Job (via the sync-only query, which also carries jobCosting
    and each visit's own id — see client.py), upsert, deactivate vanished
    ones — but ONLY if this pull was itself complete AND Clients' pull this
    run was also complete.

    A job whose client has no local JobberClient row yet (client sync was
    itself PARTIAL earlier in this same run, or genuinely hasn't synced yet)
    is skipped, logged, and picked up on a future sync once its client
    exists — not a crash, but also not reflected in seen_ids. If Clients
    came back PARTIAL this run, some real, still-active jobs may have been
    skipped for exactly this reason even though the JOBS pull itself
    reached hasNextPage: false — running the deactivation sweep in that
    case would incorrectly deactivate them. Same bug class as the original
    ceiling-cutoff fix, different trigger (a dependency's incompleteness,
    not this entity's own). Fix: this function's own pagination-completeness
    is ANDed with ``clients_complete`` before either gating the sweep or
    being reported back as this entity's overall 'complete' — so a
    downstream caller (sync_invoices(), sync_visits()) that depends on Jobs
    being trustworthy inherits the correct, dependency-aware signal without
    having to re-derive it.
    """
    # Deferred import — same circular-import reason as sync_clients()'s
    # _client_tags_display import above, jobs.py side of the cycle this time.
    from apps.jobber.api.jobs import _format_address, _humanize_status, _job_service_type

    nodes, own_complete = client.fetch_all_pages_bounded(
        client.fetch_jobs_for_sync, account, 'fetch_jobs_for_sync', deadline,
    )
    seen_ids = set()
    count = 0
    for node in nodes:
        jobber_id = node.get('id')
        if not jobber_id:
            continue

        client_id = (node.get('client') or {}).get('id')
        job_client = JobberClient.objects.filter(tenant=tenant, jobber_id=client_id).first() if client_id else None
        if job_client is None:
            logger.warning(
                "sync_jobs: skipping job=%s for tenant=%s — its client=%s has no local JobberClient row yet",
                jobber_id, tenant.id, client_id,
            )
            continue

        raw_status = node.get('jobStatus') or ''
        costing = node.get('jobCosting') or {}
        seen_ids.add(jobber_id)
        JobberJob.objects.update_or_create(
            tenant=tenant,
            jobber_id=jobber_id,
            defaults={
                'client': job_client,
                'job_number': node.get('jobNumber'),
                'title': node.get('title') or '',
                'description': node.get('instructions') or node.get('title') or '',
                'job_status': raw_status,
                'status_display': _humanize_status(raw_status),
                'service_type': _job_service_type(node),
                'total': _to_decimal(node.get('total')) or Decimal('0'),
                'jobber_created_at': _to_datetime(node.get('createdAt')),
                'start_at': _to_datetime(node.get('startAt')),
                'completed_at': _to_datetime(node.get('completedAt')),
                'address': _format_address(node.get('property')),
                'labour_duration_seconds': costing.get('labourDuration'),
                'labour_cost': _to_decimal(costing.get('labourCost')),
                'synced_at': timezone.now(),
                'is_active': True,
            },
        )
        count += 1

    complete = own_complete and clients_complete
    if complete:
        JobberJob.objects.filter(tenant=tenant, is_active=True).exclude(jobber_id__in=seen_ids).update(is_active=False)

    return {'count': count, 'complete': complete, 'nodes': nodes}


def sync_visits(account, tenant, job_nodes, complete):
    """
    Visits are nested inside each Job node (job.visits), not pulled via
    their own top-level Jobber query. Unlike Clients/Users/Jobs/Invoices,
    there's no independently-confirmed root `visits` connection in Jobber's
    schema for this codebase to build against — every other query in
    client.py was written against a field shape already confirmed live
    (either from Jobber's docs or a real account response) before shipping;
    a standalone `visits(first, after)` query would be a guess this project
    has repeatedly avoided making (see e.g. the Tag.name-vs-label
    correction). job.visits[0..n].assignedUsers, by contrast, has been
    proven working in production since the visits(first:1)->visits(first:10)
    widening. So this derives Visit rows from the SAME job_nodes sync_jobs()
    already pulled this run (via the sync-only jobs query, which asks for
    each visit's own id alongside assignedUsers) — zero new schema risk, and
    it avoids a second, wasteful full pull of every job just to re-read its
    visits.

    If Jobber's schema does have a standalone `visits` query and a more
    direct pull is preferred later, this can be revisited — flagging that
    as a deliberate choice made under uncertainty, not an oversight.

    Only the first assignedUser per visit is stored on assigned_user
    (single nullable FK, per the approved design, matching the live-proxy's
    own existing "first assignee" convention — _first_assignee). This
    field and its behavior are UNCHANGED here — still consumed by live,
    shipped production code (JobberJobsView.get()'s "Assigned To" column
    via jobs.py's _local_first_assignee()/assigned_user_name).

    NEW (2026-08-17): assigned_users (M2M) additionally stores EVERY
    assignedUser on the visit, not just the first — for Top Earner's
    per-technician revenue split, which needs to know every assignee, not
    a single "first" one. Populated from the SAME assignedUsers(first: 5)
    data already fetched for assigned_user above — no new Jobber query
    cost, no new schema risk. This is the fix for the gap this function's
    own docstring used to flag here (a visit with more than one assignee
    only having its first one stored locally) — additive, not a
    replacement of the existing behavior.

    `complete` is sync_jobs()'s own completeness flag for this run, passed
    through unchanged — Visit data can't be any more complete than the Job
    data it was extracted from. That flag is already dependency-aware (it's
    False whenever Clients came back PARTIAL this run too, not just when
    the Jobs pull itself was cut short — see sync_jobs()), so Visits
    correctly inherits the same protection without needing its own copy of
    that logic.
    """
    seen_ids = set()
    count = 0
    for job_node in job_nodes:
        job_id = job_node.get('id')
        if not job_id:
            continue
        job = JobberJob.objects.filter(tenant=tenant, jobber_id=job_id).first()
        if job is None:
            # The job itself was skipped in sync_jobs() (e.g. missing local
            # client) — its visits can't be linked to anything, skip too.
            continue

        for visit_node in (job_node.get('visits') or {}).get('nodes') or []:
            visit_id = visit_node.get('id')
            if not visit_id:
                continue

            assigned = (visit_node.get('assignedUsers') or {}).get('nodes') or []
            assigned_user = None
            if assigned:
                assigned_user_id = assigned[0].get('id')
                if assigned_user_id:
                    assigned_user = JobberUser.objects.filter(tenant=tenant, jobber_id=assigned_user_id).first()

            seen_ids.add(visit_id)
            visit, _created = JobberVisit.objects.update_or_create(
                tenant=tenant,
                jobber_id=visit_id,
                defaults={
                    'job': job,
                    'assigned_user': assigned_user,
                    'synced_at': timezone.now(),
                    'is_active': True,
                },
            )

            # New: populate the additive assigned_users M2M with EVERY
            # assignee, reusing the exact same `assigned` list resolved
            # above — not a second Jobber fetch. .set() requires a saved
            # instance, so this happens after update_or_create() returns,
            # not inside its `defaults` (Django's update_or_create doesn't
            # support M2M fields there). Any assignee not yet locally
            # synced as a JobberUser is silently skipped, same "skip if
            # not synced yet" reasoning already applied to assigned_user
            # above — picked up on a future sync once that user exists.
            all_assigned_users = []
            for assignee_node in assigned:
                assignee_id = assignee_node.get('id')
                if not assignee_id:
                    continue
                assignee = JobberUser.objects.filter(tenant=tenant, jobber_id=assignee_id).first()
                if assignee is not None:
                    all_assigned_users.append(assignee)
            visit.assigned_users.set(all_assigned_users)

            count += 1

    if complete:
        JobberVisit.objects.filter(tenant=tenant, is_active=True).exclude(jobber_id__in=seen_ids).update(is_active=False)

    return {'count': count, 'complete': complete}


def sync_timesheet_entries(account, tenant, job_nodes, complete):
    """
    TimeSheetEntries are nested inside each Job node (job.timeSheetEntries),
    not pulled via a standalone top-level query — same situation as Visits,
    confirmed against the schema rather than assumed: Query.timeSheetEntries
    does exist, but its own description is "All timesheet entries for users
    on a given day" and its filter type (TimeSheetEntriesFilterAttributes)
    has no job filter field at all — there is no way to ask the root query
    "every entry for this job." So this derives JobberTimeSheetEntry rows
    from the SAME job_nodes sync_jobs() already pulled this run (via the
    sync-only jobs query, which asks for each entry's
    id/startAt/endAt/finalDuration/user.id) — zero new schema risk, no
    second per-job round-trip.

    Stores every raw entry AS-IS, unmerged — even when the same (job, user)
    pair has overlapping time ranges. This is a real, confirmed case, not
    hypothetical: one technician's own stop/restart mistake produced two
    genuinely overlapping entries for one real job in this project's test
    data (04:00-08:00 and 04:00-09:00, same person). Merging overlapping
    intervals into a duration figure — and the separate, still-open
    question of how to handle DIFFERENT users overlapping on the same job
    (default: sum each user's own post-merge hours independently, i.e. a
    labour-hours definition, not wall-clock elapsed time — flagged as a
    real open question to revisit if a genuine case ever appears, see
    PROJECT_CONTEXT.md) — is explicitly Part B's job (the average/merge
    math), not this sync step's. This table's only job is to mirror
    Jobber's real entries faithfully; no row here is ever merged, split,
    or dropped for overlap reasons.

    `complete` is sync_jobs()'s own completeness flag for this run, passed
    through unchanged — same reasoning as sync_visits(): entry data can't
    be more complete than the job data it was extracted from, and that flag
    is already dependency-aware (False whenever Clients came back PARTIAL
    too), so this correctly inherits the same protection without needing
    its own copy of that logic.
    """
    seen_ids = set()
    count = 0
    for job_node in job_nodes:
        job_id = job_node.get('id')
        if not job_id:
            continue
        job = JobberJob.objects.filter(tenant=tenant, jobber_id=job_id).first()
        if job is None:
            # The job itself was skipped in sync_jobs() (e.g. missing local
            # client) — its timesheet entries can't be linked to anything,
            # skip too.
            continue

        for entry_node in (job_node.get('timeSheetEntries') or {}).get('nodes') or []:
            entry_id = entry_node.get('id')
            if not entry_id:
                continue

            user_data = entry_node.get('user') or {}
            entry_user = None
            user_id = user_data.get('id')
            if user_id:
                entry_user = JobberUser.objects.filter(tenant=tenant, jobber_id=user_id).first()

            seen_ids.add(entry_id)
            JobberTimeSheetEntry.objects.update_or_create(
                tenant=tenant,
                jobber_id=entry_id,
                defaults={
                    'job': job,
                    'user': entry_user,
                    'final_duration_seconds': entry_node.get('finalDuration') or 0,
                    'started_at': _to_datetime(entry_node.get('startAt')),
                    'ended_at': _to_datetime(entry_node.get('endAt')),
                    'synced_at': timezone.now(),
                    'is_active': True,
                },
            )
            count += 1

    if complete:
        JobberTimeSheetEntry.objects.filter(tenant=tenant, is_active=True).exclude(jobber_id__in=seen_ids).update(is_active=False)

    return {'count': count, 'complete': complete}


def sync_invoices(account, tenant, deadline, clients_complete, jobs_complete):
    """
    Pull every Invoice, upsert, deactivate vanished ones — but ONLY if this
    pull was itself complete AND Clients' AND Jobs' pulls this run were also
    complete (``jobs_complete`` here is already Jobs' own dependency-aware
    flag — i.e. it's already False if Clients was PARTIAL when sync_jobs()
    ran, so this naturally chains through without re-deriving it).

    The Clients dependency is the same skip-on-missing-client bug as
    sync_jobs(): an invoice whose client has no local JobberClient row yet
    is skipped and excluded from seen_ids. The Jobs dependency is different
    in kind — an invoice is never skipped just because a linked job is
    missing locally, only its jobs M2M silently omits that link this pass.
    But that's its own real problem for the *this pass is trustworthy*
    question this completeness flag exists to answer: if Jobs was cut
    short, some JobberJob rows this invoice should link to may not exist
    yet, so `invoice.jobs.set(...)` below would under-link a still-real
    connection, not because the job vanished from Jobber but because this
    run's Jobs data was itself incomplete. Gating on jobs_complete too means
    that's reported honestly (this entity stays 'incomplete' until Jobs
    actually catches up) rather than papered over.

    The Job M2M itself is matched by job_number, not jobber_id —
    _INVOICES_QUERY's nested `jobs(first: 3)` selection only returns
    jobNumber today (the same limitation _format_job_refs() already has
    live, confirmed, not new here). job_number is effectively unique per
    tenant in practice, but it's a display number, not Jobber's own
    identifier — matching by a real jobber_id would be more robust. Not
    changed here: doing so would mean widening _INVOICES_QUERY to add each
    linked job's id, which — like _JOBS_QUERY — is shared with the Invoices
    live view and Accounts' full pull, so it has the same "don't add cost to
    unrelated live consumers" tradeoff _SYNC_JOBS_QUERY exists to avoid, and
    wasn't asked for this round. Flagging as a known limitation, not a
    silent gap.
    """
    # Deferred import — same circular-import reason as sync_clients()'s
    # _client_tags_display import above, invoices.py side of the cycle this time.
    from apps.jobber.api.invoices import _safe_int, _status_display

    nodes, own_complete = client.fetch_all_pages_bounded(client.fetch_invoices, account, 'fetch_invoices', deadline)
    seen_ids = set()
    count = 0
    for node in nodes:
        jobber_id = node.get('id')
        if not jobber_id:
            continue

        client_id = (node.get('client') or {}).get('id')
        invoice_client = JobberClient.objects.filter(tenant=tenant, jobber_id=client_id).first() if client_id else None
        if invoice_client is None:
            logger.warning(
                "sync_invoices: skipping invoice=%s for tenant=%s — its client=%s has no local JobberClient row yet",
                jobber_id, tenant.id, client_id,
            )
            continue

        amounts = node.get('amounts') or {}
        raw_status = node.get('invoiceStatus') or ''
        seen_ids.add(jobber_id)
        invoice, _created = JobberInvoice.objects.update_or_create(
            tenant=tenant,
            jobber_id=jobber_id,
            defaults={
                'client': invoice_client,
                'invoice_number': _safe_int(node.get('invoiceNumber')),
                'amount': _to_decimal(node.get('total')) or Decimal('0'),
                'balance': _to_decimal(amounts.get('invoiceBalance')),
                'issued_date': _to_datetime(node.get('issuedDate')),
                'due_date': _to_datetime(node.get('dueDate')),
                'invoice_status': raw_status,
                'status_display': _status_display(raw_status),
                'synced_at': timezone.now(),
                'is_active': True,
            },
        )

        linked_job_numbers = [
            n.get('jobNumber') for n in (node.get('jobs') or {}).get('nodes') or []
            if n.get('jobNumber') is not None
        ]
        if linked_job_numbers:
            invoice.jobs.set(JobberJob.objects.filter(tenant=tenant, job_number__in=linked_job_numbers))
        else:
            invoice.jobs.clear()

        count += 1

    complete = own_complete and clients_complete and jobs_complete
    if complete:
        JobberInvoice.objects.filter(tenant=tenant, is_active=True).exclude(jobber_id__in=seen_ids).update(is_active=False)

    return {'count': count, 'complete': complete}


def _finish_run(run, wanted, counts, had_failure, error_message):
    # NOTE: JobberSyncRun has no timesheet_entries_synced column (not added
    # this round — Part A's scope was the new entity + sync logic only, see
    # PROJECT_CONTEXT.md). any_progress/all_complete below still work
    # correctly for 'timesheet_entries' (they iterate `wanted`/`counts`
    # generically, no field-name dependency) — only the per-entity count
    # persisted onto the JobberSyncRun row is skipped for this one entity.
    any_progress = any(counts.get(e, {}).get('count', 0) > 0 for e in wanted)
    all_complete = bool(wanted) and all(counts.get(e, {}).get('complete') for e in wanted)

    if had_failure and not any_progress:
        # Nothing usable synced this run at all.
        status_value = JOBBER_SYNC_STATUS[3][0]
    elif all_complete and not had_failure:
        status_value = JOBBER_SYNC_STATUS[1][0]
    else:
        # Either a ceiling/page-cap cutoff left some entities incomplete, or
        # a failure hit partway through after real progress on others — the
        # design doc's own example ("got clients+jobs but timed out on
        # invoices") is explicitly PARTIAL, not FAILED.
        status_value = JOBBER_SYNC_STATUS[2][0]

    run.status = status_value
    run.finished_at = timezone.now()
    run.error_message = error_message
    run.clients_synced = counts.get('clients', {}).get('count', run.clients_synced)
    run.users_synced = counts.get('users', {}).get('count', run.users_synced)
    run.jobs_synced = counts.get('jobs', {}).get('count', run.jobs_synced)
    run.visits_synced = counts.get('visits', {}).get('count', run.visits_synced)
    run.invoices_synced = counts.get('invoices', {}).get('count', run.invoices_synced)
    run.save()


def sync_tenant(account, entities=None):
    """
    Run one sync attempt for account.tenant, per the design doc's §2/§3/§4.

    entities: optional subset of ALL_ENTITIES to sync (e.g. ['jobs',
    'invoices']). None means "sync everything". A future ensure_fresh()
    (not built in this step) will use this to sync only what a given view
    actually needs.

    Returns the JobberSyncRun row for this attempt — either the one this
    call created and finished, or (if another process held a non-stale lock)
    the most recent existing row, unchanged.
    """
    tenant = account.tenant
    wanted = tuple(entities) if entities else ALL_ENTITIES

    run = _claim_run(tenant)
    if run is None:
        return JobberSyncRun.objects.filter(tenant=tenant).order_by('-started_at').first()

    deadline = timezone.now() + SYNC_WALL_CLOCK_CEILING
    counts = {}
    job_nodes = []
    # Dependency-completeness inputs for the deactivation-safety gating in
    # sync_jobs()/sync_invoices() (see their docstrings). Default True when
    # the upstream entity isn't part of THIS run's `wanted` set at all —
    # there's nothing from this pass to gate against in that case. Only
    # ever set to a real, potentially-False value when that entity actually
    # ran this pass.
    clients_complete = True
    jobs_complete = True
    had_failure = False
    error_message = None

    try:
        if 'clients' in wanted:
            counts['clients'] = sync_clients(account, tenant, deadline)
            clients_complete = counts['clients']['complete']
        if 'users' in wanted:
            counts['users'] = sync_users(account, tenant, deadline)
        if 'jobs' in wanted or 'visits' in wanted or 'timesheet_entries' in wanted:
            job_result = sync_jobs(account, tenant, deadline, clients_complete)
            job_nodes = job_result.pop('nodes')
            jobs_complete = job_result['complete']
            counts['jobs'] = job_result
        if 'visits' in wanted:
            counts['visits'] = sync_visits(account, tenant, job_nodes, jobs_complete)
        if 'timesheet_entries' in wanted:
            counts['timesheet_entries'] = sync_timesheet_entries(account, tenant, job_nodes, jobs_complete)
        if 'invoices' in wanted:
            counts['invoices'] = sync_invoices(account, tenant, deadline, clients_complete, jobs_complete)
    except client.JobberAPIError as exc:
        had_failure = True
        error_message = str(exc)
        logger.exception("sync_tenant: JobberAPIError mid-sync for tenant=%s", tenant.id)
    except Exception as exc:
        had_failure = True
        error_message = str(exc)
        logger.exception("sync_tenant: unexpected error mid-sync for tenant=%s", tenant.id)

    _finish_run(run, wanted, counts, had_failure, error_message)
    return run


def _entity_synced_at(tenant, entity):
    """Latest synced_at across this entity's currently-active local rows, or None if it's never synced."""
    model = ENTITY_MODELS[entity]
    return model.objects.filter(tenant=tenant, is_active=True).aggregate(latest=Max('synced_at'))['latest']


def _last_run_was_unclean(tenant):
    """
    If the tenant's most recent JobberSyncRun wasn't a clean SUCCESS, its
    entities' synced_at timestamps can look artificially fresh — a PARTIAL
    run still stamps synced_at on whatever it did manage to upsert before
    being cut short, even though the overall picture is known-incomplete.
    JobberSyncRun only records one overall status per run, not a per-entity
    complete flag, so this can't pinpoint which specific entity was the
    problem — it's a coarse "the last attempt wasn't clean, don't trust
    synced_at alone this time" signal, applied to every entity ensure_fresh()
    is asked about on this call, not just whichever ones that past run
    happened to touch.
    """
    latest = JobberSyncRun.objects.filter(tenant=tenant).order_by('-started_at').first()
    if latest is None:
        return True
    return latest.status in (JOBBER_SYNC_STATUS[2][0], JOBBER_SYNC_STATUS[3][0])


def ensure_fresh(tenant, entities=None, require_complete=False):
    """
    Sync-on-demand for a stale request, per the design doc's "wait-for-fresh"
    architecture. Not wired into any view yet — built for the views to call
    once the local-read paths are approved.

    entities: which entities THIS caller needs (e.g. ['jobs', 'clients',
    'visits'] for the Jobs panel). Defaults to ALL_ENTITIES.

    require_complete: True for callers that need a full, correct picture to
    rank from (Accounts, Employees) — per the design doc's section on what
    ensure_fresh() does when a needed entity comes back PARTIAL: exactly one
    synchronous retry if the first attempt comes back PARTIAL, then accept
    PARTIAL either way rather than retrying forever. Paginated callers
    (Jobs, Invoices) tolerate a PARTIAL/stale pass fine (that's already
    pagination's normal contract) and should leave this False.

    NOTE: this parameter isn't part of the task's literal two-argument
    description of this function — it's added because the retry-once-if-
    PARTIAL rule for Accounts/Employees specifically can't be implemented
    without ensure_fresh() knowing which kind of caller it is. Flagging this
    as an addition, not a silent one.

    Returns a small result dict:
      - 'last_synced_at': the OLDEST synced_at among the requested entities
        after this call (the honest "as of" time for a view rendering all
        of them together) — a real datetime, or None if nothing has ever
        synced.
      - 'sync_warning': a short string when the requested entities aren't
        known-fully-fresh after this call (the sync this call triggered
        came back anything other than a clean SUCCESS), else None.
      - 'sync_status': the status of the sync this call triggered
        ('success'/'partial'/'failed'), or None if nothing was stale enough
        to trigger one.
      - 'entities': per-entity {'was_stale': bool, 'synced_at': datetime}
        detail — mainly for the comparison test scripts.
    """
    wanted = tuple(entities) if entities else ALL_ENTITIES

    account = JobberAccount.objects.filter(tenant=tenant, is_active=True).first()
    if account is None:
        # No connected account — nothing to sync. Every real caller already
        # gates on JobberAccount existing before reaching here; this is a
        # defensive fallback, not the expected path.
        return {
            'last_synced_at': None,
            'sync_warning': None,
            'sync_status': None,
            'entities': {e: {'was_stale': True, 'synced_at': None} for e in wanted},
        }

    force_resync = _last_run_was_unclean(tenant)
    synced_at_before = {e: _entity_synced_at(tenant, e) for e in wanted}
    stale_entities = [
        e for e in wanted
        if force_resync or synced_at_before[e] is None
        or timezone.now() - synced_at_before[e] >= STALENESS_THRESHOLD
    ]

    run = None
    if stale_entities:
        run = sync_tenant(account, entities=stale_entities)
        if require_complete and run.status == JOBBER_SYNC_STATUS[2][0]:
            run = sync_tenant(account, entities=stale_entities)

    synced_at_after = {e: _entity_synced_at(tenant, e) for e in wanted}

    sync_warning = None
    if run is not None and run.status != JOBBER_SYNC_STATUS[1][0]:
        sync_warning = f"Some data may be out of date (last sync: {run.status})."

    fresh_timestamps = [t for t in synced_at_after.values() if t is not None]
    last_synced_at = min(fresh_timestamps) if fresh_timestamps else None

    return {
        'last_synced_at': last_synced_at,
        'sync_warning': sync_warning,
        'sync_status': run.status if run is not None else None,
        'entities': {
            e: {'was_stale': e in stale_entities, 'synced_at': synced_at_after[e]}
            for e in wanted
        },
    }

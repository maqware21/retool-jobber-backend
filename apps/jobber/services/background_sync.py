"""
Fire-and-forget background dispatch for webhook-triggered syncs.

Exists specifically so JobberWebhookView can respond within Jobber's
1-second budget for the "job changed" family of topics (see that view's
own docstring) without waiting for a real sync_tenant() call, which can
legitimately take close to SYNC_WALL_CLOCK_CEILING (~25s). A bounded
ThreadPoolExecutor was chosen over a raw `threading.Thread` per webhook
so a burst of webhooks across many tenants can't spawn an unbounded
number of concurrent syncs (and therefore an unbounded number of open DB
connections -- see below). No new infrastructure (no Celery/RQ/broker).

COALESCING: more webhook topics means more near-simultaneous deliveries
for the SAME tenant. A naive "skip submitting if a sync is already
running" would silently drop whatever that extra event was telling us,
with nothing ever picking it back up. Instead: an event that arrives
WHILE a sync is genuinely in flight for that tenant marks a real,
DB-backed "pending_resync_marked_at" timestamp on JobberAccount (works
across all gunicorn worker processes, unlike an in-memory flag) instead
of submitting a redundant background task. The in-flight sync's own
worker thread, once it finishes its real pass, checks that marker itself
and -- if it was set during this run (not before it started) -- runs
exactly ONE more real pass, inline, on the SAME thread (no extra pool
slot, no idle waiting on another thread). This is bounded on purpose to
at most 1 trailing pass per invocation, never a chain.

2 real limits of this design, not built around, stated plainly:

  - A webhook arriving while a Sync Now- or ensure_fresh()-triggered
    sync is running (both call sync_tenant() directly, synchronously, in
    the request thread, never through submit_tenant_sync()) gets its
    marker set correctly (tenant_sync_in_flight() only checks
    JobberSyncRun.status, agnostic to which code path created the row),
    but nothing ever consumes it for that run -- the consume step only
    exists inside this module's own background-task path. That mark
    just sits until some LATER webhook-driven background sync happens to
    run and clears it as stale, with no dedicated trailing pass for it.
  - An event arriving during the ONE trailing pass itself sets a fresh
    marker that nothing in this module consumes again afterward (there
    is no second consume call after the trailing pass, by design, to
    keep this bounded to exactly one). That mark is not erased, but it
    also isn't fast-tracked -- it waits for whatever real sync (another
    webhook, or the 10-minute poll) runs next.
"""
import logging
from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections, transaction
from django.utils import timezone

from apps.jobber.models import JobberAccount, JobberSyncRun, SYNC_RUN_STALE_AFTER
from apps.jobber.services.sync import sync_tenant
from helpers.constants import JOBBER_SYNC_STATUS

logger = logging.getLogger(__name__)

# Bounded on purpose -- this pool exists only to keep the webhook handler's
# own response fast, not for throughput. Different tenants' webhook-
# triggered syncs can run concurrently (up to this cap); the SAME tenant's
# concurrent/duplicate syncs already serialize for free via sync_tenant()'s
# own _claim_run() select_for_update() lock, regardless of this cap. Kept
# small deliberately: each running sync is a real Jobber API pull plus one
# open DB connection (see _run_tenant_sync_safely's own docstring) for its
# full duration.
JOBBER_WEBHOOK_SYNC_MAX_WORKERS = 4

# Singleton, created exactly once -- at MODULE IMPORT time, not inside any
# request/view function. Django imports this module exactly once per
# worker process (via urls.py -> webhook.py -> here), the first time that
# process handles a request touching the jobber app's URLs; every
# subsequent webhook in that same process reuses this same executor
# instance and therefore the same real cap on concurrent threads.
# Constructing a ThreadPoolExecutor inside submit_tenant_sync() (or
# anywhere else that runs per-request) would silently defeat the whole
# point of bounding it -- a fresh pool per call has no shared cap at all.
#
# ThreadPoolExecutor has no public `daemon` parameter (confirmed against
# the installed Python 3.11's own concurrent/futures/thread.py source) --
# its worker threads are plain non-daemon threading.Thread objects, and
# concurrent.futures registers its own atexit hook that joins every
# outstanding worker thread on normal interpreter shutdown. In this
# project's real deployment (gunicorn, `--workers 3`, default
# ~30s graceful timeout -- see DEPLOYMENT.md, never overridden), a graceful
# worker restart will wait for an in-flight background sync to finish
# (up to SYNC_WALL_CLOCK_CEILING, ~25s) before that worker exits; if
# gunicorn's own graceful timeout elapses first, gunicorn force-kills the
# worker anyway, abandoning the sync mid-run. That's the exact same
# "worker died mid-sync" case JobberSyncRun.is_stuck's existing 5-minute
# self-heal (SYNC_RUN_STALE_AFTER) already recovers from today for
# ordinary synchronous syncs -- not a new failure mode this introduces.
_webhook_sync_executor = ThreadPoolExecutor(
    max_workers=JOBBER_WEBHOOK_SYNC_MAX_WORKERS,
    thread_name_prefix='jobber-webhook-sync',
)


def tenant_sync_in_flight(tenant):
    """
    True only if a REAL, currently-live sync is running for this tenant
    right now. Deliberately does NOT just check status='running' --
    JobberSyncRun.is_stuck's own real condition (a RUNNING row whose
    claimed_at heartbeat is older than SYNC_RUN_STALE_AFTER) is reused
    here as a queryset filter, so a worker that died mid-sync is treated
    as NOT in flight. A naive status='running' check alone would
    otherwise suppress new syncs for this tenant for up to
    SYNC_RUN_STALE_AFTER (5 minutes) after a dead worker, which is wrong
    -- the whole point of is_stuck's existing self-heal is that a new
    attempt SHOULD be allowed to proceed immediately in that case.

    _claim_run() always sets claimed_at at the same moment it creates a
    RUNNING row (confirmed directly in sync.py), so comparing against it
    directly here (rather than falling back to started_at) is accurate
    for every real row this ever checks.
    """
    return JobberSyncRun.objects.filter(
        tenant=tenant,
        status=JOBBER_SYNC_STATUS[0][0],
        claimed_at__gte=timezone.now() - SYNC_RUN_STALE_AFTER,
    ).exists()


def mark_pending_resync(tenant):
    """
    Records that a real event arrived for this tenant while a sync was
    already genuinely in flight (see tenant_sync_in_flight()). Consumed
    by that in-flight sync's own trailing check
    (_consume_pending_resync()) once it finishes, which triggers exactly
    one more real pass -- see this module's own docstring for the full
    coalescing design.

    A plain UPDATE on JobberAccount (one row per tenant already) -- a
    real, shared Postgres field, not in-memory, so this works correctly
    across all gunicorn worker processes. Self-clears even if nothing
    else explicitly resets it: see _consume_pending_resync()'s own
    docstring for why a stale leftover mark can never persist forever.
    """
    JobberAccount.objects.filter(tenant=tenant).update(pending_resync_marked_at=timezone.now())


def _consume_pending_resync(tenant, after):
    """
    Atomically reads and clears JobberAccount.pending_resync_marked_at
    for this tenant. Returns that timestamp ONLY if it's strictly after
    `after` (the real start time of the sync pass that's asking) --
    meaning a real event arrived DURING this pass, genuinely worth a
    trailing pass. Returns None otherwise.

    Critically, the marker is cleared EVERY time this runs, regardless of
    whether it was fresh enough to return -- a mark at or before `after`
    is either already covered by the pass that just ran, or a genuine
    stale leftover from an earlier worker that died before ever
    consuming it. Clearing it unconditionally here is what makes the
    marker self-clearing even when nothing else ever explicitly resets
    it -- every real sync pass checks this on its way out, so a stale
    mark can survive at most until the next real sync for that tenant,
    never permanently.

    select_for_update() inside a transaction, same real locking
    primitive sync.py's own _claim_run() already uses -- defensive
    against 2 passes finishing at nearly the same moment across different
    gunicorn workers, even though _claim_run()'s own lock already makes
    that a rare edge case in practice (only one real sync_tenant() call
    is ever actually doing work for a tenant at a time).
    """
    with transaction.atomic():
        account = JobberAccount.objects.select_for_update().filter(tenant=tenant).first()
        if account is None or account.pending_resync_marked_at is None:
            return None
        stale_mark = account.pending_resync_marked_at
        account.pending_resync_marked_at = None
        account.save(update_fields=['pending_resync_marked_at'])
        if stale_mark <= after:
            return None
        return stale_mark


def submit_tenant_sync(account, entities):
    """
    Submits a real sync_tenant() call to the bounded background pool and
    returns immediately, without waiting for it to finish. Called by
    JobberWebhookView when no sync is currently in flight for this
    tenant (see tenant_sync_in_flight()) -- when one IS in flight, the
    caller marks pending_resync instead of calling this, per this
    module's own coalescing design.
    """
    _webhook_sync_executor.submit(_run_tenant_sync_safely, account, entities)


def _run_one_pass(account, entities, tenant):
    """One real sync_tenant() call, with its own connection cleanup. Returns sync_tenant()'s own real JobberSyncRun row."""
    try:
        return sync_tenant(account, entities=entities)
    finally:
        close_old_connections()


def _did_own_pass(run, run_started_at):
    """
    True only if `run` reflects a pass THIS call itself actually
    performed, not sync_tenant()'s own "lost the claim, return the most
    recent existing row instead" fallback. Quoting sync.py directly:

        run = _claim_run(tenant)
        if run is None:
            return JobberSyncRun.objects.filter(tenant=tenant).order_by('-started_at').first()

    A row THIS call's own _claim_run() created and finished is never
    still 'running' by the time sync_tenant() returns (synchronous --
    _finish_run() always runs before sync_tenant() returns), and its
    started_at is set at the moment of that creation, which cannot be
    before run_started_at (captured just before calling sync_tenant()).
    Either signal alone proves this call did NOT get its own real pass.
    """
    if run is None:
        return False
    if run.status == JOBBER_SYNC_STATUS[0][0]:
        return False
    if run.started_at < run_started_at:
        return False
    return True


def _run_tenant_sync_safely(account, entities):
    """
    Runs on a pool worker thread, never on the request thread. Both
    required safety fixes live here, explicitly:

    1. try/except + logger.exception() -- a raw background thread's
       uncaught exception only prints to stderr via Python's default
       thread excepthook; it never reaches Django's request-level error
       reporting. Without this, a real sync failure triggered by a
       webhook could fail completely silently.
    2. close_old_connections() in `finally` (inside _run_one_pass) --
       Django closes DB connections via the request_finished signal,
       which only fires for the request thread and never for a detached
       background thread. CONN_MAX_AGE is unset in settings.py (defaults
       to 0) against a real PostgreSQL backend with a finite
       max_connections. Without this, every webhook received leaks one
       more open connection until the database refuses new ones.

    Only a task whose sync_tenant() call actually performed a pass (see
    _did_own_pass()) may consume the pending-resync marker or run a
    trailing pass. A task that lost the claim to another concurrent
    process (both see "nothing in flight" and both submit) must not
    touch the marker at all -- consuming it here would STEAL it from the
    task that actually did the work and will check it itself when it
    finishes; stealing it would silently drop a real coalesced event with
    nothing left to pick it back up.

    A truly unexpected exception (outside sync_tenant()'s own internal
    error handling, which already catches and records a normal mid-sync
    failure itself -- see sync.py) still consumes/clears the marker,
    since there's no `run` object here to tell whether a claim was won;
    leaving it set indefinitely would be worse than an occasional early
    clear. No trailing pass is attempted on top of a failure either way.

    After a real, own pass succeeds, checks for a real coalescing mark
    set DURING this run (see _consume_pending_resync()) and -- bounded to
    AT MOST ONE trailing pass, never a loop -- runs sync_tenant() exactly
    one more time, inline on this SAME thread, if one was found. A
    failure in the trailing pass is logged at error level and NOT
    retried further here; the 10-minute staleness poll is the real
    backstop for anything still stale after that.
    """
    tenant = account.tenant
    run_started_at = timezone.now()
    run = None

    try:
        run = _run_one_pass(account, entities, tenant)
    except Exception:
        logger.exception(
            "background_sync: sync_tenant() failed for tenant=%s "
            "(webhook-triggered, entities=%s)",
            tenant.id, entities,
        )
        _consume_pending_resync(tenant, after=run_started_at)
        return

    if not _did_own_pass(run, run_started_at):
        logger.info(
            "background_sync: tenant=%s's sync_tenant() call lost the claim to "
            "another process already performing this pass -- not touching "
            "the pending marker; whichever task actually did the work "
            "owns that decision instead.",
            tenant.id,
        )
        return

    marked_at = _consume_pending_resync(tenant, after=run_started_at)
    if marked_at is None:
        logger.info(
            "background_sync: tenant=%s's pass finished with no pending marker -- no trailing pass needed",
            tenant.id,
        )
        return

    logger.info(
        "background_sync: coalesced event detected for tenant=%s (marked_at=%s, "
        "during a pass started at %s) -- running exactly 1 trailing pass",
        tenant.id, marked_at, run_started_at,
    )
    try:
        _run_one_pass(account, entities, tenant)
    except Exception:
        logger.error(
            "background_sync: trailing coalesced sync_tenant() failed for "
            "tenant=%s -- not retrying further here; relying on the "
            "10-minute staleness poll to catch up.",
            tenant.id, exc_info=True,
        )

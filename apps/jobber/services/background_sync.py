"""
Fire-and-forget background dispatch for webhook-triggered syncs.

Exists specifically so JobberWebhookView can respond within Jobber's
1-second budget for JOB_UPDATE/JOB_CLOSED (see that view's own docstring)
without waiting for a real sync_tenant() call, which can legitimately take
close to SYNC_WALL_CLOCK_CEILING (~25s). A bounded ThreadPoolExecutor was
chosen over a raw `threading.Thread` per webhook so a burst of webhooks
across many tenants can't spawn an unbounded number of concurrent syncs
(and therefore an unbounded number of open DB connections — see below).
No new infrastructure (no Celery/RQ/broker) — see
jobber_webhooks_design_proposal.md for the full design discussion this
resolves.
"""
import logging
from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections

from apps.jobber.services.sync import sync_tenant

logger = logging.getLogger(__name__)

# Bounded on purpose — this pool exists only to keep the webhook handler's
# own response fast, not for throughput. Different tenants' webhook-
# triggered syncs can run concurrently (up to this cap); the SAME tenant's
# concurrent/duplicate syncs already serialize for free via sync_tenant()'s
# own _claim_run() select_for_update() lock, regardless of this cap. Kept
# small deliberately: each running sync is a real Jobber API pull plus one
# open DB connection (see _run_tenant_sync_safely's own docstring) for its
# full duration.
JOBBER_WEBHOOK_SYNC_MAX_WORKERS = 4

# Singleton, created exactly once — at MODULE IMPORT time, not inside any
# request/view function. Django imports this module exactly once per
# worker process (via urls.py -> webhook.py -> here), the first time that
# process handles a request touching the jobber app's URLs; every
# subsequent webhook in that same process reuses this same executor
# instance and therefore the same real cap on concurrent threads.
# Constructing a ThreadPoolExecutor inside submit_tenant_sync() (or
# anywhere else that runs per-request) would silently defeat the whole
# point of bounding it — a fresh pool per call has no shared cap at all.
#
# ThreadPoolExecutor has no public `daemon` parameter (confirmed against
# the installed Python 3.11's own concurrent/futures/thread.py source) —
# its worker threads are plain non-daemon threading.Thread objects, and
# concurrent.futures registers its own atexit hook that joins every
# outstanding worker thread on normal interpreter shutdown. In this
# project's real deployment (gunicorn, `--workers 3`, default
# ~30s graceful timeout — see DEPLOYMENT.md, never overridden), a graceful
# worker restart will wait for an in-flight background sync to finish
# (up to SYNC_WALL_CLOCK_CEILING, ~25s) before that worker exits; if
# gunicorn's own graceful timeout elapses first, gunicorn force-kills the
# worker anyway, abandoning the sync mid-run. That's the exact same
# "worker died mid-sync" case JobberSyncRun.is_stuck's existing 5-minute
# self-heal (SYNC_RUN_STALE_AFTER) already recovers from today for
# ordinary synchronous syncs — not a new failure mode this introduces.
_webhook_sync_executor = ThreadPoolExecutor(
    max_workers=JOBBER_WEBHOOK_SYNC_MAX_WORKERS,
    thread_name_prefix='jobber-webhook-sync',
)


def submit_tenant_sync(account, entities):
    """
    Submits a real sync_tenant() call to the bounded background pool and
    returns immediately, without waiting for it to finish. Called by
    JobberWebhookView for JOB_UPDATE/JOB_CLOSED so the webhook response
    itself never blocks on the real sync.
    """
    _webhook_sync_executor.submit(_run_tenant_sync_safely, account, entities)


def _run_tenant_sync_safely(account, entities):
    """
    Runs on a pool worker thread, never on the request thread. Both
    required safety fixes live here, explicitly:

    1. try/except + logger.exception() — a raw background thread's
       uncaught exception only prints to stderr via Python's default
       thread excepthook; it never reaches Django's request-level error
       reporting. Without this, a real sync failure triggered by a
       webhook could fail completely silently.
    2. close_old_connections() in `finally` — Django closes DB
       connections via the request_finished signal, which only fires for
       the request thread and never for a detached background thread.
       CONN_MAX_AGE is unset in settings.py (defaults to 0) against a
       real PostgreSQL backend with a finite max_connections. Without
       this, every webhook received leaks one more open connection until
       the database refuses new ones.
    """
    try:
        sync_tenant(account, entities=entities)
    except Exception:
        logger.exception(
            "background_sync: sync_tenant() failed for tenant=%s "
            "(webhook-triggered, entities=%s)",
            account.tenant_id, entities,
        )
    finally:
        close_old_connections()

"""
Jobber webhook receiver.

All webhook events sent by Jobber arrive here via POST. The endpoint is public
(no JWT) — authenticity is established by verifying the HMAC-SHA256 signature
Jobber includes on every request.

Jobber requires a 200 response within 1 second. APP_DISCONNECT's own handler
stays synchronous (a single filtered UPDATE on one row, effectively
instant). The "job changed" family of topics below cannot make that same
claim — the real action they trigger is a full sync_tenant() call, which
can legitimately take close to sync.SYNC_WALL_CLOCK_CEILING (~25s) — so
those dispatch to background_sync.submit_tenant_sync() instead of running
inline. See background_sync.py's own module docstring for the full
reasoning (a bounded ThreadPoolExecutor, not Celery/RQ, and not a raw
unbounded thread either).

_JOB_CHANGE_WEBHOOK_TOPICS is kept as one named constant, not inlined in
the dispatch below, so adding another topic (e.g. VISIT_DESTROY or
JOB_DESTROY) later is a 1-line change. Confirmed real, exact spellings
against jobber_graphql_schema.json's own WebHookTopicEnum, not guessed.
TIMESHEET_DESTROY specifically: confirmed live that deleting a time
entry fires NO webhook at all under JOB_UPDATE/TIMESHEET_UPDATE alone --
a plain delete is its own distinct topic, not folded into
TIMESHEET_UPDATE.
"""

import base64
import hashlib
import hmac as hmac_module
import json
import logging

from django.conf import settings
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.jobber.models import JobberAccount
from apps.jobber.services.background_sync import mark_pending_resync, submit_tenant_sync, tenant_sync_in_flight

logger = logging.getLogger(__name__)

_JOB_CHANGE_WEBHOOK_TOPICS = (
    'JOB_CREATE',
    'JOB_UPDATE',
    'JOB_CLOSED',
    'TIMESHEET_CREATE',
    'TIMESHEET_UPDATE',
    'TIMESHEET_DESTROY',
    'VISIT_UPDATE',
    'VISIT_COMPLETE',
)


def _verify_signature(raw_body: bytes, header_value: str) -> bool:
    """
    Verify the ``X-Jobber-Hmac-SHA256`` header against the raw request body.

    Jobber computes HMAC-SHA256 of the raw body bytes using the app's
    OAuth client secret and base64-encodes the digest. We reproduce that
    computation and compare with ``hmac.compare_digest`` to prevent
    timing-based attacks.
    """
    if not header_value:
        return False
    secret = settings.JOBBER_CLIENT_SECRET.encode('utf-8')
    digest = hmac_module.new(secret, raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode('utf-8')
    return hmac_module.compare_digest(expected, header_value)


@method_decorator(csrf_exempt, name='dispatch')
class JobberWebhookView(View):
    """
    POST /v1/jobber/webhook/

    Receives and dispatches Jobber webhook events. Must be registered in the
    Jobber Developer Center with the APP_DISCONNECT topic plus every topic in
    _JOB_CHANGE_WEBHOOK_TOPICS above (and any others added later) pointing at:
        https://api.techtrackpro.com/v1/jobber/webhook/
    """

    def post(self, request):
        # Read the raw body first — before any parsing — so the HMAC is
        # computed over the exact bytes Jobber signed.
        raw_body = request.body
        signature = request.headers.get('X-Jobber-Hmac-SHA256', '')

        if not _verify_signature(raw_body, signature):
            logger.warning(
                "Jobber webhook rejected: invalid or missing X-Jobber-Hmac-SHA256 "
                "(remote addr: %s)",
                request.META.get('REMOTE_ADDR'),
            )
            return HttpResponse(status=401)

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            logger.warning("Jobber webhook: could not parse JSON body")
            # Still return 200 — we authenticated the sender; a malformed
            # body is our problem to log, not Jobber's to retry indefinitely.
            return HttpResponse(status=200)

        topic = (
            (payload.get('data') or {})
            .get('webHookEvent', {})
            .get('topic')
        )

        logger.info("Jobber webhook received: topic=%r", topic)

        if topic == 'APP_DISCONNECT':
            # Stays inline — a single filtered UPDATE on one row, well
            # within the 1-second budget on its own.
            self._handle_app_disconnect(payload)
        elif topic in _JOB_CHANGE_WEBHOOK_TOPICS:
            # NOT run inline — see this module's own docstring for why a
            # real sync_tenant() call can't honestly stay synchronous here.
            self._handle_job_change(payload)
        else:
            logger.info("Jobber webhook: no handler for topic %r — ignoring", topic)

        # Always 200 — Jobber disables webhooks for apps that consistently
        # fail to respond in time.
        return HttpResponse(status=200)

    def _handle_app_disconnect(self, payload):
        """
        Mark the JobberAccount for the disconnected account as inactive.

        Jobber guarantees at-least-once delivery, so this event may arrive
        more than once. Filtering on ``is_active=True`` makes repeated
        deliveries safe no-ops.
        """
        event = (payload.get('data') or {}).get('webHookEvent', {})
        account_id = event.get('accountId')

        if not account_id:
            logger.warning("APP_DISCONNECT webhook payload missing accountId — cannot act")
            return

        # is_active=True filter makes this idempotent: if already inactive,
        # filter returns None and we log and exit without touching the row.
        account = JobberAccount.objects.filter(
            jobber_account_id=account_id,
            is_active=True,
        ).first()

        if account is None:
            logger.info(
                "APP_DISCONNECT for accountId=%r — no active JobberAccount found "
                "(already inactive, or account was never enriched with an ID)",
                account_id,
            )
            return

        account.is_active = False
        account.save(update_fields=['is_active', 'updated_at'])
        logger.info(
            "APP_DISCONNECT: JobberAccount tenant=%s marked inactive (accountId=%r)",
            account.tenant_id,
            account_id,
        )

    def _handle_job_change(self, payload):
        """
        Submits a real, targeted sync to the background pool for the
        account this event belongs to -- or, if one is already genuinely
        in flight for this tenant, marks it as pending instead (see
        background_sync.py's own module docstring for the full
        coalescing design this is part of).

        The payload only carries topic/accountId/itemId, never the changed
        job data itself — itemId is NOT used to filter the sync (sync_jobs()
        has no per-job fetch today, a real, accepted inefficiency). This
        exists purely as a trigger: "something changed for this account, go
        find out via the existing full-tenant sync" — same trigger-only
        role APP_DISCONNECT's own accountId already plays above.

        Confirmed live: a single real user action can fire 2-3 of these
        webhooks within well under a second of each other, on different
        gunicorn workers, in no reliable order (delivery order is not
        guaranteed to match the order the underlying changes actually
        happened in) -- tenant_sync_in_flight()/mark_pending_resync()
        below is what keeps a real burst like that from submitting
        several redundant background tasks at once, without ever
        silently dropping whichever event arrived while one was already
        running.

        Idempotency beyond that: still has no dedup logic of its own for
        the "nothing in flight yet" case. Jobber's at-least-once delivery
        (including duplicate near-simultaneous deliveries) is already
        absorbed for free by sync_tenant()'s own _claim_run()
        select_for_update() lock — two near-simultaneous deliveries that
        BOTH see "nothing in flight" just resolve to "whichever commits
        first does the real work," identical to JobberSyncNowView's own
        documented double-click safety.
        """
        event = (payload.get('data') or {}).get('webHookEvent', {})
        account_id = event.get('accountId')
        topic = event.get('topic')

        if not account_id:
            logger.warning("%s webhook payload missing accountId — cannot act", topic)
            return

        account = JobberAccount.objects.filter(
            jobber_account_id=account_id,
            is_active=True,
        ).first()

        if account is None:
            logger.info(
                "%s for accountId=%r — no active JobberAccount found, nothing to sync",
                topic, account_id,
            )
            return

        if tenant_sync_in_flight(account.tenant):
            mark_pending_resync(account.tenant)
            logger.info(
                "%s: sync already in flight for tenant=%s — marked pending, "
                "the in-flight pass's own trailing check will pick it up",
                topic, account.tenant_id,
            )
            return

        logger.info(
            "%s: submitting background sync for JobberAccount tenant=%s (accountId=%r)",
            topic, account.tenant_id, account_id,
        )
        submit_tenant_sync(account, entities=['jobs', 'visits', 'timesheet_entries'])

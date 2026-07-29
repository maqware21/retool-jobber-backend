"""
Jobber webhook receiver.

All webhook events sent by Jobber arrive here via POST. The endpoint is public
(no JWT) — authenticity is established by verifying the HMAC-SHA256 signature
Jobber includes on every request.

Jobber requires a 200 response within 1 second. Heavy processing should be
offloaded to a background task (TODO: move to Celery/RQ when task
infrastructure is added).
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

logger = logging.getLogger(__name__)


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
    Jobber Developer Center with the APP_DISCONNECT topic (and any others
    added later) pointing at:
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
            # TODO: move to a background task (Celery/RQ) if DB latency
            # ever risks breaching Jobber's 1-second response requirement.
            self._handle_app_disconnect(payload)
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

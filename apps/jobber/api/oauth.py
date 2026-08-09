import logging
from urllib.parse import urlencode

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.http import HttpResponseRedirect
from django.utils import timezone
from rest_framework import status
from rest_framework.views import APIView

from apps.jobber.models import (
    JobberAccount,
    JobberClient,
    JobberInvoice,
    JobberJob,
    JobberSyncRun,
    JobberUser,
    JobberVisit,
)
from apps.jobber.services import client
from apps.jobber.services import state as state_service
from apps.tenants.models import Tenant
from apps.users.models import User
from helpers.api_exception import validator_errors
from helpers.constants import JOBBER_SYNC_STATUS
from helpers.messages import MESSAGES
from helpers.user_permissions import CustomerPermission
from helpers.utils import api_response_parser

logger = logging.getLogger(__name__)


def _redirect_to_frontend(base_url, **params):
    """Redirect the browser to a frontend URL, appending query params if any."""
    url = f"{base_url}?{urlencode(params)}" if params else base_url
    return HttpResponseRedirect(url)


class JobberConnectView(APIView):
    """
    GET /v1/jobber/connect/
    Starts the OAuth flow. Returns the Jobber authorization URL for the
    frontend to redirect the customer's browser to.
    """
    permission_classes = [CustomerPermission]

    def get(self, request):
        data = {}
        try:
            if not settings.JOBBER_CLIENT_ID:
                return api_response_parser(
                    data=data,
                    message=MESSAGES['JOBBER_NOT_CONFIGURED'],
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                    success=False,
                )
            state = state_service.build_state(request.user.id)
            data = {'authorize_url': client.build_authorize_url(state)}
            return api_response_parser(
                data=data,
                message=MESSAGES['SUCCESS'],
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)


class JobberCallbackView(APIView):
    """
    GET /v1/jobber/callback/
    OAuth redirect target hit by the customer's browser (no JWT). Verifies the
    signed state, exchanges the code for tokens, links them to the customer's
    tenant, then redirects the browser back to the frontend.
    """
    permission_classes = []
    authentication_classes = []

    def get(self, request):
        # Jobber sends ?error=access_denied when the user declines.
        if request.query_params.get('error'):
            reason = request.query_params.get('error')
            logger.info("Jobber authorization declined: %s", reason)
            return _redirect_to_frontend(settings.JOBBER_CONNECT_FAILURE_URL, reason=reason)

        code = request.query_params.get('code')
        raw_state = request.query_params.get('state')
        if not code or not raw_state:
            return _redirect_to_frontend(settings.JOBBER_CONNECT_FAILURE_URL, reason='missing_params')

        try:
            user_id = state_service.read_state(raw_state)
        except signing.BadSignature:
            logger.warning("Jobber callback with invalid/expired state")
            return _redirect_to_frontend(settings.JOBBER_CONNECT_FAILURE_URL, reason='invalid_state')

        user = User.get(id=user_id, check_from_all=True)
        if user is None:
            return _redirect_to_frontend(settings.JOBBER_CONNECT_FAILURE_URL, reason='unknown_user')

        try:
            token_data = client.exchange_code(code)
            account = self._link_account(user, token_data)
        except client.JobberAPIError:
            return _redirect_to_frontend(settings.JOBBER_CONNECT_FAILURE_URL, reason='exchange_failed')
        except Exception:
            logger.exception("Unexpected error completing Jobber connection for user=%s", user_id)
            return _redirect_to_frontend(settings.JOBBER_CONNECT_FAILURE_URL, reason='server_error')

        # Best-effort enrichment — never blocks a successful connection.
        self._enrich_account(account)

        return _redirect_to_frontend(settings.JOBBER_CONNECT_SUCCESS_URL, status='connected')

    @transaction.atomic
    def _link_account(self, user, token_data):
        """Attach (creating if needed) a tenant to the user and store the tokens."""
        tenant = user.tenant
        if tenant is None:
            tenant = Tenant.objects.create()
            user.tenant = tenant
            user.save(update_fields=['tenant'])

        account = JobberAccount.objects.filter(tenant=tenant).first()
        # A JobberAccount already existing for this tenant means this is a
        # RECONNECT, not this tenant's first-ever Jobber connect — see
        # _invalidate_local_data() below for why that distinction matters.
        is_reconnect = account is not None
        if account is None:
            account = JobberAccount(tenant=tenant, access_token='', refresh_token='')
        account.is_active = True
        account.store_tokens(token_data)

        if is_reconnect:
            self._invalidate_local_data(tenant)

        # Phase 2 local-sync bootstrap: seed the tenant's first JobberSyncRun
        # row as an already-finished SUCCESS with zero counts, in the same
        # transaction as the JobberAccount row above. The sync engine's
        # concurrency guard (select_for_update on the tenant's latest
        # JobberSyncRun row) assumes one already exists — this is the only
        # place a brand-new tenant gets one. Seeding SUCCESS (not RUNNING)
        # means the very first real sync sees a plain "stale, never synced"
        # tenant and takes the normal stale-request path, with no
        # special-cased "first sync ever" branch needed anywhere in the sync
        # engine. Guarded by .exists() so a tenant reconnecting Jobber after
        # a disconnect doesn't get a second bootstrap row wiping/duplicating
        # real sync history.
        if not JobberSyncRun.objects.filter(tenant=tenant).exists():
            JobberSyncRun.objects.create(
                tenant=tenant,
                status=JOBBER_SYNC_STATUS[1][0],
                finished_at=timezone.now(),
            )

        return account

    @staticmethod
    def _invalidate_local_data(tenant):
        """
        A tenant reconnecting Jobber may be pointing at a DIFFERENT
        underlying Jobber account than whatever last synced this tenant's
        local tables — those tables are scoped by Tenant only, not by which
        specific JobberAccount synced them, so old data would otherwise
        keep looking "fresh" by timestamp even though it belongs to a
        since-replaced connection. Real gap, found via manual testing.

        Fix: mark every existing entity row for this tenant inactive.
        ensure_fresh() already treats "no active rows for this entity" as
        "never synced, definitely stale" — the exact same path already
        proven correct for a brand-new tenant's first-ever connect — so the
        very next normal page load triggers a real, correct sync
        automatically. No new sync call here on purpose: this only needs to
        make the existing staleness check see the truth, and staying fast
        and dumb keeps this transaction quick rather than slowing down the
        OAuth redirect itself.

        Called only when is_reconnect is True (see _link_account above) —
        a brand-new tenant has no rows here yet, so this would be a no-op
        for it regardless, but there's no reason to run five empty UPDATE
        queries on the one path (first-ever connect) that can never need them.
        """
        JobberClient.objects.filter(tenant=tenant, is_active=True).update(is_active=False)
        JobberUser.objects.filter(tenant=tenant, is_active=True).update(is_active=False)
        JobberJob.objects.filter(tenant=tenant, is_active=True).update(is_active=False)
        JobberVisit.objects.filter(tenant=tenant, is_active=True).update(is_active=False)
        JobberInvoice.objects.filter(tenant=tenant, is_active=True).update(is_active=False)

    def _enrich_account(self, account):
        info = client.fetch_account_info(account)
        if not info:
            return
        account.jobber_account_id = info.get('id') or account.jobber_account_id
        account.save(update_fields=['jobber_account_id', 'updated_at'])

        name = info.get('name')
        tenant = account.tenant
        if name and not tenant.business_name:
            tenant.business_name = name
            tenant.save(update_fields=['business_name', 'updated_at'])


class JobberStatusView(APIView):
    """
    GET /v1/jobber/status/
    Reports whether the authenticated customer's tenant has a live Jobber
    connection. Never exposes tokens.
    """
    permission_classes = [CustomerPermission]

    def get(self, request):
        data = {'connected': False}
        try:
            account = self._account_for(request.user)
            if account:
                data = {
                    'connected': True,
                    'jobber_account_id': account.jobber_account_id,
                    'business_name': account.tenant.business_name,
                    'scope': account.scope,
                    'connected_at': account.created_at,
                }
            return api_response_parser(
                data=data,
                message=MESSAGES['SUCCESS'],
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)

    @staticmethod
    def _account_for(user):
        if not user.tenant_id:
            return None
        return JobberAccount.objects.filter(tenant_id=user.tenant_id, is_active=True).first()


class JobberDisconnectView(APIView):
    """
    POST /v1/jobber/disconnect/
    Removes the stored Jobber tokens for the customer's tenant.
    """
    permission_classes = [CustomerPermission]

    def post(self, request):
        data = {}
        try:
            account = None
            if request.user.tenant_id:
                account = JobberAccount.objects.filter(tenant_id=request.user.tenant_id).first()

            if account is None:
                return api_response_parser(
                    data=data,
                    message=MESSAGES['JOBBER_NOT_CONNECTED'],
                    status=status.HTTP_404_NOT_FOUND,
                    success=False,
                )

            # Notify Jobber we initiated the disconnect so it can immediately
            # invalidate the tokens on its side. Best-effort — failure is
            # logged inside call_app_disconnect and does not block cleanup.
            client.call_app_disconnect(account)
            account.delete()
            return api_response_parser(
                data=data,
                message=MESSAGES['JOBBER_DISCONNECTED'],
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)

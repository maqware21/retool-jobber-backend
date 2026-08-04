import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.views import APIView

from apps.jobber.models import JobberAccount
from apps.jobber.services import client
from helpers.api_exception import validator_errors
from helpers.messages import MESSAGES
from helpers.user_permissions import CustomerPermission
from helpers.utils import api_response_parser

logger = logging.getLogger(__name__)

PAGE_SIZE = 25
# Safety cap per collection: 20 pages x 25 records = 500 records. A ranking
# needs the complete picture (partial data could produce a WRONG order, not
# just an incomplete list) — unlike Jobs/Invoices, where a single page is an
# acceptable simplification. This cap exists so a very large real account
# can't turn one request into an unbounded pull.
MAX_PAGES = 20


def _fetch_all(fetch_fn, account, label):
    """
    Loop `fetch_fn(account, first=PAGE_SIZE, after=cursor)` following the
    real page_info.end_cursor until Jobber reports no more pages, or
    MAX_PAGES is hit. Returns the full list of nodes collected.

    If the cap is hit while more pages still exist, logs a clear warning —
    the caller's ranking is based on a bounded sample, not literally
    everything, and that should be visible in the logs, not silent.
    """
    all_nodes = []
    cursor = None
    for _page_num in range(MAX_PAGES):
        page = fetch_fn(account, first=PAGE_SIZE, after=cursor)
        all_nodes.extend(page.get('nodes') or [])
        page_info = page.get('pageInfo') or {}
        if not page_info.get('hasNextPage'):
            return all_nodes
        cursor = page_info.get('endCursor')

    logger.warning(
        "%s: hit the %d-page safety cap (%d records) for tenant=%s — "
        "the Accounts ranking is based on a bounded sample, not the full account.",
        label, MAX_PAGES, MAX_PAGES * PAGE_SIZE, account.tenant_id,
    )
    return all_nodes


def _rank_accounts(job_nodes, invoice_nodes):
    """
    Group by client.id: job count per client from job_nodes, summed
    invoice total per client from invoice_nodes (client is already present
    on every invoice node — no new field needed). Merge, sort descending
    by revenue.
    """
    clients = {}

    def _entry(client_data):
        client_id = client_data.get('id')
        if not client_id:
            return None
        entry = clients.setdefault(
            client_id, {'name': client_data.get('name'), 'job_count': 0, 'revenue': 0.0},
        )
        if not entry.get('name') and client_data.get('name'):
            entry['name'] = client_data.get('name')
        return entry

    for job in job_nodes:
        entry = _entry(job.get('client') or {})
        if entry is not None:
            entry['job_count'] += 1

    for invoice in invoice_nodes:
        entry = _entry(invoice.get('client') or {})
        if entry is not None:
            entry['revenue'] += invoice.get('total') or 0.0

    ranked = sorted(clients.values(), key=lambda c: c['revenue'], reverse=True)
    return [
        {
            'name': c['name'],
            'job_count': c['job_count'],
            'revenue': c['revenue'],
            # Confirmed dead end, same as Jobs' trade field: no service-type
            # taxonomy exists anywhere in Jobber's schema. Always null — the
            # frontend renders this as "—", not hidden.
            'type': None,
        }
        for c in ranked
    ]


class JobberAccountsView(APIView):
    """
    GET /v1/jobber/accounts/
    Computes a full "Top Accounts" ranking for the authenticated customer's
    connected Jobber account. No pagination params — this endpoint always
    computes the complete ranking, that's the whole point.

    UNCACHED: every request re-pulls every job and every invoice (bounded
    by MAX_PAGES) and re-ranks from scratch. This needs a caching layer
    (e.g. 15-30 min per tenant) before real customer data volume — see
    PROJECT_CONTEXT.md. Deliberately shipped uncached for now; flagging,
    not deferring silently.
    """
    permission_classes = [CustomerPermission]

    def get(self, request):
        data = {'connected': False, 'accounts': [], 'computed_at': None}
        try:
            account = self._account_for(request.user)
            if account is None:
                return api_response_parser(
                    data=data,
                    message=MESSAGES['JOBBER_NOT_CONNECTED'],
                    status=status.HTTP_200_OK,
                    success=True,
                )

            job_nodes = _fetch_all(client.fetch_jobs, account, 'fetch_jobs')
            invoice_nodes = _fetch_all(client.fetch_invoices, account, 'fetch_invoices')

            data = {
                'connected': True,
                'accounts': _rank_accounts(job_nodes, invoice_nodes),
                'computed_at': timezone.now().isoformat(),
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

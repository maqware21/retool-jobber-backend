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


def _job_service_type(job_node):
    """
    The job's first line item's linked product/service name, or None if the
    job has no line items or used a freeform line item. Duplicated from
    apps.jobber.api.jobs._job_service_type — same reasoning as this app's
    other small duplicated helpers (e.g. _humanize_status in jobs.py and
    employees.py): each api/*.py module stays self-contained, only client.py
    is cross-imported.
    """
    line_items = (job_node.get('lineItems') or {}).get('nodes') or []
    if not line_items:
        return None
    linked = line_items[0].get('linkedProductOrService')
    if not linked:
        return None
    return linked.get('name')


def _client_tags_display(client_data):
    """Every tag label on the client, comma-joined, or None if it has none."""
    tag_nodes = (client_data.get('tags') or {}).get('nodes') or []
    labels = [t.get('label') for t in tag_nodes if t.get('label')]
    return ', '.join(labels) if labels else None


def _rank_accounts(job_nodes, invoice_nodes):
    """
    Group by client.id: job count per client from job_nodes, summed
    invoice total per client from invoice_nodes (client is already present
    on every invoice node — no new field needed). Merge, sort descending
    by revenue.

    "type" is free-text, not a fixed taxonomy — Jobber's own client tags,
    comma-joined if the client has more than one. None if the client has no
    tags; frontend renders "—".
    """
    clients = {}

    def _entry(client_data):
        client_id = client_data.get('id')
        if not client_id:
            return None
        entry = clients.setdefault(
            client_id,
            {'name': client_data.get('name'), 'job_count': 0, 'revenue': 0.0, 'type': None},
        )
        if not entry.get('name') and client_data.get('name'):
            entry['name'] = client_data.get('name')
        if not entry.get('type'):
            tags_display = _client_tags_display(client_data)
            if tags_display:
                entry['type'] = tags_display
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
            'type': c['type'],
        }
        for c in ranked
    ]


def _service_type_breakdown(job_nodes):
    """
    Tallies each job's _job_service_type() across every job in job_nodes.
    Jobs with no derivable type (no line items, or a freeform line item with
    no linked catalog entry) don't contribute to any bucket. Sorted
    descending by count. Extends the SAME job full-pull _rank_accounts
    already consumes — no second pull.
    """
    counts = {}
    for job in job_nodes:
        service_type = _job_service_type(job)
        if not service_type:
            continue
        counts[service_type] = counts.get(service_type, 0) + 1

    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    return [{'name': name, 'count': count} for name, count in ranked]


class JobberAccountsView(APIView):
    """
    GET /v1/jobber/accounts/
    Computes a full "Top Accounts" ranking (accounts) plus a
    "Jobs by Service Type" tally (service_type_breakdown) for the
    authenticated customer's connected Jobber account. No pagination
    params — this endpoint always computes both, that's the whole point.

    UNCACHED: every request re-pulls every job and every invoice (bounded
    by client.fetch_all_pages's safety cap) and re-ranks from scratch. This
    needs a caching layer (e.g. 15-30 min per tenant) before real customer
    data volume — see PROJECT_CONTEXT.md. Deliberately shipped uncached for
    now; flagging, not deferring silently.
    """
    permission_classes = [CustomerPermission]

    def get(self, request):
        data = {
            'connected': False,
            'accounts': [],
            'service_type_breakdown': [],
            'computed_at': None,
        }
        try:
            account = self._account_for(request.user)
            if account is None:
                return api_response_parser(
                    data=data,
                    message=MESSAGES['JOBBER_NOT_CONNECTED'],
                    status=status.HTTP_200_OK,
                    success=True,
                )

            job_nodes = client.fetch_all_pages(client.fetch_jobs, account, 'fetch_jobs')
            invoice_nodes = client.fetch_all_pages(client.fetch_invoices, account, 'fetch_invoices')

            data = {
                'connected': True,
                'accounts': _rank_accounts(job_nodes, invoice_nodes),
                'service_type_breakdown': _service_type_breakdown(job_nodes),
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

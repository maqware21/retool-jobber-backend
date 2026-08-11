import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.views import APIView

from apps.jobber.models import JobberAccount, JobberInvoice, JobberJob
from apps.jobber.services import client
from apps.jobber.services.sync import ensure_fresh
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

    Phase 2 cutover: now reads from local tables via
    _local_accounts_response() (ensure_fresh(require_complete=True) + local
    tables), not Jobber directly. The original live-proxy body is
    preserved, unused, in _get_live() below for a fast rollback if needed —
    revert by having get() call self._get_live(request) instead of
    _local_accounts_response(). Jobs and Invoices are also cut over;
    Employees is unchanged — still live-proxy, per the current rollout.
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
            data = _local_accounts_response(request.user)
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

    def _get_live(self, request):
        """
        DEAD CODE — deliberately kept, not called from anywhere. This is the
        exact live-proxy body get() used before the Phase 2 cutover above.
        Rollback: make get() call self._get_live(request) again instead of
        _local_accounts_response().
        """
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


# ── Local-table read path (Phase 2) ──────────────────────────────────────────
# Built alongside the live-proxy code above, NOT wired into JobberAccountsView
# yet. See the accompanying compare_accounts.py script.
#
# Structurally different from _rank_accounts()/_service_type_breakdown() by
# necessity, not by choice: those two derive type/service-type fields from
# raw GraphQL node shapes on every call. That derivation already happened
# once, at sync time — JobberClient.tags_display and JobberJob.service_type
# are already the derived values — so this just aggregates over plain
# stored fields, it doesn't re-derive anything from a nested dict shape.

def _rank_local_accounts(tenant_id):
    """Local-table equivalent of _rank_accounts() — same grouping/output shape."""
    clients = {}

    for job in JobberJob.objects.filter(tenant_id=tenant_id, is_active=True).select_related('client'):
        if job.client is None:
            continue
        entry = clients.setdefault(job.client.id, {
            'name': job.client.name,
            'job_count': 0,
            'revenue': 0.0,
            'type': job.client.tags_display,
        })
        entry['job_count'] += 1

    for invoice in JobberInvoice.objects.filter(tenant_id=tenant_id, is_active=True).select_related('client'):
        if invoice.client is None:
            continue
        entry = clients.setdefault(invoice.client.id, {
            'name': invoice.client.name,
            'job_count': 0,
            'revenue': 0.0,
            'type': invoice.client.tags_display,
        })
        entry['revenue'] += float(invoice.amount or 0)

    ranked = sorted(clients.values(), key=lambda c: c['revenue'], reverse=True)
    return [
        {'name': c['name'], 'job_count': c['job_count'], 'revenue': c['revenue'], 'type': c['type']}
        for c in ranked
    ]


def _local_service_type_breakdown(tenant_id):
    """Local-table equivalent of _service_type_breakdown()."""
    counts = {}
    jobs = JobberJob.objects.filter(tenant_id=tenant_id, is_active=True).exclude(service_type__isnull=True).exclude(service_type='')
    for job in jobs:
        counts[job.service_type] = counts.get(job.service_type, 0) + 1

    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    return [{'name': name, 'count': count} for name, count in ranked]


def _local_accounts_response(user):
    """
    Local-table equivalent of JobberAccountsView.get()'s `data` dict. Calls
    ensure_fresh() first (require_complete=True — a ranking over an
    incomplete pull is a wrong answer, not just a stale one, per the design
    doc), then reads local tables — never Jobber directly.
    """
    tenant_id = user.tenant_id
    if not tenant_id:
        return {'connected': False, 'accounts': [], 'service_type_breakdown': [], 'computed_at': None}

    account = JobberAccount.objects.filter(tenant_id=tenant_id, is_active=True).first()
    if account is None:
        return {'connected': False, 'accounts': [], 'service_type_breakdown': [], 'computed_at': None}

    fresh = ensure_fresh(account.tenant, entities=['clients', 'jobs', 'invoices'], require_complete=True)

    data = {
        'connected': True,
        'accounts': _rank_local_accounts(tenant_id),
        'service_type_breakdown': _local_service_type_breakdown(tenant_id),
        'computed_at': timezone.now().isoformat(),
        'last_synced_at': fresh['last_synced_at'].isoformat() if fresh['last_synced_at'] else None,
    }
    if fresh['sync_warning']:
        data['sync_warning'] = fresh['sync_warning']
    return data

import logging

from rest_framework import status
from rest_framework.views import APIView

from apps.jobber.models import JobberAccount
from apps.jobber.services import client
from helpers.api_exception import validator_errors
from helpers.messages import MESSAGES
from helpers.user_permissions import CustomerPermission
from helpers.utils import api_response_parser

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100

# Confirmed against Jobber's own Invoice Status doc + a live query:
# draft/awaiting_payment/paid/past_due/bad_debt/sent_not_due are the only
# real values. No 1:1 "Draft/Pending/Overdue/Paid" mapping exists natively —
# this is our own bucketing, matching what the (pre-existing) UI already
# called these four buckets.
_STATUS_DISPLAY_MAP = {
    'draft': 'Draft',
    'awaiting_payment': 'Pending',
    'sent_not_due': 'Pending',
    'past_due': 'Overdue',
    'paid': 'Paid',
    'bad_debt': 'Overdue',
}


def _status_display(raw_status):
    if not raw_status:
        return ''
    return _STATUS_DISPLAY_MAP.get(raw_status, raw_status.replace('_', ' ').title())


def _format_job_refs(jobs_data):
    """
    "JOB-{n}" comma-joined for every job linked to the invoice, "—" if none.
    Queries jobs(first: 3) on the invoice rather than trusting raw jobIds —
    an invoice can reference zero or multiple jobs.
    """
    nodes = (jobs_data or {}).get('nodes') or []
    numbers = [n.get('jobNumber') for n in nodes if n.get('jobNumber') is not None]
    if not numbers:
        return '—'
    return ', '.join(f"JOB-{n}" for n in numbers)


def _map_invoice(node):
    client_data = node.get('client') or {}
    amounts = node.get('amounts') or {}
    raw_status = node.get('invoiceStatus') or ''
    return {
        'id': f"INV-{node.get('invoiceNumber')}",
        'jobber_id': node.get('id'),
        'customer': client_data.get('name'),
        'job_ids': _format_job_refs(node.get('jobs')),
        'amount': node.get('total'),
        # Total and balance are genuinely distinct in Jobber (a partially
        # paid invoice has balance < total) — surfaced separately, not
        # currently rendered as its own column, but available for later.
        'balance': amounts.get('invoiceBalance'),
        # Raw ISO strings, on purpose — formatting (and the "Not sent yet" /
        # "—" fallback text) happens client-side via formatJobberDate, same
        # convention as the Jobs endpoint's `date` field.
        'issued_date': node.get('issuedDate'),
        'due_date': node.get('dueDate'),
        'status': raw_status,
        'status_display': _status_display(raw_status),
    }


def _compute_summary(invoices):
    """
    Total Billed / Paid / Pending / Overdue — computed over the CURRENTLY
    FETCHED PAGE ONLY, same documented simplification as the Jobs panel's
    client-side pagination (see PROJECT_CONTEXT.md). Not a full-account
    aggregate; will undercount once an account has more invoices than a
    single page fetches.
    """
    def bucket(label):
        return [inv for inv in invoices if inv['status_display'] == label]

    paid = bucket('Paid')
    pending = bucket('Pending')
    overdue = bucket('Overdue')

    return {
        'total_billed': sum(inv['amount'] for inv in invoices),
        'total_billed_count': len(invoices),
        'paid': sum(inv['amount'] for inv in paid),
        'paid_count': len(paid),
        'pending': sum(inv['amount'] for inv in pending),
        'pending_count': len(pending),
        'overdue': sum(inv['amount'] for inv in overdue),
        'overdue_count': len(overdue),
    }


class JobberInvoicesView(APIView):
    """
    GET /v1/jobber/invoices/?first=&after=
    Live-proxies Jobber's `invoices` query for the authenticated customer's
    connected Jobber account. No local caching or sync engine — every call
    hits Jobber fresh (deliberate "live proxy" design, see PROJECT_CONTEXT.md).
    """
    permission_classes = [CustomerPermission]

    def get(self, request):
        data = {'connected': False, 'invoices': [], 'summary': None, 'page_info': None}
        try:
            account = self._account_for(request.user)
            if account is None:
                return api_response_parser(
                    data=data,
                    message=MESSAGES['JOBBER_NOT_CONNECTED'],
                    status=status.HTTP_200_OK,
                    success=True,
                )

            first = self._parse_first(request.query_params.get('first'))
            after = request.query_params.get('after') or None

            raw = client.fetch_invoices(account, first=first, after=after)
            page_info = raw.get('pageInfo') or {}
            invoices = [_map_invoice(node) for node in (raw.get('nodes') or [])]

            data = {
                'connected': True,
                'invoices': invoices,
                'summary': _compute_summary(invoices),
                'page_info': {
                    'has_next_page': bool(page_info.get('hasNextPage')),
                    'end_cursor': page_info.get('endCursor'),
                },
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

    @staticmethod
    def _parse_first(raw_value):
        try:
            n = int(raw_value)
        except (TypeError, ValueError):
            return DEFAULT_PAGE_SIZE
        return max(1, min(n, MAX_PAGE_SIZE))

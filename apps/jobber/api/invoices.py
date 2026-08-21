import logging

from rest_framework import status
from rest_framework.views import APIView

from apps.jobber.models import JobberAccount, JobberInvoice
from apps.jobber.services import client
from apps.jobber.services.sync import ensure_fresh
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


def _safe_int(value):
    """
    Jobber returns invoiceNumber as a STRING (confirmed live: "1", not 1),
    unlike Job.jobNumber which is already an Int. Falls back to None (not 0)
    on anything unparseable so a sort never silently treats it as smallest.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
        # Genuine numeric field for sorting — "id" is a display string
        # ("INV-10"), and a plain string/locale sort on it puts "INV-10"
        # before "INV-9" once an account passes 9 invoices. The frontend
        # must sort on this field, never on "id".
        'invoice_number': _safe_int(node.get('invoiceNumber')),
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

    Draft invoices are excluded from total_billed (and its count) — a
    draft hasn't been sent yet, so it isn't "billed." Drafts still appear
    in the invoice list itself; this only changes the summary card math.
    Resolves the previously-open "does Draft count toward Total Billed"
    question per TL decision.
    """
    def bucket(label):
        return [inv for inv in invoices if inv['status_display'] == label]

    paid = bucket('Paid')
    pending = bucket('Pending')
    overdue = bucket('Overdue')
    billed = [inv for inv in invoices if inv['status_display'] != 'Draft']

    return {
        'total_billed': sum(inv['amount'] for inv in billed),
        'total_billed_count': len(billed),
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

    Phase 2 cutover: now reads from local tables via
    _local_invoices_response() (ensure_fresh() + local tables), not Jobber
    directly. The original live-proxy body is preserved, unused, in
    _get_live() below for a fast rollback if needed — revert by having
    get() call self._get_live(request) instead of _local_invoices_response().
    Jobs is also cut over; Accounts/Employees are unchanged — still
    live-proxy, per the current rollout.
    """
    permission_classes = [CustomerPermission]

    def get(self, request):
        data = {'connected': False, 'invoices': [], 'summary': None, 'page_info': None}
        try:
            first = self._parse_first(request.query_params.get('first'))
            after = request.query_params.get('after') or None

            data = _local_invoices_response(request.user, first=first, after=after)
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

    def _get_live(self, request):
        """
        DEAD CODE — deliberately kept, not called from anywhere. This is the
        exact live-proxy body get() used before the Phase 2 cutover above.
        Rollback: make get() call self._get_live(request) again instead of
        _local_invoices_response().
        """
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


# ── Local-table read path (Phase 2) ──────────────────────────────────────────
# Built alongside the live-proxy code above, NOT wired into JobberInvoicesView
# yet. Confirmed via a side-by-side comparison against the live-proxy output.

def _isoformat(value):
    return value.isoformat() if value else None


def _local_format_job_refs(invoice):
    """Local equivalent of _format_job_refs() — "JOB-{n}" comma-joined, "—" if none."""
    numbers = [j.job_number for j in invoice.jobs.filter(is_active=True) if j.job_number is not None]
    if not numbers:
        return '—'
    return ', '.join(f"JOB-{n}" for n in numbers)


def _map_local_invoice(invoice):
    """Local-table equivalent of _map_invoice() — same keys, same field types."""
    return {
        'id': f"INV-{invoice.invoice_number}",
        'jobber_id': invoice.jobber_id,
        'invoice_number': invoice.invoice_number,
        'customer': invoice.client.name if invoice.client else None,
        'job_ids': _local_format_job_refs(invoice),
        'amount': float(invoice.amount) if invoice.amount is not None else None,
        'balance': float(invoice.balance) if invoice.balance is not None else None,
        'issued_date': _isoformat(invoice.issued_date),
        'due_date': _isoformat(invoice.due_date),
        'status': invoice.invoice_status,
        'status_display': invoice.status_display,
    }


def _local_invoices_response(user, first=DEFAULT_PAGE_SIZE, after=None):
    """
    Local-table equivalent of JobberInvoicesView.get()'s `data` dict. Calls
    ensure_fresh() first, then reads local tables — never Jobber directly.

    Same OFFSET-based local pagination caveat as jobs.py's
    _local_jobs_response(). The summary is deliberately still computed over
    just this page (matching _compute_summary()'s existing page-only
    limitation exactly) rather than upgraded to a full-account aggregate —
    that upgrade is real and available once local tables are the source of
    truth, but doing it here would make the live and local outputs diverge
    in a way that looks like a bug during this round's side-by-side
    comparison, when it would actually just be an intentional improvement
    saved for the cutover step.
    """
    tenant_id = user.tenant_id
    if not tenant_id:
        return {'connected': False, 'invoices': [], 'summary': None, 'page_info': None}

    account = JobberAccount.objects.filter(tenant_id=tenant_id, is_active=True).first()
    if account is None:
        return {'connected': False, 'invoices': [], 'summary': None, 'page_info': None}

    fresh = ensure_fresh(account.tenant, entities=['clients', 'jobs', 'invoices'])

    queryset = JobberInvoice.objects.filter(tenant_id=tenant_id, is_active=True).select_related('client').order_by('id')
    offset = int(after) if after else 0
    page = list(queryset[offset:offset + first])
    has_next_page = queryset.count() > offset + first
    end_cursor = str(offset + first) if has_next_page else None

    invoices = [_map_local_invoice(inv) for inv in page]

    data = {
        'connected': True,
        'invoices': invoices,
        'summary': _compute_summary(invoices),
        'page_info': {
            'has_next_page': has_next_page,
            'end_cursor': end_cursor,
        },
        'last_synced_at': _isoformat(fresh['last_synced_at']),
    }
    if fresh['sync_warning']:
        data['sync_warning'] = fresh['sync_warning']
    return data

import logging

from rest_framework import status
from rest_framework.views import APIView

from apps.jobber.models import JobberAccount, JobberJob
from apps.jobber.services import client
from apps.jobber.services.sync import ensure_fresh
from helpers.api_exception import validator_errors
from helpers.messages import MESSAGES
from helpers.user_permissions import CustomerPermission
from helpers.utils import api_response_parser

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


def _humanize_status(raw_status):
    """'requires_invoicing' -> 'Requires Invoicing'"""
    if not raw_status:
        return ''
    return raw_status.replace('_', ' ').title()


def _format_address(property_data):
    """Join the non-empty parts of a Jobber Property into one display string."""
    if not property_data:
        return None
    parts = [
        property_data.get('street'),
        property_data.get('city'),
        property_data.get('province'),
        property_data.get('postalCode'),
    ]
    parts = [p for p in parts if p]
    return ', '.join(parts) if parts else None


def _first_assignee(job_node):
    """
    The name of the first assigned user on the job's first returned visit.
    "First" is whichever visit Jobber returns first — not independently
    verified as chronologically first. Acceptable simplification for now.
    """
    visits = (job_node.get('visits') or {}).get('nodes') or []
    if not visits:
        return 'Unassigned'
    assigned = (visits[0].get('assignedUsers') or {}).get('nodes') or []
    if not assigned:
        return 'Unassigned'
    name = assigned[0].get('name') or {}
    return name.get('full') or 'Unassigned'


def _job_service_type(job_node):
    """
    The job's first line item's linked product/service name, or None if the
    job has no line items or used a freeform line item (no linked catalog
    entry). Free-text, not a fixed taxonomy — whatever the account named
    their catalog entries. Same derivation Accounts' service_type_breakdown
    tally uses (duplicated there rather than cross-imported, consistent with
    every other api/*.py module in this app).
    """
    line_items = (job_node.get('lineItems') or {}).get('nodes') or []
    if not line_items:
        return None
    linked = line_items[0].get('linkedProductOrService')
    if not linked:
        return None
    return linked.get('name')


def _map_job(node):
    client_data = node.get('client') or {}
    raw_status = node.get('jobStatus') or ''
    return {
        'id': f"JOB-{node.get('jobNumber')}",
        'jobber_id': node.get('id'),
        # Genuine numeric field for sorting — "id" is a display string
        # ("JOB-10"), and a plain string/locale sort on it puts "JOB-10"
        # before "JOB-9" once an account passes 9 jobs. The frontend must
        # sort on this field, never on "id". (Job.jobNumber is already a
        # real int from Jobber, unlike Invoice.invoiceNumber which is a
        # string — no parsing needed here.)
        'job_number': node.get('jobNumber'),
        'customer': client_data.get('name'),
        # Free-text, from the job's first line item's linked catalog entry —
        # not a fixed taxonomy (Jobber has none). None if the job has no
        # line items or used a freeform one; frontend renders "—".
        'type': _job_service_type(node),
        'description': node.get('instructions') or node.get('title') or '',
        'assigned_to': _first_assignee(node),
        'status': raw_status,
        'status_display': _humanize_status(raw_status),
        'value': node.get('total'),
        # Raw ISO string, on purpose — formatting happens client-side against
        # the viewer's local timezone (see PROJECT_CONTEXT.md timezone note).
        'date': node.get('startAt') or node.get('createdAt'),
        'address': _format_address(node.get('property')),
    }


class JobberJobsView(APIView):
    """
    GET /v1/jobber/jobs/?first=&after=

    Phase 2 cutover: now reads from local tables via _local_jobs_response()
    (ensure_fresh() + local tables), not Jobber directly. The original
    live-proxy body is preserved, unused, in _get_live() below for a fast
    rollback if needed — revert by having get() call self._get_live(request)
    instead of _local_jobs_response(). Invoices/Accounts/Employees are
    unchanged — still live-proxy, per the current comparison-only rollout.
    """
    permission_classes = [CustomerPermission]

    def get(self, request):
        data = {'connected': False, 'jobs': [], 'page_info': None}
        try:
            first = self._parse_first(request.query_params.get('first'))
            after = request.query_params.get('after') or None

            data = _local_jobs_response(request.user, first=first, after=after)
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
        _local_jobs_response().
        """
        data = {'connected': False, 'jobs': [], 'page_info': None}
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

            raw = client.fetch_jobs(account, first=first, after=after)
            page_info = raw.get('pageInfo') or {}

            data = {
                'connected': True,
                'jobs': [_map_job(node) for node in (raw.get('nodes') or [])],
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
# Built alongside the live-proxy code above, NOT wired into JobberJobsView
# yet — JobberJobsView.get() still calls client.fetch_jobs()/_map_job()
# exactly as it does today. This exists to compare the two paths, which
# was confirmed to produce matching output against the live-proxy version,
# before anything gets swapped.

def _isoformat(value):
    return value.isoformat() if value else None


def _local_first_assignee(job):
    """
    Local equivalent of _first_assignee():the assigned_user_name of this
    job's first Visit (by local insertion order — JobberVisit has no
    explicit ordinal field, so this is "whichever visit was synced first,"
    an analog to live's "whichever visit Jobber returns first," not a
    byte-for-byte reproduction of it), falling back to 'Unassigned'.
    """
    visit = job.visits.filter(is_active=True).order_by('id').first()
    if visit is None:
        return 'Unassigned'
    return visit.assigned_user_name


def _map_local_job(job):
    """Local-table equivalent of _map_job() — same keys, same field types."""
    return {
        'id': f"JOB-{job.job_number}",
        'jobber_id': job.jobber_id,
        'job_number': job.job_number,
        'customer': job.client.name if job.client else None,
        'type': job.service_type,
        'description': job.description or '',
        'assigned_to': _local_first_assignee(job),
        'status': job.job_status,
        'status_display': job.status_display,
        'value': float(job.total) if job.total is not None else None,
        # ISO string, matching the live field's type — not guaranteed to be
        # byte-identical to Jobber's own raw string (e.g. "Z" vs "+00:00"
        # timezone suffix), but both are valid ISO8601 and parse identically
        # client-side.
        'date': _isoformat(job.start_at or job.jobber_created_at),
        'address': job.address,
    }


def _local_jobs_response(user, first=DEFAULT_PAGE_SIZE, after=None):
    """
    Local-table equivalent of JobberJobsView.get()'s `data` dict. Calls
    ensure_fresh() first, then reads local tables — never Jobber directly.

    Pagination is OFFSET-based here, not Jobber's own cursor — `after` is a
    local, opaque numeric-string offset (matching the field's String type,
    not its original cursor semantics). Fine for comparing outputs now;
    would need real thought before this ever backs paginated production UI.
    """
    tenant_id = user.tenant_id
    if not tenant_id:
        return {'connected': False, 'jobs': [], 'page_info': None}

    account = JobberAccount.objects.filter(tenant_id=tenant_id, is_active=True).first()
    if account is None:
        return {'connected': False, 'jobs': [], 'page_info': None}

    fresh = ensure_fresh(account.tenant, entities=['clients', 'users', 'jobs', 'visits'])

    queryset = JobberJob.objects.filter(tenant_id=tenant_id, is_active=True).select_related('client').order_by('id')
    offset = int(after) if after else 0
    page = list(queryset[offset:offset + first])
    has_next_page = queryset.count() > offset + first
    end_cursor = str(offset + first) if has_next_page else None

    data = {
        'connected': True,
        'jobs': [_map_local_job(job) for job in page],
        'page_info': {
            'has_next_page': has_next_page,
            'end_cursor': end_cursor,
        },
        'last_synced_at': _isoformat(fresh['last_synced_at']),
    }
    if fresh['sync_warning']:
        data['sync_warning'] = fresh['sync_warning']
    return data

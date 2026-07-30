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


def _map_job(node):
    client_data = node.get('client') or {}
    raw_status = node.get('jobStatus') or ''
    return {
        'id': f"JOB-{node.get('jobNumber')}",
        'jobber_id': node.get('id'),
        'customer': client_data.get('name'),
        # Confirmed dead end against a live query: Jobber has no trade/service
        # category taxonomy anywhere reachable from Job. Always null — the
        # frontend renders this as "—", not hidden.
        'type': None,
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
    Live-proxies Jobber's `jobs` query for the authenticated customer's
    connected Jobber account. No local caching or sync engine — every call
    hits Jobber fresh (deliberate "live proxy" design, see PROJECT_CONTEXT.md).
    """
    permission_classes = [CustomerPermission]

    def get(self, request):
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

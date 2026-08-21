import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.views import APIView

from apps.jobber.models import JobberAccount, JobberJob, JobberUser
from apps.jobber.services import client
from apps.jobber.services.sync import ensure_fresh
from helpers.api_exception import validator_errors
from helpers.messages import MESSAGES
from helpers.user_permissions import CustomerPermission
from helpers.utils import api_response_parser

logger = logging.getLogger(__name__)


def _humanize_status(raw_status):
    """'requires_invoicing' -> 'Requires Invoicing'"""
    if not raw_status:
        return ''
    return raw_status.replace('_', ' ').title()


def _rank_employees(job_nodes, user_nodes):
    """
    A basic roster with job counts — NOT the full Electricians performance
    panel (goals/ratings/drive-time/callback remain blocked, see
    PROJECT_CONTEXT.md).

    Seeds every real user first, so employees with zero assigned jobs still
    appear with job_count: 0. Then walks each job's first-visit
    assignedUsers (whatever fetch_jobs's existing query already returns —
    no new jobs query written) and credits every unique assignee on that
    job once. A job with multiple assignees on its visit credits all of
    them; the same job is never double-counted for the same employee.
    Sorted descending by job_count.
    """
    employees = {}

    for user in user_nodes:
        user_id = user.get('id')
        if not user_id:
            continue
        employees[user_id] = {
            'name': (user.get('name') or {}).get('full'),
            'job_count': 0,
            'jobs': [],
        }

    for job in job_nodes:
        visits = (job.get('visits') or {}).get('nodes') or []
        credited_for_this_job = set()
        for visit in visits:
            assigned = (visit.get('assignedUsers') or {}).get('nodes') or []
            for user in assigned:
                user_id = user.get('id')
                if not user_id or user_id in credited_for_this_job:
                    continue
                credited_for_this_job.add(user_id)
                entry = employees.setdefault(user_id, {
                    'name': (user.get('name') or {}).get('full'),
                    'job_count': 0,
                    'jobs': [],
                })
                entry['job_count'] += 1
                entry['jobs'].append({
                    'jobber_id': job.get('id'),
                    'title': job.get('title') or '',
                    'status_display': _humanize_status(job.get('jobStatus') or ''),
                })

    return sorted(employees.values(), key=lambda e: e['job_count'], reverse=True)


class JobberEmployeesView(APIView):
    """
    GET /v1/jobber/employees/
    A basic roster with job counts for the authenticated customer's
    connected Jobber account. Pulls the full job set (reusing fetch_jobs —
    no new jobs query) and the full user roster once via
    client.fetch_all_pages, groups jobs by each job's first-visit
    assignedUsers, merges so zero-job employees still appear.

    UNCACHED, same caveat as Accounts — every request re-pulls and
    re-groups from scratch. See PROJECT_CONTEXT.md.
    """
    permission_classes = [CustomerPermission]

    def get(self, request):
        data = {'connected': False, 'employees': [], 'computed_at': None}
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
            user_nodes = client.fetch_all_pages(client.fetch_users, account, 'fetch_users')

            data = {
                'connected': True,
                'employees': _rank_employees(job_nodes, user_nodes),
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


# ── Local-table read path (Phase 2) ──────────────────────────────────────────
# Built alongside the live-proxy code above, NOT wired into
# JobberEmployeesView yet. Confirmed via a side-by-side comparison against
# the live-proxy output.

def _rank_local_employees(tenant_id):
    """
    Local-table equivalent of _rank_employees(). Walks ALL of a job's
    Visits (not just the first — matching live's actual behavior, which
    walks every visit and credits every unique assignee once per job), but
    each local Visit only ever has ONE assigned_user stored (see
    sync.py's sync_visits() — a known, already-flagged gap from Step 2:
    JobberVisit.assigned_user is a single nullable FK, not an M2M). So a
    Jobber Visit that had multiple assignedUsers only ever contributes its
    first one locally — this ranking will undercount those cases relative
    to the live version. Expected, not a new bug; flagged again here since
    it's directly relevant to reading the comparison script's output.
    """
    employees = {}

    for user in JobberUser.objects.filter(tenant_id=tenant_id, is_active=True):
        employees[user.id] = {'name': user.name, 'job_count': 0, 'jobs': []}

    jobs = JobberJob.objects.filter(tenant_id=tenant_id, is_active=True).prefetch_related('visits__assigned_user')
    for job in jobs:
        credited_for_this_job = set()
        for visit in job.visits.filter(is_active=True):
            user = visit.assigned_user
            if user is None or user.id in credited_for_this_job:
                continue
            credited_for_this_job.add(user.id)
            entry = employees.setdefault(user.id, {'name': user.name, 'job_count': 0, 'jobs': []})
            entry['job_count'] += 1
            entry['jobs'].append({
                'jobber_id': job.jobber_id,
                'title': job.title or '',
                'status_display': job.status_display,
            })

    return sorted(employees.values(), key=lambda e: e['job_count'], reverse=True)


def _local_employees_response(user):
    """
    Local-table equivalent of JobberEmployeesView.get()'s `data` dict. Calls
    ensure_fresh() first (require_complete=True — same "a ranking over an
    incomplete pull is a wrong answer" reasoning as Accounts), then reads
    local tables — never Jobber directly.
    """
    tenant_id = user.tenant_id
    if not tenant_id:
        return {'connected': False, 'employees': [], 'computed_at': None}

    account = JobberAccount.objects.filter(tenant_id=tenant_id, is_active=True).first()
    if account is None:
        return {'connected': False, 'employees': [], 'computed_at': None}

    fresh = ensure_fresh(account.tenant, entities=['users', 'jobs', 'visits'], require_complete=True)

    data = {
        'connected': True,
        'employees': _rank_local_employees(tenant_id),
        'computed_at': timezone.now().isoformat(),
        'last_synced_at': fresh['last_synced_at'].isoformat() if fresh['last_synced_at'] else None,
    }
    if fresh['sync_warning']:
        data['sync_warning'] = fresh['sync_warning']
    return data

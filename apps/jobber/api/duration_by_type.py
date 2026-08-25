import logging
from collections import defaultdict

from rest_framework import status
from rest_framework.views import APIView

from apps.jobber.api.electricians_summary import _gather_job_assignees, calculate_job_duration_by_user
from apps.jobber.models import JobberAccount, JobberJob
from apps.jobber.services.sync import ensure_fresh
from helpers.api_exception import validator_errors
from helpers.messages import MESSAGES
from helpers.user_permissions import CustomerPermission
from helpers.utils import api_response_parser

logger = logging.getLogger(__name__)

# Confirmed rule (2026-08-25, matches verify_service_type_distribution.py
# exactly) -- a service_type only qualifies for this chart when it has
# real data to compare: at least this many jobs, spanning at least this
# many distinct technicians. Computed fresh on every call, never a
# hardcoded list of "today's" qualifying types -- as real job history
# accumulates, types move in and out of qualifying on their own.
MIN_JOBS_PER_TYPE = 2
MIN_TECHNICIANS_PER_TYPE = 2

_NOT_CONNECTED_DATA = {
    'connected': False,
    'types': [],
}


def _local_duration_by_type_response(tenant):
    """
    Real per-technician average job duration, grouped by service_type,
    for types with enough real data to support a genuine comparison --
    backs the Electricians panel's "Avg Job Duration by Type &
    Technician" chart.

    Nested per-type shape ({'types': [{'service_type', 'job_count',
    'technician_count', 'technicians': [...]}]}), NOT flat rows --
    matches DurationDotPlotRow's own real prop shape directly
    ({jobType, entries: [{name, hours}]}), unlike Monthly Revenue's flat
    rows (which fed a Recharts pivot table, a genuinely different
    consumer with a genuinely different natural shape). See
    duration_by_type_proposal.md for the full reasoning.

    Population: ALL active jobs, UNWINDOWED -- deliberately NOT the same
    6-month rolling window every other real metric on this page uses.
    Confirmed decision (2026-08-25): this is the EXACT SAME population
    verify_service_type_distribution.py already queried and got the
    real "2 types qualify" result against; using a different (windowed)
    population here would silently disagree with that already-verified
    finding. Also a substantive reason beyond consistency: this
    computation is inherently data-scarcity-sensitive, and narrowing it
    to a rolling window would only shrink an already-thin dataset
    further, working against the one thing this feature needs (enough
    real jobs to compare).

    Duration source: calculate_job_duration_by_user(job) -- the PER-USER
    breakdown, NOT calculate_job_duration_seconds(job) (the job-TOTAL).
    Confirmed decision (2026-08-25): for a job with a single assignee
    these are identical, but for a qualifying-type job with MULTIPLE
    assignees, crediting the job's full total to every assignee would
    double-count a shared job's duration into more than one person's
    per-type average. calculate_job_duration_by_user() is the same
    function the already-shipped real per-technician "Avg Job Duration"
    stat (_accumulate_technician_job_stats() in technician_stats.py)
    already uses for exactly this reason -- each assignee credited only
    their own tracked seconds on that job.

    Exclusion is per-job, never per technician-type pair wholesale: if a
    technician has 3 jobs of a qualifying type and 1 has no real
    timesheet entries, that ONE job is excluded from their average --
    never treated as 0 -- but their average still comes from the other
    2. A technician is only left off a type's technicians list entirely
    if NONE of their jobs of that type have real logged time. A type can
    also still be dropped AFTER this per-job exclusion if fewer than
    MIN_TECHNICIANS_PER_TYPE technicians end up with any real average at
    all (e.g. one of the two originally-assigned technicians never
    logged real time on any job of that type) -- the initial
    2+-jobs/2+-technicians check is based on ASSIGNMENT, not on having
    real duration data, so this second check is a real, distinct
    degenerate case worth guarding separately, not redundant with the
    first.
    """
    if tenant is None:
        return dict(_NOT_CONNECTED_DATA)
    tenant_id = tenant.id

    account = JobberAccount.objects.filter(tenant_id=tenant_id, is_active=True).first()
    if account is None:
        return dict(_NOT_CONNECTED_DATA)

    # require_complete=True -- same reasoning as every other aggregate
    # endpoint here: a duration average computed over a partially-synced
    # entity set is a WRONG number, not just a stale one.
    fresh = ensure_fresh(account.tenant, entities=['jobs', 'visits', 'timesheet_entries'], require_complete=True)

    all_jobs = list(JobberJob.objects.filter(tenant_id=tenant_id, is_active=True))

    by_type = defaultdict(list)
    for job in all_jobs:
        if job.service_type:
            by_type[job.service_type].append(job)

    types_data = []
    for service_type, jobs in by_type.items():
        # Gathered once per job, reused below for both the qualifying
        # check and the per-technician accumulation -- not re-derived.
        job_assignees = {job.id: _gather_job_assignees(job) for job in jobs}

        distinct_tech_ids = set()
        for assignees in job_assignees.values():
            distinct_tech_ids.update(assignees.keys())

        if len(jobs) < MIN_JOBS_PER_TYPE or len(distinct_tech_ids) < MIN_TECHNICIANS_PER_TYPE:
            continue  # not enough real data to compare -- excluded entirely, not a single sad dot

        per_tech_seconds = defaultdict(list)
        per_tech_name = {}
        for job in jobs:
            assignees = job_assignees[job.id]
            if not assignees:
                continue
            seconds_by_user = calculate_job_duration_by_user(job)
            for user_id, user in assignees.items():
                per_tech_name[user_id] = user.name
                seconds = seconds_by_user.get(user_id)
                if not seconds:
                    continue  # no real timesheet entries for THIS job -- excluded, never 0
                per_tech_seconds[user_id].append(seconds)

        technicians = [
            {
                'user_id': user_id,
                'name': per_tech_name[user_id],
                'avg_duration_seconds': round(sum(seconds_list) / len(seconds_list)),
            }
            for user_id, seconds_list in per_tech_seconds.items()
        ]
        technicians.sort(key=lambda t: t['name'])

        if len(technicians) < MIN_TECHNICIANS_PER_TYPE:
            continue  # after excluding null-duration jobs, too few technicians have a REAL average left

        types_data.append({
            'service_type': service_type,
            'job_count': len(jobs),
            'technician_count': len(technicians),
            'technicians': technicians,
        })

    types_data.sort(key=lambda t: t['service_type'])

    data = {
        'connected': True,
        'types': types_data,
        'last_synced_at': fresh['last_synced_at'].isoformat() if fresh['last_synced_at'] else None,
    }
    if fresh['sync_warning']:
        data['sync_warning'] = fresh['sync_warning']
    return data


class JobberDurationByTypeView(APIView):
    """
    GET /v1/jobber/duration-by-type/
    Real per-technician average job duration, grouped by service_type,
    for types with enough real data to support a genuine comparison --
    backs the Electricians panel's "Avg Job Duration by Type &
    Technician" chart. Reads local tables only via ensure_fresh() --
    never calls Jobber directly. A dedicated endpoint, not a field on
    electricians-summary -- see duration_by_type_proposal.md for why.
    """
    permission_classes = [CustomerPermission]

    def get(self, request):
        data = dict(_NOT_CONNECTED_DATA)
        try:
            data = _local_duration_by_type_response(request.user.tenant)
            return api_response_parser(
                data=data,
                message=MESSAGES['SUCCESS'],
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)

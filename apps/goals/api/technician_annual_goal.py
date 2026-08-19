from rest_framework import status
from rest_framework.views import APIView

from apps.goals.models import TechnicianAnnualGoal
from apps.goals.serializers.technician_annual_goal import (
    TechnicianAnnualGoalRowSerializer,
    TechnicianAnnualGoalWriteSerializer,
)
from apps.goals.utils import current_year, parse_year
from apps.jobber.models import JobberUser
from helpers.api_exception import validator_errors
from helpers.messages import MESSAGES
from helpers.user_permissions import CustomerPermission
from helpers.utils import api_response_parser


class TechnicianAnnualGoalView(APIView):
    """
    GET  /v1/goals/technicians/annual/?year=YYYY
        Every technician in the already-synced JobberUser roster, each
        with their ANNUAL goal for that year (null if unset). Defaults to
        the current year. A separate endpoint from TechnicianGoalView
        (monthly) rather than a query-param mode on it -- same reasoning
        as TeamAnnualGoalView.

    POST /v1/goals/technicians/annual/
        {"user": <JobberUser id>, "year": "YYYY", "goal_amount": "..."}
        Creates or updates (upsert) that one technician's annual goal.

    This is OUR OWN data, entered directly by the customer -- NOT synced
    from Jobber, NOT derived from TechnicianGoal (monthly x 12 was
    explicitly rejected by TL). Plain CRUD; no sync_tenant()/
    ensure_fresh() involvement. Exact mirror of TechnicianGoalView.
    """
    permission_classes = [CustomerPermission]

    def get(self, request):
        data = []
        try:
            tenant_id = request.user.tenant_id
            year_param = request.query_params.get('year')
            year = parse_year(year_param) if year_param else current_year()

            users = JobberUser.objects.filter(tenant_id=tenant_id, is_active=True).order_by('name')
            goals_by_user_id = {
                goal.user_id: goal.goal_amount
                for goal in TechnicianAnnualGoal.fetch(tenant_id=tenant_id, year=year)
            }
            rows = [
                {
                    'user_id': user.id,
                    'name': user.name,
                    'goal_amount': goals_by_user_id.get(user.id),
                }
                for user in users
            ]
            data = TechnicianAnnualGoalRowSerializer(rows, many=True).data
            return api_response_parser(
                data=data,
                message=MESSAGES['SUCCESS'],
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)

    def post(self, request):
        data = {}
        try:
            tenant_id = request.user.tenant_id
            year = parse_year(request.data.get('year'))
            user_id = request.data.get('user')

            # Same guard as TechnicianGoalView.post() -- only attempt the
            # upsert lookup when a user was actually given, else let the
            # serializer's own "this field is required" error surface it.
            existing = None
            if user_id is not None:
                existing = TechnicianAnnualGoal.fetch(tenant_id=tenant_id, user_id=user_id, year=year)

            serializer = TechnicianAnnualGoalWriteSerializer(
                instance=existing, data=request.data, context={'tenant_id': tenant_id},
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()

            data = serializer.data
            message = MESSAGES['UPDATED' if existing else 'CREATED'].format('Technician annual goal')
            return api_response_parser(
                data=data,
                message=message,
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)

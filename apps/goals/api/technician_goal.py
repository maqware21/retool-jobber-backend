from rest_framework import status
from rest_framework.views import APIView

from apps.goals.models import TechnicianGoal
from apps.goals.serializers.technician_goal import (
    TechnicianGoalRowSerializer,
    TechnicianGoalWriteSerializer,
)
from apps.goals.utils import current_month, parse_month
from apps.jobber.models import JobberUser
from helpers.api_exception import validator_errors
from helpers.messages import MESSAGES
from helpers.user_permissions import CustomerPermission
from helpers.utils import api_response_parser


class TechnicianGoalView(APIView):
    """
    GET  /v1/goals/technicians/?month=YYYY-MM
        Every technician in the already-synced JobberUser roster (same
        roster JobberEmployeesView already reuses for a different shape --
        not a separate technician list), each with their goal for that
        month (null if unset). Defaults to the current month.

    POST /v1/goals/technicians/
        {"user": <JobberUser id>, "month": "YYYY-MM", "goal_amount": "..."}
        Creates or updates (upsert) that one technician's goal for that
        month.

    This is OUR OWN data, entered directly by the customer -- NOT synced
    from Jobber. Plain CRUD; no sync_tenant()/ensure_fresh() involvement.
    """
    permission_classes = [CustomerPermission]

    def get(self, request):
        data = []
        try:
            tenant_id = request.user.tenant_id
            month_param = request.query_params.get('month')
            month = parse_month(month_param) if month_param else current_month()

            users = JobberUser.objects.filter(tenant_id=tenant_id, is_active=True).order_by('name')
            goals_by_user_id = {
                goal.user_id: goal.goal_amount
                for goal in TechnicianGoal.fetch(tenant_id=tenant_id, month=month)
            }
            rows = [
                {
                    'user_id': user.id,
                    'name': user.name,
                    'goal_amount': goals_by_user_id.get(user.id),
                }
                for user in users
            ]
            data = TechnicianGoalRowSerializer(rows, many=True).data
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
            month = parse_month(request.data.get('month'))
            user_id = request.data.get('user')

            # Only attempt the upsert lookup when a user was actually given --
            # fetch(user_id=None, ...) would otherwise fall through to its
            # "narrower call" branch and return a queryset instead of a
            # single instance/None, which is not a valid `instance=` for the
            # serializer below. A missing `user` should surface as the
            # serializer's own "this field is required" error, not a crash.
            existing = None
            if user_id is not None:
                existing = TechnicianGoal.fetch(tenant_id=tenant_id, user_id=user_id, month=month)

            serializer = TechnicianGoalWriteSerializer(
                instance=existing, data=request.data, context={'tenant_id': tenant_id},
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()

            data = serializer.data
            message = MESSAGES['UPDATED' if existing else 'CREATED'].format('Technician goal')
            return api_response_parser(
                data=data,
                message=message,
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)

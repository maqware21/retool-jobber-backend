from rest_framework import status
from rest_framework.views import APIView

from apps.goals.models import TeamGoal
from apps.goals.serializers.team_goal import TeamGoalSerializer
from apps.goals.utils import current_month, parse_month
from helpers.api_exception import validator_errors
from helpers.messages import MESSAGES
from helpers.user_permissions import CustomerPermission
from helpers.utils import api_response_parser


class TeamGoalView(APIView):
    """
    GET  /v1/goals/team/?month=YYYY-MM
        The whole-team monthly goal for that month (defaults to the
        current month), or null if none has been set yet. Never
        auto-creates a row.

    POST /v1/goals/team/
        {"month": "YYYY-MM", "goal_amount": "..."}
        Creates or updates (upsert) that month's team goal.

    This is OUR OWN data, entered directly by the customer -- NOT synced
    from Jobber. Plain CRUD; no sync_tenant()/ensure_fresh() involvement.
    """
    permission_classes = [CustomerPermission]

    def get(self, request):
        data = None
        try:
            month_param = request.query_params.get('month')
            month = parse_month(month_param) if month_param else current_month()

            instance = TeamGoal.fetch(tenant_id=request.user.tenant_id, month=month)
            data = TeamGoalSerializer(instance).data if instance else None
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

            existing = TeamGoal.fetch(tenant_id=tenant_id, month=month)
            serializer = TeamGoalSerializer(
                instance=existing, data=request.data, context={'tenant_id': tenant_id},
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()

            data = serializer.data
            message = MESSAGES['UPDATED' if existing else 'CREATED'].format('Team goal')
            return api_response_parser(
                data=data,
                message=message,
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)

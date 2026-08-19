from rest_framework import status
from rest_framework.views import APIView

from apps.goals.models import TeamAnnualGoal
from apps.goals.serializers.team_annual_goal import TeamAnnualGoalSerializer
from apps.goals.utils import current_year, parse_year
from helpers.api_exception import validator_errors
from helpers.messages import MESSAGES
from helpers.user_permissions import CustomerPermission
from helpers.utils import api_response_parser


class TeamAnnualGoalView(APIView):
    """
    GET  /v1/goals/team/annual/?year=YYYY
        The whole-team ANNUAL goal for that year (defaults to the current
        year), or null if none has been set yet. Never auto-creates a
        row. A separate endpoint from TeamGoalView (monthly) rather than
        a query-param mode on it -- that view is already live/shipped;
        retrofitting it risked regressing working code for no benefit.

    POST /v1/goals/team/annual/
        {"year": "YYYY", "goal_amount": "..."}
        Creates or updates (upsert) that year's team goal.

    This is OUR OWN data, entered directly by the customer -- NOT synced
    from Jobber, NOT derived from TeamGoal (monthly x 12 was explicitly
    rejected by TL -- this is a genuinely independent figure). Plain
    CRUD; no sync_tenant()/ensure_fresh() involvement. Exact mirror of
    TeamGoalView's shape.
    """
    permission_classes = [CustomerPermission]

    def get(self, request):
        data = None
        try:
            year_param = request.query_params.get('year')
            year = parse_year(year_param) if year_param else current_year()

            instance = TeamAnnualGoal.fetch(tenant_id=request.user.tenant_id, year=year)
            data = TeamAnnualGoalSerializer(instance).data if instance else None
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

            existing = TeamAnnualGoal.fetch(tenant_id=tenant_id, year=year)
            serializer = TeamAnnualGoalSerializer(
                instance=existing, data=request.data, context={'tenant_id': tenant_id},
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()

            data = serializer.data
            message = MESSAGES['UPDATED' if existing else 'CREATED'].format('Team annual goal')
            return api_response_parser(
                data=data,
                message=message,
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)

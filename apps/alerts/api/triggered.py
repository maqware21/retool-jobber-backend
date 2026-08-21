import logging

from rest_framework import status
from rest_framework.views import APIView

from apps.alerts.services.evaluate import evaluate_alert_rules
from helpers.api_exception import validator_errors
from helpers.messages import MESSAGES
from helpers.user_permissions import CustomerPermission
from helpers.utils import api_response_parser

logger = logging.getLogger(__name__)


class AlertsTriggeredView(APIView):
    """
    GET /v1/alerts/triggered/
    Every currently-triggered alert for the customer's tenant, evaluated
    against the same real per-technician numbers already shown on the
    Electricians panel's cards (see evaluate_alert_rules()). Backs both
    AlarmPanel (company-wide "Needs Attention" list) and each technician
    card's alertCount on the frontend -- one fetch, not a call per card.
    """
    permission_classes = [CustomerPermission]

    def get(self, request):
        data = []
        try:
            data = evaluate_alert_rules(request.user.tenant)
            return api_response_parser(
                data=data,
                message=MESSAGES['SUCCESS'],
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)

import logging

from rest_framework import status
from rest_framework.views import APIView

from apps.alerts.models import AlertRule
from apps.alerts.serializers.alert_rule import AlertRuleSerializer
from helpers.api_exception import validator_errors
from helpers.constants import ALERT_RULE_TYPES
from helpers.messages import MESSAGES
from helpers.user_permissions import CustomerPermission
from helpers.utils import api_response_parser

logger = logging.getLogger(__name__)


class AlertRuleListView(APIView):
    """
    GET  /v1/alerts/rules/
        Every active AlertRule for the customer's tenant, plus the list
        of real rule_type choices (so the frontend's dropdown never
        hardcodes a copy that could drift from the backend's own
        choices). No technician roster here (2026-08-21) -- that only
        ever existed for the old per-rule technician picker, which is
        gone now that a rule is a company-wide policy, not tied to one
        named person.

    POST /v1/alerts/rules/
        {"rule_type": "...", "threshold_value": "...", "severity":
        "critical"|"warning"} -- always creates a new rule (never an
        upsert -- unlike Goals, duplicates are allowed on purpose, see
        AlertRule's own docstring for why).

    OUR OWN data, entered directly by the customer -- NOT synced from
    Jobber. Plain CRUD; no sync_tenant()/ensure_fresh() involvement.
    """
    permission_classes = [CustomerPermission]

    def get(self, request):
        data = {'rules': [], 'rule_types': []}
        try:
            tenant_id = request.user.tenant_id
            rules = AlertRule.objects.filter(
                tenant_id=tenant_id, is_active=True,
            ).order_by('-created_at')

            data = {
                'rules': AlertRuleSerializer(rules, many=True).data,
                'rule_types': [{'value': key, 'label': label} for key, label in ALERT_RULE_TYPES],
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

    def post(self, request):
        data = {}
        try:
            tenant_id = request.user.tenant_id
            serializer = AlertRuleSerializer(data=request.data, context={'tenant_id': tenant_id})
            serializer.is_valid(raise_exception=True)
            serializer.save()

            data = serializer.data
            return api_response_parser(
                data=data,
                message=MESSAGES['CREATED'].format('Alert rule'),
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)


class AlertRuleDetailView(APIView):
    """
    PATCH  /v1/alerts/rules/<id>/
        Partial update -- any of rule_type/threshold_value/severity/
        is_enabled. Used both for the "Save" edit flow and the enable/
        disable toggle (a PATCH with only {"is_enabled": ...}).

    DELETE /v1/alerts/rules/<id>/
        Soft-delete (is_active=False), same convention as every other
        model in this project -- never a real SQL DELETE.
    """
    permission_classes = [CustomerPermission]

    @staticmethod
    def _get_rule(request, pk):
        return AlertRule.objects.filter(
            pk=pk, tenant_id=request.user.tenant_id, is_active=True,
        ).first()

    def patch(self, request, pk):
        data = {}
        try:
            rule = self._get_rule(request, pk)
            if rule is None:
                return api_response_parser(
                    data=data,
                    message=MESSAGES['OBJ_NOT_FOUND_ERROR'].format('Alert rule'),
                    status=status.HTTP_404_NOT_FOUND,
                    success=False,
                )

            serializer = AlertRuleSerializer(
                instance=rule, data=request.data, partial=True,
                context={'tenant_id': request.user.tenant_id},
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()

            data = serializer.data
            return api_response_parser(
                data=data,
                message=MESSAGES['UPDATED'].format('Alert rule'),
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)

    def delete(self, request, pk):
        data = {}
        try:
            rule = self._get_rule(request, pk)
            if rule is None:
                return api_response_parser(
                    data=data,
                    message=MESSAGES['OBJ_NOT_FOUND_ERROR'].format('Alert rule'),
                    status=status.HTTP_404_NOT_FOUND,
                    success=False,
                )

            rule.is_active = False
            rule.save(update_fields=['is_active', 'updated_at'])
            return api_response_parser(
                data=data,
                message=MESSAGES['DELETE'].format('Alert rule'),
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)

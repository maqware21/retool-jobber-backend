from rest_framework import serializers

from apps.alerts.models import AlertRule
from apps.jobber.models import JobberUser
from helpers.constants import ALERT_RULE_TYPES_REQUIRING_USER


class AlertRuleSerializer(serializers.ModelSerializer):
    """
    Read/write shape for one AlertRule. Used for both the list (GET),
    create (POST), and update (PATCH) endpoints -- PATCH passes
    partial=True, everything else is identical.
    """
    rule_type_display = serializers.CharField(source='get_rule_type_display', read_only=True)
    user_name = serializers.CharField(source='user.name', read_only=True, allow_null=True)
    # Scoped to active JobberUsers; validate_user() below closes the
    # remaining gap -- a PrimaryKeyRelatedField's queryset can't itself
    # filter by tenant, so a cross-tenant user_id must be rejected
    # explicitly. Same pattern as TechnicianGoalWriteSerializer.
    user = serializers.PrimaryKeyRelatedField(
        queryset=JobberUser.objects.filter(is_active=True), allow_null=True, required=False,
    )

    class Meta:
        model = AlertRule
        fields = [
            'id', 'rule_type', 'rule_type_display', 'user', 'user_name',
            'threshold_value', 'severity', 'is_enabled', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_threshold_value(self, value):
        # DecimalField alone allows negative numbers with no complaint --
        # a threshold can't be negative, same explicit check already
        # used for Goals' goal_amount.
        if value < 0:
            raise serializers.ValidationError('threshold_value cannot be negative.')
        return value

    def validate_user(self, value):
        if value is None:
            return value
        tenant_id = self.context.get('tenant_id')
        if value.tenant_id != tenant_id:
            raise serializers.ValidationError('Technician not found for this account.')
        return value

    def validate(self, attrs):
        # On a partial PATCH, an omitted field isn't in attrs -- fall
        # back to the existing instance's value so this still validates
        # the real, final rule_type/user pairing, not just whatever
        # happened to be sent in this one request.
        rule_type = attrs.get('rule_type', getattr(self.instance, 'rule_type', None))
        user = attrs.get('user', getattr(self.instance, 'user', None))
        if rule_type in ALERT_RULE_TYPES_REQUIRING_USER and user is None:
            raise serializers.ValidationError({'user': ['This rule type requires a technician.']})
        return attrs

    def create(self, validated_data):
        validated_data['tenant_id'] = self.context['tenant_id']
        return AlertRule.objects.create(**validated_data)

    def update(self, instance, validated_data):
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return instance

from rest_framework import serializers

from apps.alerts.models import AlertRule


class AlertRuleSerializer(serializers.ModelSerializer):
    """
    Read/write shape for one AlertRule. Used for both the list (GET),
    create (POST), and update (PATCH) endpoints -- PATCH passes
    partial=True, everything else is identical.

    No `user` field (2026-08-21, confirmed TL correction) -- a rule is a
    company-wide policy, not tied to one named technician at creation
    time. See AlertRule's own docstring.
    """
    rule_type_display = serializers.CharField(source='get_rule_type_display', read_only=True)

    class Meta:
        model = AlertRule
        fields = [
            'id', 'rule_type', 'rule_type_display',
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

    def create(self, validated_data):
        validated_data['tenant_id'] = self.context['tenant_id']
        return AlertRule.objects.create(**validated_data)

    def update(self, instance, validated_data):
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return instance

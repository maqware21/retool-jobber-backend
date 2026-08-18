from rest_framework import serializers

from apps.goals.models import TeamGoal
from apps.goals.utils import parse_month


class TeamGoalSerializer(serializers.ModelSerializer):
    """
    Read/write shape for the whole-team monthly goal. `month` is accepted
    and returned as 'YYYY-MM' (not the underlying DateField's 'YYYY-MM-01')
    -- normalized to the 1st of that month internally, on the way in and
    back out, so the API never leaks the storage detail to callers.
    """
    month = serializers.CharField()

    class Meta:
        model = TeamGoal
        fields = ['id', 'month', 'goal_amount', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_month(self, value):
        return parse_month(value)

    def validate_goal_amount(self, value):
        # DecimalField alone allows negative numbers with no complaint --
        # a goal can't be negative, so this is a real, explicit check.
        if value < 0:
            raise serializers.ValidationError('goal_amount cannot be negative.')
        return value

    def create(self, validated_data):
        validated_data['tenant_id'] = self.context['tenant_id']
        instance = TeamGoal.create(validated_data)
        if not instance:
            raise serializers.ValidationError({'error': ['Error while saving team goal.']})
        return instance

    def update(self, instance, validated_data):
        instance.goal_amount = validated_data.get('goal_amount', instance.goal_amount)
        instance.save()
        return instance

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['month'] = instance.month.strftime('%Y-%m')
        return data

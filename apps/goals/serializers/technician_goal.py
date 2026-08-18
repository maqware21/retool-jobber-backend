from rest_framework import serializers

from apps.goals.models import TechnicianGoal
from apps.goals.utils import parse_month
from apps.jobber.models import JobberUser


class TechnicianGoalRowSerializer(serializers.Serializer):
    """
    One row in the per-technician goals roster: a JobberUser from the
    already-synced roster, plus their goal_amount for the requested month
    (null if this technician has no goal set for that month yet). Plain
    Serializer, not ModelSerializer -- this shape doesn't map 1:1 to
    either JobberUser or TechnicianGoal, it's the two merged together.
    """
    user_id = serializers.IntegerField()
    name = serializers.CharField()
    goal_amount = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)


class TechnicianGoalWriteSerializer(serializers.ModelSerializer):
    """
    Sets/updates ONE technician's goal for a month. `month` is accepted
    and returned as 'YYYY-MM', same normalization as TeamGoalSerializer.
    """
    month = serializers.CharField()
    # Scoped to active JobberUsers; validate_user() below closes the
    # remaining gap -- a PrimaryKeyRelatedField's queryset can't itself
    # filter by tenant, so a cross-tenant user_id must be rejected explicitly.
    user = serializers.PrimaryKeyRelatedField(queryset=JobberUser.objects.filter(is_active=True))

    class Meta:
        model = TechnicianGoal
        fields = ['id', 'user', 'month', 'goal_amount', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_month(self, value):
        return parse_month(value)

    def validate_goal_amount(self, value):
        if value < 0:
            raise serializers.ValidationError('goal_amount cannot be negative.')
        return value

    def validate_user(self, value):
        tenant_id = self.context.get('tenant_id')
        if value.tenant_id != tenant_id:
            raise serializers.ValidationError('Technician not found for this account.')
        return value

    def create(self, validated_data):
        validated_data['tenant_id'] = self.context['tenant_id']
        instance = TechnicianGoal.create(validated_data)
        if not instance:
            raise serializers.ValidationError({'error': ['Error while saving technician goal.']})
        return instance

    def update(self, instance, validated_data):
        instance.goal_amount = validated_data.get('goal_amount', instance.goal_amount)
        instance.save()
        return instance

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['month'] = instance.month.strftime('%Y-%m')
        return data

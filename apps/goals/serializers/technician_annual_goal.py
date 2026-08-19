from rest_framework import serializers

from apps.goals.models import TechnicianAnnualGoal
from apps.goals.utils import parse_year
from apps.jobber.models import JobberUser


class TechnicianAnnualGoalRowSerializer(serializers.Serializer):
    """
    One row in the per-technician ANNUAL goals roster: a JobberUser from
    the already-synced roster, plus their goal_amount for the requested
    year (null if unset). Exact mirror of TechnicianGoalRowSerializer.
    """
    user_id = serializers.IntegerField()
    name = serializers.CharField()
    goal_amount = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)


class TechnicianAnnualGoalWriteSerializer(serializers.ModelSerializer):
    """
    Sets/updates ONE technician's ANNUAL goal for a year. `year` is
    accepted and returned as 'YYYY', same normalization as
    TeamAnnualGoalSerializer.
    """
    year = serializers.CharField()
    user = serializers.PrimaryKeyRelatedField(queryset=JobberUser.objects.filter(is_active=True))

    class Meta:
        model = TechnicianAnnualGoal
        fields = ['id', 'user', 'year', 'goal_amount', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_year(self, value):
        return parse_year(value)

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
        instance = TechnicianAnnualGoal.create(validated_data)
        if not instance:
            raise serializers.ValidationError({'error': ['Error while saving technician annual goal.']})
        return instance

    def update(self, instance, validated_data):
        instance.goal_amount = validated_data.get('goal_amount', instance.goal_amount)
        instance.save()
        return instance

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['year'] = instance.year.strftime('%Y')
        return data

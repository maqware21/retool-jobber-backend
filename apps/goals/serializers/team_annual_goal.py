from rest_framework import serializers

from apps.goals.models import TeamAnnualGoal
from apps.goals.utils import parse_year


class TeamAnnualGoalSerializer(serializers.ModelSerializer):
    """
    Read/write shape for the whole-team ANNUAL goal. `year` is accepted
    and returned as 'YYYY' (not the underlying DateField's 'YYYY-01-01')
    -- normalized to Jan 1 of that year internally, same pattern as
    TeamGoalSerializer's month handling.
    """
    year = serializers.CharField()

    class Meta:
        model = TeamAnnualGoal
        fields = ['id', 'year', 'goal_amount', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_year(self, value):
        return parse_year(value)

    def validate_goal_amount(self, value):
        if value < 0:
            raise serializers.ValidationError('goal_amount cannot be negative.')
        return value

    def create(self, validated_data):
        validated_data['tenant_id'] = self.context['tenant_id']
        instance = TeamAnnualGoal.create(validated_data)
        if not instance:
            raise serializers.ValidationError({'error': ['Error while saving team annual goal.']})
        return instance

    def update(self, instance, validated_data):
        instance.goal_amount = validated_data.get('goal_amount', instance.goal_amount)
        instance.save()
        return instance

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['year'] = instance.year.strftime('%Y')
        return data

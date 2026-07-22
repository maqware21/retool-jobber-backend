from rest_framework import serializers

from apps.users.models import User


class ProfileSerializer(serializers.ModelSerializer):
    """Read-only user profile — used in login and profile GET responses."""

    role = serializers.SerializerMethodField()
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'full_name',
            'role', 'tenant_id', 'is_active', 'is_profile_completed',
            'is_admin_created', 'created_at',
        ]
        read_only_fields = fields

    def get_role(self, obj):
        return obj.role

    def get_full_name(self, obj):
        return obj.full_name


class ProfileUpdateSerializer(serializers.ModelSerializer):
    """Writable serializer for profile PUT — only safe fields."""

    class Meta:
        model = User
        fields = ['first_name', 'last_name']

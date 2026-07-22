from rest_framework import serializers

from apps.users.models import User
from helpers.constants import USER_PERMISSIONS
from helpers.messages import MESSAGES

ROLE_CHOICES = [p[0] for p in USER_PERMISSIONS]


class AdminCreateUserSerializer(serializers.Serializer):
    """Admin creates a new platform user (admin or customer)."""

    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100, required=False, allow_blank=True, default=None)
    email = serializers.EmailField(max_length=255)
    role = serializers.ChoiceField(choices=ROLE_CHOICES)

    def validate_email(self, value):
        value = value.lower().strip()
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError(MESSAGES['EMAIL_EXIST'])
        return value


class UserListSerializer(serializers.ModelSerializer):
    """Serializer for listing and retrieving users (Admin console)."""

    role = serializers.SerializerMethodField()
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'full_name',
            'role', 'tenant_id', 'is_active', 'is_admin_created', 'created_at',
        ]

    def get_role(self, obj):
        return obj.role

    def get_full_name(self, obj):
        return obj.full_name


class UserUpdateSerializer(serializers.ModelSerializer):
    """Admin updates a user's basic info or active status."""

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'is_active']

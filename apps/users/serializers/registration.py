from rest_framework import serializers

from apps.users.models import User
from helpers.messages import MESSAGES
from helpers.validations import password_validations


class RegistrationSerializer(serializers.Serializer):
    """Self-registration — public endpoint, creates a Customer user."""

    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100, required=False, allow_blank=True, default=None)
    email = serializers.EmailField(max_length=255)
    password = serializers.CharField(write_only=True)

    def validate_email(self, value):
        value = value.lower().strip()
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError(MESSAGES['EMAIL_EXIST'])
        return value

    def validate_password(self, value):
        password_validations(value)
        return value

    def create(self, validated_data):
        user = User.create(
            email=validated_data['email'],
            first_name=validated_data['first_name'],
            last_name=validated_data.get('last_name'),
            password=validated_data['password'],
            permission='customer',
            is_admin_created=False,
            check_validation=False,   # already validated above
        )
        if user is None:
            raise serializers.ValidationError({'email': [MESSAGES['EMAIL_EXIST']]})
        return user

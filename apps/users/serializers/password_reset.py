from rest_framework import serializers

from helpers.validations import password_validations


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=255)

    def validate_email(self, value):
        return value.strip().lower()


class ResetPasswordSerializer(serializers.Serializer):
    # uid and token are checked by the view, so that every bad link gives the
    # same answer whatever is wrong with it.
    uid = serializers.CharField(required=False, allow_blank=True, default='')
    token = serializers.CharField(required=False, allow_blank=True, default='')
    new_password = serializers.CharField(write_only=True)

    def validate_new_password(self, value):
        password_validations(value)
        return value

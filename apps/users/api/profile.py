from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.users.serializers.login import ChangePasswordSerializer
from apps.users.serializers.profile import ProfileSerializer, ProfileUpdateSerializer
from helpers.api_exception import validator_errors
from helpers.messages import MESSAGES
from helpers.utils import api_response_parser


class ProfileView(APIView):
    """
    GET  /v1/profile/  — return the authenticated user's profile.
    PUT  /v1/profile/  — update first_name / last_name.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = {}
        try:
            data = ProfileSerializer(request.user).data
            return api_response_parser(
                data=data,
                message=MESSAGES['SUCCESS'],
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)

    def put(self, request):
        data = {}
        try:
            serializer = ProfileUpdateSerializer(
                request.user,
                data=request.data,
                partial=True,
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()
            data = ProfileSerializer(request.user).data
            return api_response_parser(
                data=data,
                message=MESSAGES['UPDATED'].format('Profile'),
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)


class ChangePasswordView(APIView):
    """
    POST /v1/profile/change-password/
    Requires: { "old_password": "...", "new_password": "..." }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        data = {}
        try:
            serializer = ChangePasswordSerializer(
                data=request.data,
                context={'request': request},
            )
            serializer.is_valid(raise_exception=True)
            request.user.set_password(serializer.validated_data['new_password'])
            request.user.save()
            return api_response_parser(
                data=data,
                message=MESSAGES['UPDATED'].format('Password'),
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)

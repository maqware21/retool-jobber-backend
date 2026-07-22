from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError

from apps.users.serializers.login import LoginSerializer, ChangePasswordSerializer
from apps.users.serializers.profile import ProfileSerializer
from helpers.api_exception import validator_errors
from helpers.messages import MESSAGES
from helpers.utils import api_response_parser


class LoginView(APIView):
    """
    POST /v1/login/
    Accepts email + password for both Admin and Customer roles.
    Returns JWT tokens + user profile.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        data = {}
        try:
            serializer = LoginSerializer(
                data=request.data,
                context={'request': request},
            )
            serializer.is_valid(raise_exception=True)
            user = serializer.validated_data['user']
            data = {
                'user': ProfileSerializer(user).data,
                'token': user.token,
            }
            return api_response_parser(
                data=data,
                message=MESSAGES['SUCCESS'],
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)


class LogoutView(APIView):
    """
    POST /v1/logout/
    Blacklists the provided refresh token to invalidate the session.
    Body: { "refresh": "<refresh_token>" }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        data = {}
        try:
            refresh_token = request.data.get('refresh')
            if not refresh_token:
                from rest_framework.exceptions import ValidationError
                raise ValidationError({'refresh': [MESSAGES['REQUIRED'].format('Refresh token')]})

            token = RefreshToken(refresh_token)
            token.blacklist()
            return api_response_parser(
                data=data,
                message=MESSAGES['SUCCESS'],
                status=status.HTTP_200_OK,
                success=True,
            )
        except TokenError:
            return api_response_parser(
                data=data,
                message=MESSAGES['INVALID_TOKEN'],
                status=status.HTTP_400_BAD_REQUEST,
                success=False,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)


class ForgotPasswordView(APIView):
    """
    POST /v1/forgot-password/
    Stub — email service not yet wired. Fields are on the User model (temp_pass_exp, is_reset_password).
    """
    permission_classes = [AllowAny]

    def post(self, request):
        return api_response_parser(
            data={},
            message=MESSAGES['PASSWORD_RESET_COMING_SOON'],
            status=status.HTTP_501_NOT_IMPLEMENTED,
            success=False,
        )

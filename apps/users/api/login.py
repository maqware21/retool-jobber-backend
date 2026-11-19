from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError

from apps.users.models import User
from apps.users.serializers.login import LoginSerializer, ChangePasswordSerializer
from apps.users.serializers.password_reset import ForgotPasswordSerializer, ResetPasswordSerializer
from apps.users.serializers.profile import ProfileSerializer
from apps.users.services.password_reset import get_user_for_reset, send_reset_email
from apps.users.throttles import ForgotPasswordThrottle, ResetPasswordThrottle
from helpers import background_tasks
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
    POST /v1/forgot-password/   Body: { "email": "..." }

    Always answers 200 with the same message, whether or not the email
    belongs to an active account, so it cannot be used to find accounts.
    The email is sent from a background thread so the response time does
    not reveal whether one was sent. Users with an unusable password, such
    as a new customer who has not yet chosen one, are included on purpose.
    """
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ForgotPasswordThrottle]

    def post(self, request):
        data = {}
        try:
            serializer = ForgotPasswordSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            user = (
                User.objects
                .filter(email__iexact=serializer.validated_data['email'], is_active=True)
                .order_by('pk')
                .first()
            )
            if user is not None:
                background_tasks.submit(send_reset_email, user.pk)
            return api_response_parser(
                data=data,
                message=MESSAGES['PASSWORD_RESET_EMAIL_SENT'],
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)


class ResetPasswordView(APIView):
    """
    POST /v1/reset-password/   Body: { "uid": "...", "token": "...", "new_password": "..." }

    Sets the password for the user the emailed link belongs to. Every
    problem with the link (wrong uid, bad token, expired, already used,
    inactive user) gets the same 400. The token is bound to the current
    password hash, so it works once. A user whose password is unusable can
    set their first password here.
    """
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ResetPasswordThrottle]

    def post(self, request):
        data = {}
        try:
            serializer = ResetPasswordSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            values = serializer.validated_data

            user = get_user_for_reset(values['uid'], values['token'])
            if user is None:
                return api_response_parser(
                    data=data,
                    message=MESSAGES['PASSWORD_RESET_INVALID_LINK'],
                    status=status.HTTP_400_BAD_REQUEST,
                    success=False,
                )

            user.set_password(values['new_password'])
            user.save(update_fields=['password', 'updated_at'])
            return api_response_parser(
                data=data,
                message=MESSAGES['PASSWORD_RESET_DONE'],
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)

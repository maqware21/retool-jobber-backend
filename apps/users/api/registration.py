from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView
from apps.users.serializers.profile import ProfileSerializer
from apps.users.serializers.registration import RegistrationSerializer
from helpers.api_exception import validator_errors

from helpers.utils import api_response_parser

from helpers.messages import MESSAGES


class RegistrationView(APIView):
    """
    POST /v1/registration/
    Public self-registration — creates a Customer account and returns JWT tokens.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        data = {}
        try:
            serializer = RegistrationSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            user = serializer.save()
            data = {
                'user': ProfileSerializer(user).data,
                'token': user.token,
            }
            return api_response_parser(
                data=data,
                message=MESSAGES['CREATED'].format('Account'),
                status=status.HTTP_201_CREATED,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)

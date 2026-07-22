import logging

from django.db.models import Q
from django.http import Http404
from rest_framework import status
from rest_framework.viewsets import GenericViewSet

from apps.users.models import User
from apps.users.serializers.users import (
    AdminCreateUserSerializer,
    UserListSerializer,
    UserUpdateSerializer,
)
from helpers.api_exception import validator_errors
from helpers.messages import MESSAGES
from helpers.user_permissions import AdminPermission
from helpers.utils import api_response_parser, create_random_password

logger = logging.getLogger(__name__)


class UserView(GenericViewSet):
    """
    Admin-only CRUD for platform users.
    Registered in urls.py via DefaultRouter → prefix 'users'.

    GET    /v1/users/          → list all users (searchable)
    POST   /v1/users/          → create user (admin or customer)
    GET    /v1/users/{id}/     → retrieve single user
    PATCH  /v1/users/{id}/     → update first_name / last_name / is_active
    DELETE /v1/users/{id}/     → soft-deactivate (sets is_active=False)
    """
    permission_classes = [AdminPermission]

    def list(self, request):
        data = []
        try:
            qs = User.objects.all()
            search = request.query_params.get('search', '').strip()
            if search:
                qs = qs.filter(
                    Q(email__icontains=search) |
                    Q(first_name__icontains=search) |
                    Q(last_name__icontains=search)
                )
            data = UserListSerializer(qs, many=True).data
            return api_response_parser(
                data=data,
                message=MESSAGES['SUCCESS'],
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)

    def create(self, request):
        data = {}
        try:
            serializer = AdminCreateUserSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            vd = serializer.validated_data
            role = vd.pop('role')
            temp_password = create_random_password()

            user = User.create(
                email=vd['email'],
                first_name=vd['first_name'],
                last_name=vd.get('last_name'),
                password=temp_password,
                permission=role,
                is_admin_created=True,
                check_validation=False,
            )
            if user is None:
                return api_response_parser(
                    data=data,
                    message=MESSAGES['EMAIL_EXIST'],
                    status=status.HTTP_400_BAD_REQUEST,
                    success=False,
                )

            data = {
                'user': UserListSerializer(user).data,
                'temp_password': temp_password,
            }
            return api_response_parser(
                data=data,
                message=MESSAGES['CREATED'].format('User'),
                status=status.HTTP_201_CREATED,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)

    def retrieve(self, request, pk=None):
        data = {}
        try:
            user = User.objects.filter(id=pk).first()
            if not user:
                raise Http404(MESSAGES['USER_NOT_FOUND'])
            data = UserListSerializer(user).data
            return api_response_parser(
                data=data,
                message=MESSAGES['SUCCESS'],
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)

    def partial_update(self, request, pk=None):
        data = {}
        try:
            user = User.objects.filter(id=pk).first()
            if not user:
                raise Http404(MESSAGES['USER_NOT_FOUND'])
            serializer = UserUpdateSerializer(user, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            data = UserListSerializer(user).data
            return api_response_parser(
                data=data,
                message=MESSAGES['UPDATED'].format('User'),
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)

    def destroy(self, request, pk=None):
        data = {}
        try:
            user = User.objects.filter(id=pk).first()
            if not user:
                raise Http404(MESSAGES['USER_NOT_FOUND'])
            user.is_active = False
            user.save()
            return api_response_parser(
                data=data,
                message=MESSAGES['DELETE'].format('User'),
                status=status.HTTP_200_OK,
                success=True,
            )
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(data=data, message=msg, status=st, success=success)

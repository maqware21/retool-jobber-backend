from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import Permission
from django.db import models
from django.db.models import Q

from helpers.messages import MESSAGES


class Manager(BaseUserManager):
    """
    Default manager — wired to User.objects.
    Handles create_user and create_superuser for Django management commands.
    """

    def create_user(self, email, first_name, password):
        if not email:
            raise ValueError(MESSAGES['ENTER_EMAIL'])
        if not first_name:
            raise ValueError(MESSAGES['ENTER_FIRST_NAME'])
        if not password:
            raise ValueError(MESSAGES['ENTER_PASSWORD'])

        user = self.model(
            email=self.normalize_email(email),
            first_name=first_name,
        )
        user.set_password(password)
        user.is_superuser = False
        user.save(using=self._db)
        return user

    def create_superuser(self, email, first_name, password):
        user = self.create_user(email, first_name, password)
        user.is_staff = True
        user.is_superuser = True
        user.save(using=self._db)
        return user


class AdminManager(models.Manager):
    """Returns only users with the 'admin' permission."""

    def get_queryset(self):
        return super().get_queryset().filter(
            Q(user_permissions__name='admin')
        )


class CustomerManager(models.Manager):
    """Returns only users with the 'customer' permission."""

    def get_queryset(self):
        return super().get_queryset().filter(
            Q(user_permissions__name='customer')
        )

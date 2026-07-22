import logging

from psycopg2 import IntegrityError, DatabaseError
from rest_framework_simplejwt.tokens import RefreshToken
from safedelete import SOFT_DELETE_CASCADE
from safedelete.models import SafeDeleteModel

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin, Permission
from django.db import models

from apps.users.managers import Manager, AdminManager, CustomerManager
from helpers.constants import USER_PERMISSIONS
from helpers.messages import MESSAGES
from helpers.models import DateModel
from helpers.validations import password_validations

logger = logging.getLogger(__name__)


class User(AbstractBaseUser, PermissionsMixin, DateModel):
    """
    Central user model for both Admin and Customer roles.
    Role is stored as a Django Permission (codename: admin | customer).
    Superadmin is is_superuser=True — no permission needed.
    """

    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100, null=True, blank=True, default=None)
    email = models.EmailField(
        max_length=255,
        unique=True,
        db_index=True,
        error_messages={'unique': MESSAGES['EMAIL_EXIST']},
    )

    # Tenant FK — null for Admin users; populated when customer connects Jobber
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='users',
    )

    is_staff = models.BooleanField(default=False)
    is_profile_completed = models.BooleanField(default=False)
    last_login = models.DateTimeField(default=None, null=True, blank=True)

    # Future: email verification (fields only — not wired yet)
    verification_code = models.IntegerField(null=True, blank=True, default=None)
    verification_email = models.UUIDField(null=True, blank=True, default=None)

    # Future: forgot-password flow (fields only — not wired yet)
    temp_pass_exp = models.DateTimeField(default=None, null=True, blank=True)
    is_reset_password = models.BooleanField(default=False)

    # Tracks whether an Admin created this account (vs self-registration)
    is_admin_created = models.BooleanField(default=False)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name']

    objects = Manager()
    admin_objects = AdminManager()
    customer_objects = CustomerManager()

    class Meta:
        db_table = 'users'
        verbose_name = 'user'
        verbose_name_plural = 'users'
        permissions = USER_PERMISSIONS

    def __str__(self):
        return self.email

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def token(self):
        """Returns JWT access + refresh tokens with role and tenant_id embedded."""
        refresh = RefreshToken.for_user(self)
        refresh['role'] = self.role
        refresh['tenant_id'] = self.tenant_id
        return {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
        }

    @property
    def role(self):
        """
        Returns the user's single role string: 'admin', 'customer', or 'superadmin'.
        Superadmin takes precedence over any permission.
        """
        if self.is_superuser:
            return 'superadmin'
        permissions = list(
            self.user_permissions.all().values_list('codename', flat=True)
        )
        return permissions[0] if permissions else None

    @property
    def all_user_permissions(self):
        permissions = list(
            self.user_permissions.all().values_list('name', flat=True)
        )
        if self.is_superuser:
            permissions.append('superadmin')
        return permissions

    @property
    def full_name(self):
        last = f" {self.last_name}" if self.last_name else ""
        return f"{self.first_name}{last}"

    @property
    def name(self):
        return {'first_name': self.first_name, 'last_name': self.last_name}

    # ── Classmethods ──────────────────────────────────────────────────────────

    @classmethod
    def get(cls, email=None, id=None, check_from_all=False):
        """
        Fetch a customer user by email or id.
        Pass check_from_all=True to search across all roles.
        Returns None on not-found or DB error (caller must null-check).
        """
        try:
            qs = cls.objects.all() if check_from_all else cls.customer_objects.all()
            if email:
                return qs.filter(email=email).first()
            if id:
                return qs.filter(id=id).first()
            return qs
        except (IntegrityError, DatabaseError) as e:
            logger.error("User.get failed email=%s id=%s: %s", email, id, e)
            return None

    @classmethod
    def get_admin(cls, email):
        """Fetch an admin user by email."""
        try:
            return cls.admin_objects.filter(email=email).first()
        except (IntegrityError, DatabaseError) as e:
            logger.error("User.get_admin failed email=%s: %s", email, e)
            return None

    @classmethod
    def create(cls, email, first_name, password, last_name=None,
               permission=None, is_active=True, is_admin_created=False,
               check_validation=True):
        """
        Creates and persists a new user.
        Returns the User instance on success, None on DB failure.
        Raises ValidationError if check_validation=True and password is weak.
        """
        if check_validation:
            password_validations(password)
        try:
            user = cls(
                email=email.lower().strip(),
                first_name=first_name,
                last_name=last_name,
                is_active=is_active,
                is_admin_created=is_admin_created,
            )
            user.set_password(password)
            user.save()

            if permission:
                perm = Permission.objects.filter(codename=permission).first()
                if perm:
                    user.user_permissions.add(perm)

            return user
        except (IntegrityError, DatabaseError) as e:
            logger.error("User.create failed for email=%s: %s", email, e)
            return None

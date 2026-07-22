# Backend Common Practices — tech_track_pro

> Architectural blueprints derived from analysis of a production Django DRF codebase.
> Anti-patterns from the reference project have been corrected here.
> Follow these conventions for all code written in this backend.

---

## Table of Contents

1. [Directory Layout Blueprint](#1-directory-layout-blueprint)
2. [Global Error & Exception Handling](#2-global-error--exception-handling)
3. [Standard Model & Manager Patterns](#3-standard-model--manager-patterns)
4. [Standard Response & Try-Except Conventions](#4-standard-response--try-except-conventions)

---

## 1. Directory Layout Blueprint

```
backend/
├── config/                         # Django project config (DO NOT put business logic here)
│   ├── __init__.py
│   ├── settings.py
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
│
├── apps/                           # All Django applications live here
│   ├── __init__.py
│   └── users/                      # One folder per domain
│       ├── __init__.py
│       ├── apps.py                 # name = "apps.users"
│       ├── models.py
│       ├── managers.py             # Custom managers and querysets (separate file)
│       ├── serializers.py          # Input validation only — no external API calls
│       ├── views.py                # Thin views — delegate to services
│       ├── services.py             # Business logic layer (Stripe, email, etc.)
│       ├── permissions.py          # App-specific permission classes
│       ├── urls.py
│       └── migrations/
│           └── __init__.py
│
├── helpers/                        # Cross-cutting utilities — NOT a Django app (no apps.py)
│   ├── __init__.py
│   ├── models.py                   # Abstract base models (DateModel)
│   ├── api_exception.py            # Global DRF exception handler
│   ├── utils.py                    # api_response_parser and shared helpers
│   ├── constants.py                # All enums, choices, and magic values
│   ├── messages.py                 # All user-facing string constants
│   ├── pagination.py               # Custom pagination classes
│   ├── permissions.py              # Global permission classes (AdminPermission, etc.)
│   └── validators.py               # Reusable field validators
│
├── .venv/                          # Never committed (in .gitignore)
├── .env                            # Never committed (in .gitignore)
├── .env.example                    # Committed — shows required keys without values
├── .gitignore
├── manage.py
└── requirements.txt
```

### Rules

- **Every app lives under `apps/`** with `name = "apps.<appname>"` in `apps.py`.
- **`helpers/` is not a Django app** — it has no `apps.py` and is never in `INSTALLED_APPS`.
- **`config/` contains only settings, URL root, and WSGI/ASGI** — no models, no views.
- **Business logic goes in `services.py`**, not in serializers, models, or views.
- **DB table names** are set explicitly via `Meta.db_table` using `snake_case` only — never hyphens.
- **One serializer file per app** unless the app is very large, in which case create a `serializers/` sub-package.
- **One `managers.py` per app** for custom managers and querysets — never inline inside `models.py`.

---

## 2. Global Error & Exception Handling

### `helpers/api_exception.py`

```python
import logging
from rest_framework.views import exception_handler
from rest_framework.exceptions import ValidationError, AuthenticationFailed, NotAuthenticated
from rest_framework.response import Response
from rest_framework import status

logger = logging.getLogger(__name__)


def api_exception_handler(exc, context):
    """
    Global DRF exception handler.
    Normalises ALL error responses into:
        {"success": false, "message": "<string>"}
    Unhandled exceptions (non-DRF) are caught and returned as a JSON 500.
    """
    # Let DRF build the default response first
    response = exception_handler(exc, context)

    if response is not None:
        # DRF recognised the exception — normalise its shape
        message = _extract_message(exc, response.data)
        response.data = {
            "success": False,
            "message": message,
        }
        return response

    # Unhandled exception (not a DRF exception) — return a JSON 500
    # Log it with full traceback so it appears in error monitoring
    logger.exception(
        "Unhandled exception in %s",
        context.get("view", "unknown view"),
        exc_info=exc,
    )
    return Response(
        {"success": False, "message": "An unexpected error occurred. Please try again later."},
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def _extract_message(exc, data):
    """Pull the first meaningful error string out of any DRF exception."""
    if isinstance(exc, ValidationError):
        return _flatten_validation_errors(data)
    if isinstance(exc, (AuthenticationFailed, NotAuthenticated)):
        return _flatten_auth_errors(data)
    # Generic DRF exception (PermissionDenied, NotFound, MethodNotAllowed, etc.)
    if isinstance(data, dict) and "detail" in data:
        return str(data["detail"])
    if isinstance(data, list) and data:
        return str(data[0])
    return str(data)


def _flatten_validation_errors(data):
    """
    Recursively collect error strings from a ValidationError detail tree.
    Returns only the first error for a clean single-message response.
    """
    errors = []
    _collect_errors(data, errors)
    return errors[0] if errors else "Validation error."


def _collect_errors(data, out):
    if isinstance(data, list):
        for item in data:
            _collect_errors(item, out)
    elif isinstance(data, dict):
        for value in data.values():
            _collect_errors(value, out)
    else:
        out.append(str(data))


def _flatten_auth_errors(data):
    errors = []
    _collect_errors(data, errors)
    return errors[0] if errors else "Authentication failed."
```

### Wire it into `config/settings.py`

```python
REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "helpers.api_exception.api_exception_handler",
    # ... other DRF settings
}
```

### `helpers/api_exception.py` — `view_exception_handler` helper

Use this inside view `except` blocks instead of catching raw exceptions yourself:

```python
import logging
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import ValidationError

logger = logging.getLogger(__name__)


def view_exception_handler(exc):
    """
    Translate common exceptions into (success, message, http_status) tuples
    for use inside view try/except blocks.

    Usage:
        except Exception as e:
            success, msg, st = view_exception_handler(e)
            return api_response_parser(success=False, message=msg, status=st)
    """
    if isinstance(exc, Http404):
        return False, "Resource not found.", status.HTTP_404_NOT_FOUND

    if isinstance(exc, ValidationError):
        errors = []
        _collect_errors(exc.detail, errors)
        return False, errors[0] if errors else "Validation error.", status.HTTP_400_BAD_REQUEST

    # Log unexpected errors — never swallow silently
    logger.exception("Unexpected error in view", exc_info=exc)
    return False, "An unexpected error occurred.", status.HTTP_500_INTERNAL_SERVER_ERROR
```

---

## 3. Standard Model & Manager Patterns

### 3a. Abstract Base Model — `helpers/models.py`

```python
from django.db import models


class DateModel(models.Model):
    """
    Universal abstract base for all project models.
    Provides audit timestamps and a soft is_active flag.
    """
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
```

### 3b. Upload Path Helpers — `helpers/models.py`

```python
import uuid
import os


def _unique_filename(filename):
    ext = os.path.splitext(filename)[1]
    return f"{uuid.uuid4().hex}{ext}"


def upload_path(folder):
    """
    Factory that returns an upload_to callable for a given folder.

    Usage:
        avatar = models.FileField(upload_to=upload_path("users/avatars"))
    """
    def _upload_to(instance, filename):
        return f"{folder}/{_unique_filename(filename)}"
    return _upload_to
```

### 3c. Custom Manager & QuerySet — `apps/<app>/managers.py`

```python
from django.db import models


class ActiveQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def inactive(self):
        return self.filter(is_active=False)


class ActiveManager(models.Manager):
    """Default manager — only returns active records."""

    def get_queryset(self):
        return ActiveQuerySet(self.model, using=self._db).active()

    def all_records(self):
        """Bypass the active filter when explicitly needed."""
        return ActiveQuerySet(self.model, using=self._db)
```

### 3d. User Model & User Manager — `apps/users/managers.py`

```python
import logging
from django.contrib.auth.models import BaseUserManager

logger = logging.getLogger(__name__)


class UserManager(BaseUserManager):

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if not extra_fields["is_staff"]:
            raise ValueError("Superuser must have is_staff=True.")
        if not extra_fields["is_superuser"]:
            raise ValueError("Superuser must have is_superuser=True.")
        return self.create_user(email, password, **extra_fields)
```

### 3e. Standard Model — `apps/users/models.py`

```python
import logging
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from helpers.models import DateModel, upload_path
from apps.users.managers import UserManager

logger = logging.getLogger(__name__)


class User(AbstractBaseUser, PermissionsMixin, DateModel):
    email = models.EmailField(unique=True, db_index=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    avatar = models.FileField(upload_to=upload_path("users/avatars"), null=True, blank=True)

    is_staff = models.BooleanField(default=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    objects = UserManager()

    class Meta:
        db_table = "users"        # Always set explicitly

    def __str__(self):
        return self.email

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()
```

### 3f. Standard Domain Model — `apps/<app>/models.py`

```python
import logging
from django.db import models
from helpers.models import DateModel

logger = logging.getLogger(__name__)


class SomeModel(DateModel):
    name = models.CharField(max_length=255)
    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="some_models",
    )

    objects = SomeModelManager()      # Always assign a custom manager

    class Meta:
        db_table = "some_models"      # snake_case only, never hyphens
        ordering = ["-created_at"]

    def __str__(self):
        return self.name
```

### Rules for Models

- **Never return `None` from classmethods on failure** — raise the exception or log and re-raise. Silent `None` returns hide bugs.
- **Never put business logic or external API calls in a model** — use `services.py`.
- **Never define the same property twice** — Python silently uses the last definition.
- **Explicit `db_table`** — always set it; use `snake_case` only.
- **Explicit `related_name`** on every `ForeignKey` and `ManyToManyField`.
- **One manager file** — keep all `Manager` and `QuerySet` subclasses in `managers.py`, not inline in `models.py`.
- **`__str__`** — always define it for every model.

---

## 4. Standard Response & Try-Except Conventions

### 4a. `helpers/utils.py` — Unified response formatter

```python
from rest_framework.response import Response
from rest_framework import status as http_status


def api_response(
    *,
    success: bool,
    message: str,
    data=None,
    status: int = None,
) -> Response:
    """
    Single, consistent response envelope for all API responses.

    Success shape:
        {"success": true, "message": "...", "data": <any>}

    Failure shape:
        {"success": false, "message": "..."}

    Never use "status" as a key — the word is ambiguous. Use "success" (bool).
    """
    if status is None:
        status = http_status.HTTP_200_OK if success else http_status.HTTP_400_BAD_REQUEST

    body = {"success": success, "message": message}
    if success and data is not None:
        body["data"] = data

    return Response(body, status=status)
```

### 4b. Standard View Template

```python
import logging
from rest_framework.views import APIView
from rest_framework import status
from helpers.utils import api_response
from helpers.api_exception import view_exception_handler
from helpers.messages import MESSAGES
from helpers.permissions import AdminPermission    # or whichever role is required

logger = logging.getLogger(__name__)


class ExampleView(APIView):
    permission_classes = [AdminPermission]

    def get(self, request):
        try:
            # 1. Validate query params via a serializer if needed
            # 2. Query data
            # 3. Serialise output
            data = {}
            return api_response(success=True, message=MESSAGES["SUCCESS"], data=data)

        except Exception as exc:
            success, msg, st = view_exception_handler(exc)
            return api_response(success=success, message=msg, status=st)

    def post(self, request):
        try:
            serializer = ExampleSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)   # raises ValidationError → DRF handler
            result = ExampleService.create(serializer.validated_data, actor=request.user)
            return api_response(
                success=True,
                message=MESSAGES["CREATED"],
                data=result,
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            success, msg, st = view_exception_handler(exc)
            return api_response(success=success, message=msg, status=st)
```

### 4c. Standard Serializer Template

```python
from rest_framework import serializers
from apps.users.models import User


class ExampleSerializer(serializers.ModelSerializer):
    """Input-only serializer — validates and sanitises data. No side effects."""

    class Meta:
        model = User
        fields = ["email", "first_name", "last_name", "password"]
        extra_kwargs = {
            "password": {"write_only": True},
        }

    def validate_email(self, value):
        """Field-level validators: only raise ValidationError — never return None."""
        return value.lower().strip()

    def validate(self, attrs):
        """Cross-field validation."""
        # raise serializers.ValidationError("...") on failure
        return attrs
```

### 4d. Standard Service Template — `apps/<app>/services.py`

```python
import logging
from django.db import transaction
from rest_framework.exceptions import ValidationError
from helpers.messages import MESSAGES

logger = logging.getLogger(__name__)


class ExampleService:
    """
    Contains all business logic for the Example domain.
    Views call services; services call models, external APIs, and send notifications.
    Serializers never call services — they only validate input.
    """

    @staticmethod
    @transaction.atomic
    def create(validated_data: dict, actor) -> dict:
        """
        Wraps creation in a transaction so that DB writes and external calls
        can be rolled back together on failure.
        """
        try:
            instance = SomeModel.objects.create(**validated_data)
            # External API calls go here
            logger.info("Created %s id=%s by user=%s", instance.__class__.__name__, instance.pk, actor.pk)
            return {"id": instance.pk}
        except Exception:
            logger.exception("Failed to create example for user=%s", actor.pk)
            raise   # Always re-raise — never swallow
```

### 4e. `helpers/messages.py` — String constants

```python
MESSAGES = {
    # Generic
    "SUCCESS": "Success.",
    "CREATED": "Created successfully.",
    "UPDATED": "Updated successfully.",
    "DELETED": "Deleted successfully.",
    "NOT_FOUND": "Not found.",
    "PERMISSION_DENIED": "You do not have permission to perform this action.",
    "UNAUTHENTICATED": "Authentication credentials were not provided.",
    # Auth
    "INVALID_CREDENTIALS": "Invalid email or password.",
    "ACCOUNT_INACTIVE": "Your account is inactive. Please contact support.",
    "EMAIL_TAKEN": "An account with this email already exists.",
    # Validation
    "REQUIRED": "This field is required.",
    "INVALID_EMAIL": "Enter a valid email address.",
    "PASSWORD_WEAK": "Password must be at least 8 characters and include uppercase, lowercase, and a digit.",
}
```

### 4f. Logging Setup — `config/settings.py`

```python
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
```

### 4g. `config/settings.py` — Must-follow conventions

```python
from decouple import config   # pip install python-decouple

SECRET_KEY = config("SECRET_KEY")              # Never hardcode
DEBUG = config("DEBUG", default=False, cast=bool)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost").split(",")

AUTH_USER_MODEL = "users.User"

INSTALLED_APPS = [
    # Django built-ins
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "corsheaders",
    # Local apps
    "apps.users",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",       # Must be before CommonMiddleware
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "helpers.api_exception.api_exception_handler",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "helpers.pagination.StandardPagination",
    "PAGE_SIZE": 20,
}

CORS_ALLOWED_ORIGINS = config(
    "CORS_ALLOWED_ORIGINS",
    default="http://localhost:5173",
).split(",")
# Never use CORS_ALLOW_ALL_ORIGINS = True in production
```

---

## Anti-Patterns to Actively Avoid

| Anti-Pattern | Why It's Wrong | Correct Approach |
|---|---|---|
| `except Exception: return None` | Silent failure — bugs become invisible | Log and re-raise, or return a typed result object |
| `except:` (bare) | Catches `SystemExit`, `KeyboardInterrupt` | Always `except Exception as e:` at minimum |
| Business logic in serializers | Untestable, unrollback-able, hidden side effects | Move to `services.py` with `@transaction.atomic` |
| Business logic in models (classmethods calling external APIs) | Models should only describe data | Models for persistence; services for logic |
| `"status": false` in one path, `"success": false` in another | API clients can't rely on a stable shape | Use `"success"` everywhere via `api_response()` |
| `SECRET_KEY` hardcoded | Security vulnerability | Always from environment via `python-decouple` |
| `DEBUG = True` hardcoded | Leaks debug info in production | Always from environment |
| `CORS_ALLOW_ALL_ORIGINS = True` | Exposes API to any origin | Whitelist specific origins |
| `db_table` with hyphens | PostgreSQL requires quoting; breaks migrations | Use `snake_case` only |
| Defining the same `@property` twice on a model | Python uses last definition silently | One property per name, always |
| `type(x) == SomeException` | Doesn't match subclasses | Always `isinstance(x, SomeException)` |
| `print()` for debug output | Doesn't appear in log aggregators | Always `logger.debug()` / `logger.info()` |
| No `LOGGING` config | Errors invisible in production | Add structured LOGGING to settings |
| Committing `.env`, JSON key files, or `SECRET_KEY` | Security breach | Add to `.gitignore`; use `.env.example` |
| `AllowAllUsersModelBackend` | Lets deactivated users log in | Use the default `ModelBackend` unless you have a specific need |
| Pagination used inconsistently | Clients can't predict response shape | Apply `DEFAULT_PAGINATION_CLASS` globally in DRF settings |

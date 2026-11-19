from datetime import timedelta
from pathlib import Path

from decouple import config

from helpers.constants import DEFAULT_LIMIT

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config('SECRET_KEY')
DEBUG = config('DEBUG', default=True, cast=bool)
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='*').split(',')

AUTH_USER_MODEL = 'users.User'
AUTHENTICATION_BACKENDS = ['django.contrib.auth.backends.ModelBackend']

# ── Installed Apps ────────────────────────────────────────────────────────────

DJANGO_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

THIRD_PARTY_APPS = [
    'safedelete',
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    'drf_yasg',
]

LOCAL_APPS = [
    'apps.users.apps.UsersConfig',
    'apps.tenants.apps.TenantsConfig',
    'apps.billing.apps.BillingConfig',
    'apps.jobber.apps.JobberConfig',
    'apps.goals.apps.GoalsConfig',
    'apps.alerts.apps.AlertsConfig',
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# ── Middleware ────────────────────────────────────────────────────────────────

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'tech_track_pro.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'tech_track_pro.wsgi.application'

# ── Database — PostgreSQL ─────────────────────────────────────────────────────

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('DB_NAME', default='tech_track_pro'),
        'USER': config('DB_USER', default='postgres'),
        'PASSWORD': config('DB_PASSWORD', default=''),
        'HOST': config('DB_HOST', default='localhost'),
        'PORT': config('DB_PORT', default='5432'),
    }
}

# ── REST Framework ────────────────────────────────────────────────────────────

REST_FRAMEWORK = {
    'TEST_REQUEST_DEFAULT_FORMAT': 'json',
    'EXCEPTION_HANDLER': 'helpers.api_exception.api_exception_handler',
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.FormParser',
        'rest_framework.parsers.MultiPartParser',
        'rest_framework.parsers.JSONParser',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.LimitOffsetPagination',
    'PAGE_SIZE': DEFAULT_LIMIT,
    # Only views that name a throttle class are throttled. Counts live in the
    # default Django cache (local memory: per gunicorn worker, reset on restart).
    'DEFAULT_THROTTLE_RATES': {
        'forgot_password': '5/hour',
        'reset_password': '20/hour',
    },
    # Nginx is the one proxy in front of Django, so the client address is the
    # last X-Forwarded-For entry; earlier entries are client-supplied.
    'NUM_PROXIES': config('NUM_PROXIES', default=1, cast=int),
}

# ── JWT ───────────────────────────────────────────────────────────────────────

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_HEADER_NAME': 'HTTP_AUTHORIZATION',
}

# ── CORS ──────────────────────────────────────────────────────────────────────

CORS_ALLOWED_ORIGINS = config(
    'CORS_ALLOWED_ORIGINS',
    default='http://localhost:5173',
).split(',')

# ── Jobber OAuth / API ────────────────────────────────────────────────────────
# Credentials come from the Jobber Developer Center (developer.getjobber.com).
# REDIRECT_URI must match the OAuth callback URL registered on the app exactly.
# Endpoint/version defaults track Jobber's public API and rarely change.

JOBBER_CLIENT_ID = config('JOBBER_CLIENT_ID', default='')
JOBBER_CLIENT_SECRET = config('JOBBER_CLIENT_SECRET', default='')
JOBBER_REDIRECT_URI = config(
    'JOBBER_REDIRECT_URI',
    default='http://localhost:8000/v1/jobber/callback/',
)
# Space-separated scopes requested during authorization (read-only by default).
JOBBER_SCOPES = config('JOBBER_SCOPES', default='read_clients read_jobs')

JOBBER_AUTHORIZE_URL = config(
    'JOBBER_AUTHORIZE_URL',
    default='https://api.getjobber.com/api/oauth/authorize',
)
JOBBER_TOKEN_URL = config(
    'JOBBER_TOKEN_URL',
    default='https://api.getjobber.com/api/oauth/token',
)
JOBBER_GRAPHQL_URL = config(
    'JOBBER_GRAPHQL_URL',
    default='https://api.getjobber.com/api/graphql',
)
# Jobber GraphQL API version (X-JOBBER-GRAPHQL-VERSION header).
JOBBER_API_VERSION = config('JOBBER_API_VERSION', default='2025-04-16')

# Frontend URLs the callback redirects the browser to after the OAuth round-trip.
JOBBER_CONNECT_SUCCESS_URL = config(
    'JOBBER_CONNECT_SUCCESS_URL',
    default='http://localhost:5173/jobber/connected',
)
JOBBER_CONNECT_FAILURE_URL = config(
    'JOBBER_CONNECT_FAILURE_URL',
    default='http://localhost:5173/jobber/failed',
)

# ── Email ─────────────────────────────────────────────────────────────────────
# Standard Django SMTP settings; any SMTP-speaking provider works. The default
# backend prints messages to the console, so nothing is sent until
# EMAIL_BACKEND and the SMTP values are set. APP_BASE_URL is the frontend
# origin that links in emails point at.

EMAIL_BACKEND = config('EMAIL_BACKEND', default='django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = config('EMAIL_HOST', default='localhost')
EMAIL_PORT = config('EMAIL_PORT', default=25, cast=int)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=False, cast=bool)
EMAIL_TIMEOUT = 10  # seconds; a stalled SMTP server must not hang a worker
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='TechTrackPro <no-reply@techtrackpro.com>')
APP_BASE_URL = config('APP_BASE_URL', default='https://app.techtrackpro.com')

# Lifetime of a password-reset or set-password link, in seconds (3 days).
PASSWORD_RESET_TIMEOUT = config('PASSWORD_RESET_TIMEOUT', default=60 * 60 * 24 * 3, cast=int)

# ── Stripe ────────────────────────────────────────────────────────────────────
# All empty by default so the app boots without them; the billing code checks
# for them where it needs them. STRIPE_API_VERSION is the version the pinned
# stripe library targets; an empty env value falls back to it.

STRIPE_SECRET_KEY = config('STRIPE_SECRET_KEY', default='')
STRIPE_WEBHOOK_SECRET = config('STRIPE_WEBHOOK_SECRET', default='')
STRIPE_PRICE_ID = config('STRIPE_PRICE_ID', default='')
STRIPE_API_VERSION = config('STRIPE_API_VERSION', default='') or '2026-10-28.endive'

# ── Swagger ───────────────────────────────────────────────────────────────────

SWAGGER_SETTINGS = {
    'USE_SESSION_AUTH': False,
    'SECURITY_DEFINITIONS': {
        'Bearer': {
            'type': 'apiKey',
            'name': 'Authorization',
            'in': 'header',
        }
    },
}

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# ── Internationalisation ──────────────────────────────────────────────────────

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# ── Static ────────────────────────────────────────────────────────────────────
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
# ── Logging ───────────────────────────────────────────────────────────────────

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} {name} — {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

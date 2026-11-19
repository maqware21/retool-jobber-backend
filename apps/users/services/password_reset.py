"""
Set-password links and the emails that carry them.

One link format serves both the forgot-password email and the welcome email
for a new customer, whose password is unusable until they follow it. Links
carry a uid and a token from Django's default_token_generator: the token is
bound to the user's current password hash, so it stops working once the
password changes, and it expires after settings.PASSWORD_RESET_TIMEOUT.
"""
import logging
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from apps.users.models import User

logger = logging.getLogger(__name__)

# Frontend route that reads uid and token from the query string.
RESET_PATH = '/reset-password'

KIND_RESET = 'reset'
KIND_WELCOME = 'welcome'

_SUBJECTS = {
    KIND_RESET: 'Reset your TechTrackPro password',
    KIND_WELCOME: 'Welcome to TechTrackPro: set your password',
}


def make_reset_link(user):
    query = urlencode({
        'uid': urlsafe_base64_encode(force_bytes(user.pk)),
        'token': default_token_generator.make_token(user),
    })
    return f"{settings.APP_BASE_URL.rstrip('/')}{RESET_PATH}?{query}"


def get_user_for_reset(uid, token):
    """
    The active user the uid and token belong to, or None. A wrong uid, a
    malformed value, an inactive user, a bad token and an expired token are
    all the same None.
    """
    if not uid or not token:
        return None
    try:
        user_id = int(force_str(urlsafe_base64_decode(uid)))
    except (TypeError, ValueError, OverflowError, UnicodeDecodeError):
        return None
    user = User.objects.filter(pk=user_id, is_active=True).first()
    if user is None or not default_token_generator.check_token(user, token):
        return None
    return user


def send_set_password_email(user, kind):
    """
    Send the plain-text and HTML email for `kind` to the user. Raises on a
    delivery failure; callers decide how to log it.
    """
    context = {
        'first_name': user.first_name,
        'link': make_reset_link(user),
        'valid_days': max(settings.PASSWORD_RESET_TIMEOUT // 86400, 1),
    }
    template = f'users/emails/{kind}'
    message = EmailMultiAlternatives(
        subject=_SUBJECTS[kind],
        body=render_to_string(f'{template}.txt', context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )
    message.attach_alternative(render_to_string(f'{template}.html', context), 'text/html')
    message.send(fail_silently=False)


def send_reset_email(user_id):
    """
    Background task for forgot-password. Anything that goes wrong is logged
    with the user id and the exception type only, never the address.
    """
    user = User.objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        return
    try:
        send_set_password_email(user, KIND_RESET)
    except Exception as exc:
        logger.error(
            "Password reset email failed for user id=%s (%s).", user_id, type(exc).__name__,
        )

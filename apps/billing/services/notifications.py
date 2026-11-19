import logging

from django.utils import timezone

from apps.billing.models import Subscription
from apps.users.models import User
from apps.users.services.password_reset import KIND_WELCOME, send_set_password_email
from helpers import background_tasks

logger = logging.getLogger(__name__)


def send_welcome_email(user_id):
    """
    Send the welcome email with a set-password link and, on success, stamp
    `welcome_email_sent_at` on the user's tenant Subscription.

    Returns True when the email was sent. A failure is logged with the user
    id and the exception type only (never the address, which an SMTP error
    message can quote) and leaves the timestamp empty, so the row is picked
    up again by `resend_welcome_emails`.
    """
    user = User.objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        logger.error("Welcome email not sent: no active user with id=%s.", user_id)
        return False

    try:
        send_set_password_email(user, KIND_WELCOME)
    except Exception as exc:
        logger.error("Welcome email failed for user id=%s (%s).", user_id, type(exc).__name__)
        return False

    if user.tenant_id is not None:
        Subscription.objects.filter(tenant_id=user.tenant_id).update(
            welcome_email_sent_at=timezone.now(),
        )
    return True


def queue_welcome_email(user):
    """
    Send the welcome email from the background pool so the caller, the
    Stripe webhook, answers quickly. Call it after the transaction that
    created the user has committed.
    """
    background_tasks.submit(send_welcome_email, user.pk)

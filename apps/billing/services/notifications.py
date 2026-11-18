import logging

logger = logging.getLogger(__name__)


def queue_welcome_email(user):
    """
    Placeholder for the welcome email. It only logs; nothing is sent.
    """
    logger.info("Welcome email would be queued for user id=%s.", user.pk)

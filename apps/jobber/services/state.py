"""
Signed ``state`` for the Jobber OAuth round-trip.

The callback arrives on the user's browser without our JWT (it is a plain
redirect from Jobber), so we cannot authenticate it the usual way. Instead the
connect endpoint signs the initiating user's id into the ``state`` parameter;
the callback verifies the signature and recovers the user. The signature
(Django's SECRET_KEY) doubles as CSRF protection — a forged callback cannot
produce a valid state.
"""

from django.core import signing

STATE_SALT = 'apps.jobber.oauth.state'
# How long a connect attempt stays valid (seconds). The user has this long to
# complete Jobber's authorization screen.
STATE_MAX_AGE = 600


def build_state(user_id):
    """Return a signed, timestamped state string encoding the initiating user."""
    return signing.dumps({'user_id': user_id}, salt=STATE_SALT)


def read_state(state):
    """
    Verify a state string and return the encoded user_id.

    Raises ``signing.BadSignature`` (tampered) or ``signing.SignatureExpired``
    (older than STATE_MAX_AGE) — callers should treat both as an invalid attempt.
    """
    data = signing.loads(state, salt=STATE_SALT, max_age=STATE_MAX_AGE)
    return data['user_id']

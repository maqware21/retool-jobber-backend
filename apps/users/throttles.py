from rest_framework.throttling import AnonRateThrottle


class ForgotPasswordThrottle(AnonRateThrottle):
    """Per client address; the rate is DEFAULT_THROTTLE_RATES['forgot_password']."""
    scope = 'forgot_password'


class ResetPasswordThrottle(AnonRateThrottle):
    """Per client address; the rate is DEFAULT_THROTTLE_RATES['reset_password']."""
    scope = 'reset_password'

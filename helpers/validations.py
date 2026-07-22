import re

from rest_framework.exceptions import ValidationError

from helpers.messages import MESSAGES


def password_validations(password):
    if len(password) < 8:
        raise ValidationError(
            detail={'password': [MESSAGES['PASSWORD_WEAK']]},
            code='invalid'
        )
    if re.search(r'^(?=.*[0-9])(?=.*[a-z])(?=.*[A-Z]).*$', password) is None:
        raise ValidationError(
            detail={'password': [MESSAGES['PASSWORD_WEAK']]},
            code='invalid'
        )
    return True

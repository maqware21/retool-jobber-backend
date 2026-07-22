import logging

from django.db import IntegrityError
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import ValidationError, AuthenticationFailed, NotAuthenticated
from rest_framework.response import Response
from rest_framework.views import exception_handler

from helpers.messages import MESSAGES

logger = logging.getLogger(__name__)


# ── DRF-layer global exception handler ───────────────────────────────────────

def api_exception_handler(exc, context):
    """
    Wired via REST_FRAMEWORK['EXCEPTION_HANDLER'].
    Normalises every DRF error into: {"success": false, "message": "<string>"}
    Unhandled exceptions (non-DRF) are returned as a JSON 500.
    """
    response = exception_handler(exc, context)

    if response is not None:
        data = response.data
        message = _extract_message(exc, data)
        response.data = {'success': False, 'message': message}
        return response

    # Non-DRF exception — log it and return a safe JSON 500
    logger.exception(
        "Unhandled exception in %s",
        getattr(context.get('view'), '__class__', 'unknown'),
        exc_info=exc,
    )
    return Response(
        {'success': False, 'message': MESSAGES['SOMETHING_WENT_WRONG']},
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def _extract_message(exc, data):
    if isinstance(exc, ValidationError):
        return _flatten_errors(data)
    if isinstance(exc, (AuthenticationFailed, NotAuthenticated)):
        return _flatten_errors(data)
    if isinstance(data, dict) and 'detail' in data:
        return str(data['detail'])
    if isinstance(data, list) and data:
        return str(data[0])
    return str(data)


def _flatten_errors(data):
    errors = []
    _collect(data, errors)
    return errors[0] if errors else MESSAGES['SOMETHING_WENT_WRONG']


def _collect(data, out):
    if isinstance(data, list):
        for item in data:
            _collect(item, out)
    elif isinstance(data, dict):
        for value in data.values():
            _collect(value, out)
    else:
        out.append(str(data))


# ── View-layer helper (used inside view try/except blocks) ───────────────────

def validator_errors(ve):
    """
    Translates a caught exception into (success, message, http_status).
    Use inside view except blocks:
        except Exception as ve:
            success, msg, st = validator_errors(ve)
            return api_response_parser(...)
    """
    success = False
    st = status.HTTP_400_BAD_REQUEST
    msg = MESSAGES['SOMETHING_WENT_WRONG']

    if isinstance(ve, Http404):
        msg = str(ve) if str(ve) else MESSAGES['OBJ_NOT_FOUND_ERROR'].format('Resource')
        st = status.HTTP_404_NOT_FOUND

    elif isinstance(ve, ValidationError):
        errors = []
        _collect(ve.detail, errors)
        msg = errors[0] if errors else msg

    elif isinstance(ve, IntegrityError):
        logger.error("IntegrityError in view: %s", str(ve))
        msg = MESSAGES['SOMETHING_WENT_WRONG']

    else:
        logger.exception("Unexpected exception in view", exc_info=ve)

    return success, msg, st

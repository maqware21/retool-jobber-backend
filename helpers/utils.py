import random
import string

from rest_framework.response import Response


def api_response_parser(**kwargs):
    """
    Unified response envelope for all API endpoints.

    Success:  {"success": true,  "message": "...", "data": <any>}
    Failure:  {"success": false, "message": "..."}
    """
    if kwargs['success']:
        return Response(
            {'data': kwargs['data'], 'message': kwargs['message'], 'success': True},
            status=kwargs['status'],
        )
    return Response(
        {'message': kwargs['message'], 'success': False},
        status=kwargs['status'],
    )


def create_random_password(length=15):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

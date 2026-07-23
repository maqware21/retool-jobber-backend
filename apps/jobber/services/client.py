"""
Jobber OAuth 2.0 + GraphQL client.

Thin wrapper over Jobber's public API:
  - authorize URL construction
  - authorization-code exchange and refresh-token rotation
  - authenticated GraphQL calls that transparently refresh a stale token

All network failures surface as ``JobberAPIError`` so views can handle a single
exception type.
"""

import logging
from urllib.parse import urlencode

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Network timeout (connect, read) for every Jobber call, in seconds.
REQUEST_TIMEOUT = 30


class JobberAPIError(Exception):
    """Raised for any failed Jobber OAuth or GraphQL request."""


# ── OAuth ─────────────────────────────────────────────────────────────────────

def build_authorize_url(state):
    """Build the Jobber authorization URL the user's browser is sent to."""
    params = {
        'client_id': settings.JOBBER_CLIENT_ID,
        'redirect_uri': settings.JOBBER_REDIRECT_URI,
        'response_type': 'code',
        'state': state,
    }
    if settings.JOBBER_SCOPES:
        params['scope'] = settings.JOBBER_SCOPES
    return f"{settings.JOBBER_AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(code):
    """Exchange an authorization ``code`` for an access/refresh token pair."""
    return _post_token({
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': settings.JOBBER_REDIRECT_URI,
    })


def refresh_tokens(refresh_token):
    """Exchange a refresh token for a fresh access token."""
    return _post_token({
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
    })


def _post_token(payload):
    """POST to Jobber's token endpoint with client credentials, return the JSON body."""
    payload = {
        **payload,
        'client_id': settings.JOBBER_CLIENT_ID,
        'client_secret': settings.JOBBER_CLIENT_SECRET,
    }
    try:
        response = requests.post(
            settings.JOBBER_TOKEN_URL,
            data=payload,
            headers={'Accept': 'application/json'},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.error("Jobber token request failed to send: %s", exc)
        raise JobberAPIError("Could not reach Jobber to complete authorization.") from exc

    if not response.ok:
        logger.error("Jobber token endpoint %s: %s", response.status_code, response.text)
        raise JobberAPIError("Jobber rejected the token request.")

    return response.json()


# ── GraphQL ─────────────────────────────────────────────────────────────────

def get_valid_access_token(account):
    """
    Return a usable access token for ``account``, refreshing (and persisting)
    it first if it is expired or about to expire.
    """
    if account.is_expired:
        logger.info("Refreshing expired Jobber token for tenant=%s", account.tenant_id)
        token_data = refresh_tokens(account.refresh_token)
        account.store_tokens(token_data)
    return account.access_token


def execute(account, query, variables=None):
    """
    Run a GraphQL query/mutation against Jobber on behalf of ``account``.

    Refreshes a stale token up front, and retries once on a 401 in case Jobber
    invalidated the token early. Returns the ``data`` object from the response.
    """
    token = get_valid_access_token(account)
    response = _post_graphql(token, query, variables)

    if response.status_code == 401:
        # Token rejected despite our expiry check — force one refresh and retry.
        logger.info("Jobber returned 401; forcing refresh for tenant=%s", account.tenant_id)
        token_data = refresh_tokens(account.refresh_token)
        account.store_tokens(token_data)
        response = _post_graphql(account.access_token, query, variables)

    if not response.ok:
        logger.error("Jobber GraphQL %s: %s", response.status_code, response.text)
        raise JobberAPIError("Jobber API request failed.")

    body = response.json()
    if body.get('errors'):
        logger.error("Jobber GraphQL errors: %s", body['errors'])
        raise JobberAPIError("Jobber API returned errors.")

    return body.get('data')


def _post_graphql(access_token, query, variables):
    """Send a single GraphQL POST. Returns the raw ``requests.Response``."""
    try:
        return requests.post(
            settings.JOBBER_GRAPHQL_URL,
            json={'query': query, 'variables': variables or {}},
            headers={
                'Authorization': f"Bearer {access_token}",
                'Content-Type': 'application/json',
                'X-JOBBER-GRAPHQL-VERSION': settings.JOBBER_API_VERSION,
            },
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.error("Jobber GraphQL request failed to send: %s", exc)
        raise JobberAPIError("Could not reach the Jobber API.") from exc


# ── Convenience queries ───────────────────────────────────────────────────────

_ACCOUNT_QUERY = "query { account { id name } }"


def fetch_account_info(account):
    """
    Return ``{'id': ..., 'name': ...}`` for the connected Jobber account, or an
    empty dict if the query fails (connection stays valid regardless).
    """
    try:
        data = execute(account, _ACCOUNT_QUERY)
        return (data or {}).get('account') or {}
    except JobberAPIError:
        logger.warning("Could not fetch Jobber account info for tenant=%s", account.tenant_id)
        return {}

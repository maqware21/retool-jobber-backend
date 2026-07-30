"""
Jobber OAuth 2.0 + GraphQL client.

Thin wrapper over Jobber's public API:
  - authorize URL construction
  - authorization-code exchange and refresh-token rotation
  - authenticated GraphQL calls that transparently refresh a stale token
    and handle rate limiting with a single retry

All network failures surface as ``JobberAPIError`` so views can handle a single
exception type.
"""

import logging
import time
from urllib.parse import urlencode

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Network timeout (connect, read) for every Jobber call, in seconds.
REQUEST_TIMEOUT = 30

# Jobber's bucket refills at 500 points/second. Waiting 2 s restores 1 000 points,
# which is enough headroom for all but the heaviest single queries.
THROTTLE_RETRY_DELAY = 2.0


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

    body = response.json()

    # Per OAuth 2.0 (RFC 6749 §5.1), a provider MAY omit "scope" from the token
    # response when the granted scope matches what was requested — and Jobber
    # does exactly that; its token endpoint never returns a "scope" key.
    # Jobber's consent screen does not support partial/selectable scope grants
    # (the user approves the full requested set or denies it), so the scope we
    # requested is the scope that was granted. Fill it in here so every caller
    # (store_tokens included) sees a usable value instead of storing None.
    if not body.get('scope'):
        body['scope'] = settings.JOBBER_SCOPES

    return body


# ── GraphQL ─────────────────────────────────────────────────────────────────

def _log_throttle_status(body, tenant_id):
    """Log the throttle bucket state returned in every Jobber response."""
    throttle = (
        (body.get('extensions') or {})
        .get('cost', {})
        .get('throttleStatus', {})
    )
    if throttle:
        logger.info(
            "Jobber throttle status tenant=%s: %s/%s pts available (restore %s pts/s)",
            tenant_id,
            throttle.get('currentlyAvailable'),
            throttle.get('maximumAvailable'),
            throttle.get('restoreRate'),
        )


def _is_throttled(body):
    """Return True when Jobber signalled a THROTTLED cost error."""
    return any(
        (err.get('extensions') or {}).get('code') == 'THROTTLED'
        for err in (body.get('errors') or [])
    )


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

    Token handling:
      - Refreshes a stale token up front.
      - Retries once on 401 in case Jobber invalidated the token early.

    Rate-limit handling:
      - Logs ``throttleStatus`` from every response for visibility.
      - On a THROTTLED response, waits ``THROTTLE_RETRY_DELAY`` seconds and
        retries once. Still throttled after that → raises JobberAPIError with
        a distinct message so callers can tell "throttled" from other failures.

    Error handling:
      - Standard GraphQL errors arrive as a top-level ``errors`` array.
      - Two non-standard shapes are also checked explicitly (see comments).

    Returns the ``data`` object from the response.
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
    _log_throttle_status(body, account.tenant_id)

    # ── Rate limit handling ────────────────────────────────────────────────────
    if _is_throttled(body):
        logger.warning(
            "Jobber API throttled for tenant=%s; retrying after %.1fs",
            account.tenant_id,
            THROTTLE_RETRY_DELAY,
        )
        time.sleep(THROTTLE_RETRY_DELAY)
        response = _post_graphql(account.access_token, query, variables)
        body = response.json()
        _log_throttle_status(body, account.tenant_id)
        if _is_throttled(body):
            raise JobberAPIError(
                "Jobber API rate limit exceeded — still throttled after retry. "
                "Reduce query cost or add delays between requests."
            )

    # ── Non-standard error shapes ──────────────────────────────────────────────
    # Jobber uses these two shapes for auth-level rejections instead of the
    # normal GraphQL top-level "errors" array:

    # Singular "error" object: returned when the connected Jobber account is
    # inactive (e.g. subscription lapsed).
    if 'error' in body:
        msg = (body['error'] or {}).get('message', 'Jobber returned an account error')
        raise JobberAPIError(msg)

    # Root-level "message" without a "data" key: returned when the Jobber user
    # has disconnected the app from their account and the token is no longer valid.
    if 'data' not in body and 'message' in body:
        raise JobberAPIError(body['message'])

    # ── Standard GraphQL errors ────────────────────────────────────────────────
    if body.get('errors'):
        logger.error("Jobber GraphQL errors for tenant=%s: %s", account.tenant_id, body['errors'])
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


# ── Convenience queries / mutations ───────────────────────────────────────────

_ACCOUNT_QUERY = "query { account { id name } }"

# Notifies Jobber that we (not the user) initiated the disconnect. Jobber
# immediately invalidates all tokens for the app on that account.
_APP_DISCONNECT_MUTATION = "mutation { appDisconnect { userErrors { message } } }"


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


def call_app_disconnect(account):
    """
    Notify Jobber that we are initiating the disconnection of ``account``.

    Best-effort: if the call fails (e.g. tokens already invalid, account
    already disconnected on Jobber's side), the error is logged and swallowed
    so the caller can still proceed with local cleanup.
    """
    try:
        execute(account, _APP_DISCONNECT_MUTATION)
        logger.info("appDisconnect mutation succeeded for tenant=%s", account.tenant_id)
    except JobberAPIError as exc:
        logger.warning(
            "appDisconnect mutation failed for tenant=%s (proceeding with local cleanup): %s",
            account.tenant_id,
            exc,
        )

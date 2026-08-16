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
from django.utils import timezone

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

# lineItems: re-added per TL approval (2026-08-05) — jobCosting remains
# excluded (still no need for it here). lineItems.category is still a
# confirmed dead end (PRODUCT/SERVICE only, never a trade taxonomy) and is
# NOT queried; only linkedProductOrService.name is, for a free-text "Trade"
# label. Only the first line item is fetched — that's all _job_service_type
# uses. client.tags: added per TL approval for a free-text Accounts "Type"
# column (Tag only has id/label — NOT name — confirmed against the schema).
#
# This query is shared by three consumers: the Jobs single-page endpoint,
# and the Accounts/Employees full-pulls (via client.fetch_all_pages). Widening
# it adds a small per-request cost to ALL THREE, not just Jobs — acceptable
# per TL's approval, revisit if a real account's job list grows large enough
# to matter.
#
# The Phase 2 local-sync engine (apps/jobber/services/sync.py) needs two
# fields these three live consumers don't: jobCosting (labour_cost /
# labour_duration_seconds on JobberJob) and each visit's own id (needed to
# key JobberVisit rows — the three live views only ever read
# visit.assignedUsers, never the visit's own identity). Rather than widen
# THIS query and add that cost to all three live consumers for something
# only the sync engine needs, there's a separate _SYNC_JOBS_QUERY /
# fetch_jobs_for_sync() below, used only by sync.py. Revisit merging the two
# if/when jobs.py/accounts.py/employees.py migrate to reading from the local
# tables instead of calling Jobber live (see the design doc's
# endpoint-migration section) — at that point this query has only one
# caller left (the sync engine) and the two can merge safely.
_JOBS_QUERY = """
query GetJobs($first: Int!, $after: String) {
  jobs(first: $first, after: $after) {
    nodes {
      id
      jobNumber
      title
      instructions
      jobStatus
      total
      startAt
      createdAt
      client { id name tags(first: 5) { nodes { label } } }
      property { street city province postalCode }
      lineItems(first: 1) {
        nodes {
          linkedProductOrService { name }
        }
      }
      visits(first: 10) {
        nodes {
          assignedUsers(first: 5) { nodes { id name { full } } }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


# client.tags added per TL approval — same free-text Accounts "Type" reasoning
# as _JOBS_QUERY. Shared by the Invoices single-page endpoint and Accounts'
# full-pull; the extra field is fetched but unused by Invoices itself.
_INVOICES_QUERY = """
query GetInvoices($first: Int!, $after: String) {
  invoices(first: $first, after: $after) {
    nodes {
      id
      invoiceNumber
      total
      issuedDate
      dueDate
      invoiceStatus
      amounts { invoiceBalance }
      client { id name tags(first: 5) { nodes { label } } }
      jobs(first: 3) { nodes { jobNumber } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


_USERS_QUERY = """
query GetUsers($first: Int!, $after: String) {
  users(first: $first, after: $after) {
    nodes {
      id
      name { full }
      isAccountAdmin
      isAccountOwner
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


# New for the sync engine — Clients were previously only ever seen nested
# inside Job/Invoice nodes (client { id name tags }), never pulled
# independently. Not shared with any live-proxy view, so no cost tradeoff
# to weigh here the way there is for _SYNC_JOBS_QUERY below.
_CLIENTS_QUERY = """
query GetClients($first: Int!, $after: String) {
  clients(first: $first, after: $after) {
    nodes {
      id
      name
      tags(first: 5) { nodes { label } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


# Sync-only — see the comment on _JOBS_QUERY above for why this isn't just
# _JOBS_QUERY widened in place. Adds jobCosting (for JobberJob.labour_cost /
# labour_duration_seconds), each visit's own id (for JobberVisit.jobber_id
# — visits are synced by extracting them from these same job nodes, not via
# a separate top-level query; see sync.py's sync_visits() docstring for why),
# and completedAt (for JobberJob.completed_at — confirmed live 2026-08-16 to
# track when invoicing clears, not when work physically finished; see the
# model field's own comment for the full verification).
_SYNC_JOBS_QUERY = """
query GetJobsForSync($first: Int!, $after: String) {
  jobs(first: $first, after: $after) {
    nodes {
      id
      jobNumber
      title
      instructions
      jobStatus
      total
      startAt
      createdAt
      completedAt
      client { id name tags(first: 5) { nodes { label } } }
      property { street city province postalCode }
      lineItems(first: 1) {
        nodes {
          linkedProductOrService { name }
        }
      }
      jobCosting { labourCost labourDuration }
      visits(first: 10) {
        nodes {
          id
          assignedUsers(first: 5) { nodes { id name { full } } }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

# Safety cap per collection for fetch_all_pages(): 20 pages x 25 records =
# 500 records. Endpoints that need a complete picture (rankings, rosters —
# where partial data could produce a WRONG result, not just an incomplete
# list) use this instead of a single live-proxy page.
FETCH_ALL_PAGE_SIZE = 25
FETCH_ALL_MAX_PAGES = 20


def fetch_all_pages(fetch_fn, account, label, first=FETCH_ALL_PAGE_SIZE, max_pages=FETCH_ALL_MAX_PAGES):
    """
    Loop ``fetch_fn(account, first=first, after=cursor)`` following the real
    ``page_info.end_cursor`` until Jobber reports no more pages, or
    ``max_pages`` is hit. Returns the full list of nodes collected.

    If the cap is hit while more pages still exist, logs a clear warning —
    the caller's result is based on a bounded sample, not literally
    everything, and that should be visible in the logs, not silent.
    """
    all_nodes = []
    cursor = None
    for _page_num in range(max_pages):
        page = fetch_fn(account, first=first, after=cursor)
        all_nodes.extend(page.get('nodes') or [])
        page_info = page.get('pageInfo') or {}
        if not page_info.get('hasNextPage'):
            return all_nodes
        cursor = page_info.get('endCursor')

    logger.warning(
        "%s: hit the %d-page safety cap (%d records) for tenant=%s — "
        "result is based on a bounded sample, not the full account.",
        label, max_pages, max_pages * first, account.tenant_id,
    )
    return all_nodes


def fetch_all_pages_bounded(fetch_fn, account, label, deadline, first=FETCH_ALL_PAGE_SIZE, max_pages=FETCH_ALL_MAX_PAGES):
    """
    Same pagination loop as fetch_all_pages(), for the sync engine
    specifically (apps/jobber/services/sync.py). Two differences, both
    needed for the local-sync design doc's §3 wall-clock ceiling and §2's
    deactivation-sweep fix:

      - Checks ``deadline`` (a timezone-aware datetime) before starting each
        new page fetch — stops cleanly, without starting one more Jobber
        call, once the whole-sync wall-clock ceiling is reached.
      - Returns ``(nodes, complete)`` instead of just nodes. ``complete`` is
        True only when Jobber's own hasNextPage said there was nothing left;
        False on a deadline stop OR on hitting max_pages, since either way
        ``nodes`` may not be every record that exists. sync.py uses this to
        decide whether an entity's deactivation sweep is safe to run this
        pass — running it against a partial node list would incorrectly
        deactivate real, still-active records that simply weren't re-seen
        this run.

    A sibling to fetch_all_pages(), not a replacement for it — kept
    completely separate so fetch_all_pages()'s three existing live-proxy
    callers (jobs.py's single-page view aside, accounts.py's and
    employees.py's full-pulls) are entirely unaffected; their return
    contract (a plain node list, no deadline) doesn't change.
    """
    all_nodes = []
    cursor = None
    for _page_num in range(max_pages):
        if timezone.now() >= deadline:
            logger.warning(
                "%s: sync wall-clock ceiling reached for tenant=%s after %d record(s) — "
                "stopping this entity's pull short this run.",
                label, account.tenant_id, len(all_nodes),
            )
            return all_nodes, False

        page = fetch_fn(account, first=first, after=cursor)
        all_nodes.extend(page.get('nodes') or [])
        page_info = page.get('pageInfo') or {}
        if not page_info.get('hasNextPage'):
            return all_nodes, True
        cursor = page_info.get('endCursor')

    logger.warning(
        "%s: hit the %d-page safety cap (%d records) for tenant=%s — "
        "result is based on a bounded sample, not the full account this run.",
        label, max_pages, max_pages * first, account.tenant_id,
    )
    return all_nodes, False


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


def fetch_jobs(account, first=25, after=None):
    """
    Return the raw ``jobs`` connection for ``account``:
    ``{'nodes': [...], 'pageInfo': {'hasNextPage': ..., 'endCursor': ...}}``.

    Live proxy — no local caching. Raises JobberAPIError on failure like
    every other call through ``execute()``; callers decide how to surface it.
    """
    data = execute(account, _JOBS_QUERY, {'first': first, 'after': after})
    return (data or {}).get('jobs') or {
        'nodes': [],
        'pageInfo': {'hasNextPage': False, 'endCursor': None},
    }


def fetch_jobs_for_sync(account, first=25, after=None):
    """
    Sync-only variant of fetch_jobs() — same return shape, but via
    _SYNC_JOBS_QUERY (adds jobCosting and each visit's own id, neither
    needed by the three live-proxy consumers of fetch_jobs()/_JOBS_QUERY).
    Used only by apps/jobber/services/sync.py.
    """
    data = execute(account, _SYNC_JOBS_QUERY, {'first': first, 'after': after})
    return (data or {}).get('jobs') or {
        'nodes': [],
        'pageInfo': {'hasNextPage': False, 'endCursor': None},
    }


def fetch_clients(account, first=25, after=None):
    """
    Return the raw ``clients`` connection for ``account``:
    ``{'nodes': [...], 'pageInfo': {'hasNextPage': ..., 'endCursor': ...}}``.

    New for the sync engine — no live-proxy view fetches Clients
    independently today. Raises JobberAPIError on failure like every other
    call through ``execute()``.
    """
    data = execute(account, _CLIENTS_QUERY, {'first': first, 'after': after})
    return (data or {}).get('clients') or {
        'nodes': [],
        'pageInfo': {'hasNextPage': False, 'endCursor': None},
    }


def fetch_invoices(account, first=25, after=None):
    """
    Return the raw ``invoices`` connection for ``account``:
    ``{'nodes': [...], 'pageInfo': {'hasNextPage': ..., 'endCursor': ...}}``.

    Live proxy — no local caching. Raises JobberAPIError on failure like
    every other call through ``execute()``; callers decide how to surface it.
    """
    data = execute(account, _INVOICES_QUERY, {'first': first, 'after': after})
    return (data or {}).get('invoices') or {
        'nodes': [],
        'pageInfo': {'hasNextPage': False, 'endCursor': None},
    }


def fetch_users(account, first=25, after=None):
    """
    Return the raw ``users`` connection for ``account``:
    ``{'nodes': [...], 'pageInfo': {'hasNextPage': ..., 'endCursor': ...}}``.

    Live proxy — no local caching. Raises JobberAPIError on failure like
    every other call through ``execute()``; callers decide how to surface it.
    """
    data = execute(account, _USERS_QUERY, {'first': first, 'after': after})
    return (data or {}).get('users') or {
        'nodes': [],
        'pageInfo': {'hasNextPage': False, 'endCursor': None},
    }


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

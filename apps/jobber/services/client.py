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
    """
    Log the throttle bucket state AND the cost of the query that just
    produced it.

    requestedQueryCost/actualQueryCost added (2026-08-28) -- confirmed
    real gap: these are sibling fields of throttleStatus under
    extensions.cost (extensions.cost = {requestedQueryCost,
    actualQueryCost, throttleStatus}, per Jobber's own API rate-limits
    docs), not previously logged at all. They're the exact missing
    evidence that would have explained a real, confirmed paradox this
    project hit -- a response reporting a FULL throttleStatus bucket
    (currentlyAvailable == maximumAvailable) while ALSO carrying a
    THROTTLED error -- which is otherwise unexplainable from
    throttleStatus alone. See verify_jobber_trivial_query.py's docstring
    for the full incident this fixes visibility into.
    """
    cost = (body.get('extensions') or {}).get('cost') or {}
    throttle = cost.get('throttleStatus') or {}
    if throttle:
        logger.info(
            "Jobber throttle status tenant=%s: %s/%s pts available (restore %s pts/s) "
            "-- requestedQueryCost=%s actualQueryCost=%s",
            tenant_id,
            throttle.get('currentlyAvailable'),
            throttle.get('maximumAvailable'),
            throttle.get('restoreRate'),
            cost.get('requestedQueryCost'),
            cost.get('actualQueryCost'),
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

    Rate-limit handling — Jobber has TWO distinct limiters, handled
    separately (2026-08-28, confirmed against Jobber's own docs):
      - GraphQL query-cost leaky bucket: logs ``throttleStatus`` (plus
        requestedQueryCost/actualQueryCost) from every response for
        visibility. On a THROTTLED response, waits ``THROTTLE_RETRY_DELAY``
        seconds and retries once. Still throttled after that → raises
        JobberAPIError with a distinct message so callers can tell
        "throttled" from other failures.
      - DDoS-layer/Rack::Attack request-count limiter (2500 req/5min per
        app/account): surfaces as a raw HTTP 429, not a body-level error —
        logged and raised with its own distinct message, no retry (no
        considered backoff policy for this limiter yet). Previously fell
        into the generic "not response.ok" failure below, indistinguishable
        from any other HTTP error.

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

    # New (2026-08-28) -- a raw HTTP 429 is Jobber's DDoS-layer/Rack::Attack
    # request-count limiter (2500 req/5min per app/account, confirmed via
    # Jobber's own API rate-limits docs) — a SEPARATE mechanism from the
    # GraphQL query-cost throttle handled below (extensions.cost.
    # throttleStatus / _is_throttled()). Before this, a 429 fell straight
    # into the generic "not response.ok" branch below and raised the same
    # message as any other failure (a bad query, a 500, anything) — a real
    # confirmed gap: this project had to guess after the fact whether a
    # real failure was this limiter or the cost-bucket one, instead of
    # being told directly. Logged and raised distinctly now so that
    # distinction is immediate, not inferred. No retry attempted here —
    # this project doesn't yet have a considered backoff policy for THIS
    # limiter specifically (distinct from THROTTLE_RETRY_DELAY below,
    # which is tuned for the cost bucket's restore rate, not this one).
    if response.status_code == 429:
        logger.error(
            "Jobber DDoS-layer rate limit hit (HTTP 429) for tenant=%s -- the "
            "Rack::Attack request-count limiter, NOT the GraphQL query-cost "
            "throttle. No retry attempted.",
            account.tenant_id,
        )
        raise JobberAPIError(
            "Jobber API rate limit exceeded (HTTP 429 -- request-count limiter, "
            "distinct from query cost). Reduce request frequency."
        )

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


# customFields is a UNION (CustomFieldUnion -- confirmed against the
# schema): [CustomFieldArea | CustomFieldDropdown | CustomFieldLink |
# CustomFieldNumeric | CustomFieldText | CustomFieldTrueFalse]. Only the
# 2 branches below are spread -- the CONFIRMED real shapes for this
# account's "Expertise" (CustomFieldText) and "Experience"
# (CustomFieldNumeric) Team custom fields (confirmed live, 2026-08-20). A
# different tenant's custom field of the same name
# configured as a different type (or not configured at all) simply comes
# back without a `label` match for our spread fragments -- GraphQL does
# not error on an unmatched union member, it just omits the fields we
# didn't ask for -- so this degrades to a clean, no-op skip in
# _extract_custom_field() below, never a crash.
_USERS_QUERY = """
query GetUsers($first: Int!, $after: String) {
  users(first: $first, after: $after) {
    nodes {
      id
      name { full }
      phone { friendly }
      isAccountAdmin
      isAccountOwner
      customFields {
        ... on CustomFieldText { label valueText }
        ... on CustomFieldNumeric { label valueNumeric }
      }
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
# completedAt (for JobberJob.completed_at — confirmed live 2026-08-16 to
# track when invoicing clears, not when work physically finished; see the
# model field's own comment for the full verification), and timeSheetEntries
# (for JobberTimeSheetEntry — same "no viable standalone root query" situation
# as Visits, confirmed against the schema: Query.timeSheetEntries exists but
# has no job filter at all, so this is the only path; see
# JobberTimeSheetEntry's model docstring and sync.py's sync_timesheet_entries()).
# labourRate added 2026-09-03 (approved labor_cost_profit_margin_proposal.md)
# for JobberTimeSheetEntry.labour_rate — a REAL, native per-entry Jobber
# wage rate, confirmed via verify_labour_rate_field.py; a DIFFERENT field
# from jobCosting.labourCost above (already confirmed broken/always 0).
# lineItemCost added 2026-09-07 (approved revenue_composition_expense_
# profit_proposal.md) for JobberJob.line_item_cost — one more scalar
# field on the jobCosting object already being fetched here for every
# job, not a new query or a new per-job cost category. Confirmed
# genuine and non-circular (see PROJECT_CONTEXT.md's 2026-09-07 update);
# a DIFFERENT field from jobCosting.expenseCost, which stays confirmed
# dead (0.0 on every job checked) and is deliberately NOT requested here.
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
      jobCosting { labourCost labourDuration lineItemCost }
      visits(first: 10) {
        nodes {
          id
          assignedUsers(first: 5) { nodes { id name { full } } }
        }
      }
      timeSheetEntries(first: 25) {
        nodes {
          id
          startAt
          endAt
          createdAt
          finalDuration
          labourRate
          user { id }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

# Callback-detection-only (2026-08-30, approved callback_hours_design.md) —
# called at most ONCE per job, exactly when that job's first_archived_at is
# first set (see sync.py's detect_and_freeze_callbacks()). Deliberately NOT
# folded into _SYNC_JOBS_QUERY above — that query runs for EVERY job on
# EVERY sync pass, but this one only ever needs to run for the handful of
# jobs that just transitioned to archived. Keeping it separate avoids
# paying this cost on every job, every pass — the same "don't widen a
# shared query for something only one narrow caller needs" reasoning
# _SYNC_JOBS_QUERY itself already exists for. Uses Query.job(id: EncodedId!)
# — the same cheap single-job lookup verify_callback_bleed.py's fix
# confirmed real and inexpensive.
_CALLBACK_DETECTION_QUERY = """
query GetJobVisitsForCallbackDetection($id: EncodedId!) {
  job(id: $id) {
    id
    visits(first: 25) {
      nodes {
        id
        createdAt
        completedAt
        invoice { id }
      }
    }
  }
}
"""


def fetch_job_visits_for_callback_detection(account, job_id):
    """
    Real visits (id, createdAt, completedAt, invoice) for exactly one job,
    by its real jobber_id — used at most once per job by sync.py's
    detect_and_freeze_callbacks(). completedAt added (2026-08-30, PART A of
    the approved callback window addition) — needed to measure the
    CALLBACK_WINDOW_DAYS interval from the job's last COMPLETED visit
    before the reopen, not from createdAt or first_archived_at. Returns
    the raw node list (possibly empty if the job/visits aren't found —
    never raises for that case, only for a genuine JobberAPIError from
    execute()).
    """
    data = execute(account, _CALLBACK_DETECTION_QUERY, {'id': job_id})
    job_node = (data or {}).get('job') or {}
    return (job_node.get('visits') or {}).get('nodes') or []


# Safety cap per collection for fetch_all_pages(): 20 pages x 25 records =
# 500 records. Endpoints that need a complete picture (rankings, rosters —
# where partial data could produce a WRONG result, not just an incomplete
# list) use this instead of a single live-proxy page.
FETCH_ALL_PAGE_SIZE = 25
FETCH_ALL_MAX_PAGES = 20

# URGENT fix (2026-09-07) -- confirmed via diagnose_sync_jobs_query_cost.py
# against the real connected account: a real request at FETCH_ALL_PAGE_SIZE
# (25) for fetch_jobs_for_sync() now costs requestedQueryCost=10380,
# rejected outright against Jobber's 10000 ceiling (actualQueryCost=0) --
# real jobs have accumulated increasing nested visits/timeSheetEntries
# over many testing rounds, and _SYNC_JOBS_QUERY's per-job cost (lineItems,
# jobCosting, visits+assignedUsers, timeSheetEntries, all nested) is now too
# high at this page size. This broke ALL of this account's synced data,
# not just one entity -- sync_tenant() calls sync_jobs() unconditionally
# whenever 'jobs'/'visits'/'timesheet_entries' are requested, and a
# JobberAPIError here aborts the whole sync pass.
#
# CONFIRMED (not assumed) before choosing where to fix this: grepped every
# real call site of fetch_all_pages()/fetch_all_pages_bounded() in this
# project -- Clients, Users, and Invoices (both the sync engine's own
# fetch_clients/fetch_users/fetch_invoices AND the live-proxy Accounts/
# Employees endpoints' fetch_jobs/fetch_invoices/fetch_users) all rely on
# FETCH_ALL_PAGE_SIZE's shared default, none pass an explicit override.
# Real diagnostic results at OTHER sizes (same account, same real request):
# first=15 -> requestedQueryCost=6230 (succeeded), first=10 -> 4155
# (succeeded), first=5 -> 2080 (succeeded). Lowering the SHARED constant
# would touch Clients/Users/Invoices too, none of which are implicated in
# this failure -- only Jobs' own query has grown expensive. Fix: a
# SEPARATE, smaller page size passed explicitly at fetch_jobs_for_sync()'s
# own call site in sync_jobs() only -- every other entity's real,
# still-safe page size is completely untouched, still defaulting to
# FETCH_ALL_PAGE_SIZE. 10 chosen (not 15) for real headroom under the
# 10000 ceiling as this account's real jobs keep accumulating more nested
# visits/timesheet entries over time, without going small enough (5) to
# meaningfully multiply the real total request count for no added safety
# margin that matters at this account's current scale.
SYNC_JOBS_PAGE_SIZE = 10


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

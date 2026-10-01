"""
RESEARCH ONLY — completely isolated from the real app.

Does NOT call detect_and_freeze_callbacks(), does NOT read or write
is_callback/callback_bled_amount/callback_bled_amount_is_estimated
anywhere, does NOT touch any shared sync query (_JOBS_QUERY /
_SYNC_JOBS_QUERY in client.py are untouched). Pure read-only comparison
script: fetches one small, NEW, research-only field (Property.id) live,
reads everything else from already-synced local tables, and prints a
report. No writes to the database anywhere in this file.

Purpose: side-by-side comparison of the client's proposed alternative
callback-detection approach (3 signals, from a live demo) against our
own, already-proven, live is_callback flag (frozen by
detect_and_freeze_callbacks() -- see that function's own docstring for
why IT remains the sole real definition; this script never changes
that).

Per Step 1's confirmed schema finding: Property.address is genuinely
structured on Jobber's side, but Property.id itself is a real, exact
identity key -- two jobs at the same service address share the same
Property.id, no string/fuzzy address matching needed or used anywhere
here. Property.id is NOT part of the app's existing synced queries
(only street/city/province/postalCode are, and only as a pre-flattened
display string once stored locally -- see JobberJob.address) so this
script runs its own small, separate, research-only live query for it.

Signal 2 (same client) needs no new query at all -- JobberClient.jobber_id
is already synced and used directly from local data.

Signal 3 (keyword matching) scans JobberJob.title + JobberJob.description.
description is this app's real, existing "instructions or title" fallback
(see sync.py's sync_jobs() job mapping) -- NOT Jobber's separate Notes
feature (a distinct NoteConnection type, never synced anywhere in this
app). Flagged as a real, honest scope limit, not silently overstated as
"notes."

"Job type must match" safeguard: reuses JobberJob.service_type verbatim
-- this app's own existing _job_service_type() derivation (first line
item's linked product/service name), the same "job type/category"
Accounts' service_type_breakdown already uses. Explicitly NOT
Job.jobType (a real, different Jobber field -- ONE_OFF/RECURRING
scheduling cadence, confirmed against the schema -- not a service
category; using it here would have been a real, silent mistake).
Applied to Signals 1 and 2 (both are PAIRED signals -- a candidate job
compared against an earlier job at the same property/client): a pair is
only counted if both jobs have a real, non-null, EQUAL service_type. If
either side's service_type is unknown (None), the safeguard can't be
confirmed either way -- per this app's own "no data != assumed"
convention used everywhere else, an unconfirmable pair is NOT counted as
a match, rather than assumed to pass. Signal 3 has no natural pairing
partner (it's a single job's own text, not two jobs being compared) --
the safeguard genuinely does not apply to it the same way, reported
plainly here rather than forcing an artificial pairing that wasn't
specified.

Run via:
    python manage.py shell < research_alternative_callback_detection.py
on the SERVER, against tenant_id=5's real synced data. Delete afterward,
same as every other one-off script in this project.
"""
from collections import defaultdict
from datetime import timedelta

from apps.jobber.models import JobberAccount, JobberJob
from apps.jobber.services import client

TENANT_ID = 6

SAME_LOCATION_WINDOW_DAYS = 30
SAME_CLIENT_WINDOW_DAYS = 30

# Verbatim from the client's own proposal notes.
KEYWORDS = [
    'callback', 'return visit', 'redo', 'warranty', 'no charge',
    'fix previous', 'warranty call',
]

# Research-only query -- NOT _JOBS_QUERY, NOT _SYNC_JOBS_QUERY. Fetches
# only what neither of those already carries (Property.id), kept as
# small/cheap as possible on purpose.
_RESEARCH_PROPERTY_QUERY = """
query ResearchJobProperties($first: Int!, $after: String) {
  jobs(first: $first, after: $after) {
    nodes {
      id
      property {
        id
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""


def _fetch_all_property_ids(account):
    """Live-paginates the research-only query above. Returns {jobber_id: property_id or None}."""
    property_ids = {}
    after = None
    while True:
        data = client.execute(account, _RESEARCH_PROPERTY_QUERY, {'first': 50, 'after': after})
        connection = (data or {}).get('jobs') or {}
        for node in connection.get('nodes') or []:
            prop = node.get('property') or {}
            property_ids[node.get('id')] = prop.get('id')
        page_info = connection.get('pageInfo') or {}
        if not page_info.get('hasNextPage'):
            break
        after = page_info.get('endCursor')
    return property_ids


def _text_matches_keyword(title, description):
    haystack = f"{title or ''} {description or ''}".lower()
    matched = [kw for kw in KEYWORDS if kw in haystack]
    return matched


def _categorize(our_is_callback, new_flag):
    if our_is_callback and new_flag:
        return 'AGREE (both flag it)'
    if not our_is_callback and not new_flag:
        return 'AGREE (neither flags it)'
    if not our_is_callback and new_flag:
        return 'NEW APPROACH FLAGGED, OURS DID NOT'
    return 'OURS FLAGGED, NEW APPROACH DID NOT'


def _find_prior_match(job, candidates, key_fn):
    """
    Among `candidates` (all real jobs for this tenant), finds the nearest
    real prior job sharing the same key (property_id or client_jobber_id)
    as `job`, completed strictly before `job.jobber_created_at`, within
    the window, with a CONFIRMED matching service_type on both sides.
    Returns (matching_job, gap_days) or (None, None).
    """
    my_key = key_fn(job)
    if not my_key or not job.jobber_created_at:
        return None, None
    if not job.service_type:
        # Can't confirm the safeguard from our own side either -- same
        # "no data != assumed" rule applies symmetrically.
        return None, None

    best = None
    best_gap = None
    for other in candidates:
        if other.id == job.id:
            continue
        if key_fn(other) != my_key:
            continue
        if not other.completed_at:
            continue
        if other.completed_at >= job.jobber_created_at:
            continue
        if not other.service_type or other.service_type != job.service_type:
            continue
        gap = job.jobber_created_at - other.completed_at
        if gap > timedelta(days=SAME_LOCATION_WINDOW_DAYS):
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = other, gap
    return best, (best_gap.days if best_gap is not None else None)


def main():
    account = JobberAccount.objects.filter(tenant_id=TENANT_ID, is_active=True).first()
    if account is None:
        print(f"No active JobberAccount for tenant_id={TENANT_ID}.")
        return

    jobs = list(
        JobberJob.objects.filter(tenant_id=TENANT_ID, is_active=True)
        .select_related('client')
        .order_by('job_number')
    )
    if not jobs:
        print(f"No real JobberJob rows for tenant_id={TENANT_ID}.")
        return

    print(f"=== fetching real Property.id for {len(jobs)} real job(s), live, research-only query ===\n")
    property_ids_by_jobber_id = _fetch_all_property_ids(account)

    def property_key(j):
        return property_ids_by_jobber_id.get(j.jobber_id)

    def client_key(j):
        return j.client.jobber_id if j.client_id else None

    rows = []
    # One tally per rule -- kept separate on purpose, never merged, so
    # each rule's own real false-positive/miss counts are directly
    # comparable side by side.
    tally_a = defaultdict(int)
    tally_b = defaultdict(int)
    tally_c = defaultdict(int)
    tally_d = defaultdict(int)

    for job in jobs:
        our_is_callback = job.visits.filter(is_callback=True, is_active=True).exists()

        s1_match, s1_gap = _find_prior_match(job, jobs, property_key)
        s2_match, s2_gap = _find_prior_match(job, jobs, client_key)
        s3_keywords = _text_matches_keyword(job.title, job.description)

        has_s1 = bool(s1_match)
        has_s2 = bool(s2_match)
        has_s3 = bool(s3_keywords)

        # RULE A (unchanged, existing single-signal rule).
        rule_a = has_s1 or has_s2 or has_s3

        # RULE B: a genuine count of independently-agreeing signals --
        # address and client counted SEPARATELY, exactly as they were
        # before. Real risk this rule is built specifically to surface:
        # address and client matches usually co-occur (the same job at
        # the same client is, almost by definition, also at the same
        # property), so treating them as 2 independent "votes" can let a
        # single real underlying fact alone clear a >=2 threshold with no
        # real corroboration from a genuinely different kind of evidence.
        signal_count = sum([has_s1, has_s2, has_s3])
        rule_b = signal_count >= 2

        # RULE C: address-or-client collapsed into ONE combined vote
        # (they're usually the same underlying fact observed twice, not 2
        # independent ones) -- flags only if that combined vote AND at
        # least one genuinely OTHER, independent signal (keyword) also
        # agrees. This is the one built specifically to require real
        # corroboration from a different kind of evidence, not just the
        # same fact counted twice.
        rule_c = (has_s1 or has_s2) and has_s3

        # RULE D: all 3 required simultaneously -- address AND client AND
        # keyword, not any combination of 2. The strictest of the 4 --
        # real corroboration from every distinct kind of evidence at once,
        # at the cost of requiring a keyword hit even when address+client
        # both genuinely agree (see Rule D's own real result on E2/F1/F2
        # below vs. Rule C's).
        rule_d = has_s1 and has_s2 and has_s3

        category_a = _categorize(our_is_callback, rule_a)
        category_b = _categorize(our_is_callback, rule_b)
        category_c = _categorize(our_is_callback, rule_c)
        category_d = _categorize(our_is_callback, rule_d)
        tally_a[category_a] += 1
        tally_b[category_b] += 1
        tally_c[category_c] += 1
        tally_d[category_d] += 1

        rows.append({
            'job_number': job.job_number,
            'our_is_callback': our_is_callback,
            's1': f"job #{s1_match.job_number} ({s1_gap}d)" if s1_match else None,
            's2': f"job #{s2_match.job_number} ({s2_gap}d)" if s2_match else None,
            's3': ', '.join(s3_keywords) if s3_keywords else None,
            'rule_a': rule_a,
            'rule_b': rule_b,
            'rule_c': rule_c,
            'rule_d': rule_d,
            'category_a': category_a,
            'category_b': category_b,
            'category_c': category_c,
            'category_d': category_d,
        })

    print(
        f"{'Job':>6} | {'Ours':>5} | {'Sig1 (address)':^22} | {'Sig2 (client)':^22} | "
        f"{'Sig3 (keyword)':^18} | {'RuleA':^6} | {'RuleB':^6} | {'RuleC':^6} | {'RuleD':^6}"
    )
    print('-' * 140)
    for r in rows:
        print(
            f"{'#' + str(r['job_number']):>6} | {str(r['our_is_callback']):>5} | "
            f"{(r['s1'] or '-'):^22} | {(r['s2'] or '-'):^22} | {(r['s3'] or '-'):^18} | "
            f"{str(r['rule_a']):^6} | {str(r['rule_b']):^6} | {str(r['rule_c']):^6} | {str(r['rule_d']):^6}"
        )

    for label, tally in (('RULE A (any 1 signal)', tally_a), ('RULE B (>=2 independent signals)', tally_b),
                          ('RULE C (address-or-client AS ONE, plus keyword)', tally_c),
                          ('RULE D (address AND client AND keyword, all 3 required)', tally_d)):
        print(f"\n=== {label}: summary ===")
        for category, count in tally.items():
            print(f"  {category}: {count}")

    # Rows where the 4 rules don't all agree with each other -- these are
    # exactly the real, concrete cases worth looking at by hand, not
    # hardcoded to any specific job number (this tenant's real test jobs
    # today, or a different real account's jobs tomorrow).
    disagreements = [r for r in rows if len({r['rule_a'], r['rule_b'], r['rule_c'], r['rule_d']}) > 1]
    print("\n=== jobs where Rule A / B / C / D do NOT all agree with each other ===")
    if not disagreements:
        print("  none -- all 4 rules agreed on every real job.")
    for r in disagreements:
        print(
            f"  job #{r['job_number']}: ours={r['our_is_callback']} | "
            f"RuleA={r['rule_a']} ({r['category_a']}) | "
            f"RuleB={r['rule_b']} ({r['category_b']}) | "
            f"RuleC={r['rule_c']} ({r['category_c']}) | "
            f"RuleD={r['rule_d']} ({r['category_d']})"
        )

    print(
        "\nNote: Signal 3 (keyword) has no paired job to apply the "
        "'job type must match' safeguard against -- see this script's "
        "own module docstring. Its hits above are reported as-is, "
        "unfiltered by that safeguard, under all 3 rules."
    )


main()

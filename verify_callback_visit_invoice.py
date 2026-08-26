"""
Live Jobber API verification for the real callback test case (2026-08-26).
Run via `python manage.py shell < verify_callback_visit_invoice.py`.

Real test case (tenant_id=1's connected Jobber account): Job Number 3 on
the Jobber dashboard (the job's dashboard URL shows id 155158173, which is
Jobber's internal record id, NOT a usable GraphQL EncodedId -- see the
lookup note below). First visit was completed and invoiced, the job was
then reopened, a genuine second visit was added and marked complete, and
the job was closed again -- landing directly back in Archived rather than
"Requires Invoicing."

Hits Jobber's LIVE GraphQL API directly, NOT local synced tables --
confirmed against apps/jobber/models.py that JobberVisit only stores
assigned_user/assigned_users, jobber_id, and synced_at today; none of
startAt/endAt/createdAt/isComplete/visitStatus/invoice are mirrored
locally, and this R&D specifically needs the real Visit.invoice field,
which nothing local currently captures.

Job lookup note: the schema's JobFilterAttributes (confirmed via
introspection) has no jobNumber filter and no raw-integer-id filter --
only `ids: [EncodedId!]`, `status`, and date-range filters. Query.job(id:
EncodedId!) exists but needs a real EncodedId, and there's no confirmed
encoding to derive one from the dashboard's raw URL id (155158173) without
guessing -- not done here. Instead this paginates the account's own real
`jobs` connection (same pagination shape already used by fetch_all_pages
elsewhere in this project) and matches client-side on the real `jobNumber`
field returned by Jobber itself.

Reports, plainly, no detection logic:
  1) Every visit on the job, in real Jobber connection order AND
     re-sorted by createdAt (since connection order is not documented as
     chronological) -- id, startAt, endAt, createdAt, isComplete,
     visitStatus, and the visit's own `invoice` field (id + total if
     present, explicitly None if not).
  2) The job's own jobStatus, plus every invoice linked via Job.invoices
     (id, issuedDate, total).
  3) Directly states whether the createdAt-sorted SECOND visit's invoice
     is null while the job's own jobStatus still reads archived -- the
     specific real claim this script exists to check.
"""
from apps.jobber.models import JobberAccount
from apps.jobber.services.client import FETCH_ALL_MAX_PAGES, FETCH_ALL_PAGE_SIZE, JobberAPIError, execute

TENANT_ID = 1
TARGET_JOB_NUMBER = 3

_JOB_LOOKUP_QUERY = """
query GetJobsForCallbackVerification($first: Int!, $after: String) {
  jobs(first: $first, after: $after) {
    nodes {
      id
      jobNumber
      jobStatus
      invoices(first: 25) {
        nodes {
          id
          issuedDate
          total
        }
      }
      visits(first: 25) {
        nodes {
          id
          startAt
          endAt
          createdAt
          isComplete
          visitStatus
          invoice {
            id
            total
          }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def _find_job_by_number(account, job_number):
    """
    Paginate the real jobs connection (same cap convention as
    fetch_all_pages) until a node with this real jobNumber is found, or
    the account's jobs are exhausted.
    """
    cursor = None
    for _page_num in range(FETCH_ALL_MAX_PAGES):
        data = execute(account, _JOB_LOOKUP_QUERY, {'first': FETCH_ALL_PAGE_SIZE, 'after': cursor})
        jobs = (data or {}).get('jobs') or {}
        for node in jobs.get('nodes') or []:
            if node.get('jobNumber') == job_number:
                return node
        page_info = jobs.get('pageInfo') or {}
        if not page_info.get('hasNextPage'):
            break
        cursor = page_info.get('endCursor')
    return None


def _print_visit(label, v):
    inv = v.get('invoice')
    print(f"\n{label} id={v.get('id')}")
    print(f"    startAt:     {v.get('startAt')}")
    print(f"    endAt:       {v.get('endAt')}")
    print(f"    createdAt:   {v.get('createdAt')}")
    print(f"    isComplete:  {v.get('isComplete')}")
    print(f"    visitStatus: {v.get('visitStatus')}")
    if inv is None:
        print("    invoice:     None")
    else:
        print(f"    invoice:     id={inv.get('id')} total={inv.get('total')}")


account = JobberAccount.objects.filter(tenant_id=TENANT_ID, is_active=True).first()
print("account:", account)

if account is None:
    print(f"No active JobberAccount for tenant_id={TENANT_ID} -- cannot query live.")
else:
    job = None
    try:
        job = _find_job_by_number(account, TARGET_JOB_NUMBER)
    except JobberAPIError as exc:
        print(f"Jobber API error while searching for job #{TARGET_JOB_NUMBER}: {exc}")

    if job is None:
        print(
            f"Job #{TARGET_JOB_NUMBER} not found across the account's real jobs "
            f"(searched up to {FETCH_ALL_MAX_PAGES * FETCH_ALL_PAGE_SIZE} jobs) -- "
            "confirm the job number or widen the search cap."
        )
    else:
        print(f"\n=== Job #{job.get('jobNumber')} (id={job.get('id')}) ===")
        print("jobStatus:", job.get('jobStatus'))

        visits = (job.get('visits') or {}).get('nodes') or []

        print(f"\n--- Visits, real connection order ({len(visits)} total) ---")
        for i, v in enumerate(visits):
            _print_visit(f"Visit[{i}]", v)

        visits_by_created = sorted(visits, key=lambda v: v.get('createdAt') or '')
        print(f"\n--- Visits, re-sorted by createdAt ascending ---")
        for i, v in enumerate(visits_by_created):
            _print_visit(f"createdAt-order[{i}]", v)

        print("\n--- Job.invoices (job-level linkage) ---")
        invoices = (job.get('invoices') or {}).get('nodes') or []
        if not invoices:
            print("  (none)")
        for inv in invoices:
            print(f"  id={inv.get('id')} issuedDate={inv.get('issuedDate')} total={inv.get('total')}")

        # --- The specific real claim this script exists to check ---
        print("\n=== The question this script exists to answer ===")
        if len(visits_by_created) < 2:
            print(
                f"Only {len(visits_by_created)} visit(s) found on this job -- expected "
                "2 (the original + the genuine callback). Re-confirm this is the right job "
                "before drawing any conclusion."
            )
        else:
            second_visit = visits_by_created[-1]
            second_invoice = second_visit.get('invoice')
            job_status = job.get('jobStatus')
            print(f"createdAt-latest visit (id={second_visit.get('id')}) invoice: {second_invoice!r}")
            print(f"Job.jobStatus: {job_status!r}")
            if second_invoice is None and job_status and job_status.lower() == 'archived':
                print(
                    "CONFIRMED: the createdAt-latest visit has invoice=null while "
                    "jobStatus=archived -- real, direct proof job-level status cannot be "
                    "relied on for this, and Visit.invoice must be checked per-visit."
                )
            else:
                print(
                    "NOT the expected pattern -- second_invoice is "
                    f"{'null' if second_invoice is None else 'non-null'} and jobStatus is "
                    f"{job_status!r}. Reporting this real combination as observed, not "
                    "forcing it into the expected shape."
                )

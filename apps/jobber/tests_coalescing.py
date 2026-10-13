"""
Burst coalescing tests for background_sync.py's real DB-backed
coalescing design. CoalescingBurstTests uses TransactionTestCase (not
TestCase) deliberately -- it exercises REAL background threads via the
real, module-level bounded ThreadPoolExecutor, which need real,
committed rows visible across connections; TestCase's own uncommitted-
transaction wrapping would hide fixture rows from those other threads
entirely.

sync_tenant() itself is stubbed (not run for real -- no live Jobber
account exists in a sandbox test) but the stub faithfully reproduces
the one real, observable side effect tenant_sync_in_flight() actually
depends on: a real JobberSyncRun row transitioning running -> finished,
with a real ~1s gap in between to simulate a real sync's duration.

Run via:
    DJANGO_SETTINGS_MODULE=tech_track_pro.settings_migration_test \
        python manage.py test apps.jobber.tests_coalescing
"""
import threading
import time
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.jobber.models import JobberAccount, JobberSyncRun
from apps.jobber.services.background_sync import (
    _run_tenant_sync_safely,
    mark_pending_resync,
    submit_tenant_sync,
    tenant_sync_in_flight,
)
from apps.tenants.models import Tenant
from helpers.constants import JOBBER_SYNC_STATUS


class CoalescingBurstTests(TransactionTestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create()
        self.account = JobberAccount.objects.create(
            tenant=self.tenant, access_token='x', refresh_token='y',
        )
        self.call_times = []
        self._lock = threading.Lock()

    def _stub_sync_tenant(self, account, entities=None):
        run = JobberSyncRun.objects.create(
            tenant=account.tenant, status=JOBBER_SYNC_STATUS[0][0], claimed_at=timezone.now(),
        )
        with self._lock:
            self.call_times.append(timezone.now())
        time.sleep(1.0)
        run.status = JOBBER_SYNC_STATUS[1][0]
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'finished_at'])
        return run  # matches sync_tenant()'s real return contract -- _did_own_pass() relies on this

    def _dispatch_like_webhook(self):
        """Mirrors webhook.py's own real _handle_job_change() dispatch logic exactly."""
        if tenant_sync_in_flight(self.tenant):
            mark_pending_resync(self.tenant)
        else:
            submit_tenant_sync(self.account, entities=['jobs'])

    def test_burst_of_3_within_150ms_produces_exactly_2_passes(self):
        with patch(
            'apps.jobber.services.background_sync.sync_tenant',
            side_effect=self._stub_sync_tenant,
        ):
            for _ in range(3):
                self._dispatch_like_webhook()
                time.sleep(0.05)

            # Main pass (~1s) + at most 1 trailing pass (~1s) + margin.
            time.sleep(2.5)

        self.assertEqual(
            len(self.call_times), 2,
            f"expected exactly 2 real sync_tenant() calls (in-flight + 1 coalesced "
            f"trailing pass) for a burst of 3 within ~150ms, got {len(self.call_times)}",
        )

    def test_later_submission_after_everything_settles_produces_a_new_pass(self):
        with patch(
            'apps.jobber.services.background_sync.sync_tenant',
            side_effect=self._stub_sync_tenant,
        ):
            self._dispatch_like_webhook()
            time.sleep(1.5)  # let the first pass fully finish, no trailing mark set
            self.assertEqual(len(self.call_times), 1)

            self._dispatch_like_webhook()
            time.sleep(1.5)

        self.assertEqual(
            len(self.call_times), 2,
            "a later submission, arriving after everything settled, must produce its own new pass",
        )


class MarkerHygieneTests(TestCase):
    """A task that lost the sync_tenant() claim must never touch the marker."""

    def setUp(self):
        self.tenant = Tenant.objects.create()
        self.account = JobberAccount.objects.create(
            tenant=self.tenant, access_token='x', refresh_token='y',
        )

    def test_task_that_lost_the_claim_leaves_a_fresh_marker_unconsumed(self):
        mark_pending_resync(self.tenant)
        self.account.refresh_from_db()
        marked_at_before = self.account.pending_resync_marked_at
        self.assertIsNotNone(marked_at_before)

        # Simulates sync_tenant() losing the claim: it returns the most
        # recent EXISTING run (already finished, started well before this
        # task began) -- sync.py's own real fallback when _claim_run()
        # returns None.
        existing_run = JobberSyncRun.objects.create(
            tenant=self.tenant, status=JOBBER_SYNC_STATUS[1][0], claimed_at=timezone.now(),
        )
        existing_run.started_at = timezone.now() - timedelta(seconds=10)
        existing_run.save(update_fields=['started_at'])

        with patch('apps.jobber.services.background_sync.sync_tenant', return_value=existing_run):
            _run_tenant_sync_safely(self.account, ['jobs'])

        self.account.refresh_from_db()
        self.assertEqual(
            self.account.pending_resync_marked_at, marked_at_before,
            "a task that lost the claim must leave a fresh marker completely untouched",
        )


class StoreTokensConcurrencySafetyTests(TestCase):
    """store_tokens() on a refresh must not clobber a concurrently-written field."""

    def test_token_refresh_does_not_overwrite_pending_resync_marked_at(self):
        tenant = Tenant.objects.create()
        account = JobberAccount.objects.create(tenant=tenant, access_token='old', refresh_token='old')

        # Loaded into memory BEFORE a concurrent process marks pending --
        # this stale in-memory copy is exactly what a bare save() would
        # have rewritten everything from, including this field.
        stale_copy = JobberAccount.objects.get(pk=account.pk)
        mark_pending_resync(tenant)
        account.refresh_from_db()
        real_marked_at = account.pending_resync_marked_at
        self.assertIsNotNone(real_marked_at)

        stale_copy.store_tokens({'access_token': 'new-token', 'token_type': 'bearer'})

        account.refresh_from_db()
        self.assertEqual(account.access_token, 'new-token')
        self.assertEqual(
            account.pending_resync_marked_at, real_marked_at,
            "a token refresh must never overwrite a field it doesn't own, even from a stale in-memory copy",
        )


class OuterSafetyNetTests(TestCase):
    """An exception outside _run_one_pass() (e.g. in the marker consume step) must be logged, never escape."""

    def test_exception_inside_marker_consume_is_logged_and_does_not_escape(self):
        tenant = Tenant.objects.create()
        account = JobberAccount.objects.create(tenant=tenant, access_token='x', refresh_token='y')

        real_run = JobberSyncRun.objects.create(
            tenant=tenant, status=JOBBER_SYNC_STATUS[1][0], claimed_at=timezone.now(),
        )
        # Bumped slightly into the future relative to its own creation so
        # it's guaranteed >= _run_tenant_sync_safely's own run_started_at,
        # captured a moment later -- status != 'running' plus this makes
        # it a genuine "own pass" per _did_own_pass()'s real contract.
        real_run.started_at = timezone.now() + timedelta(seconds=5)
        real_run.save(update_fields=['started_at'])

        with patch('apps.jobber.services.background_sync.sync_tenant', return_value=real_run), \
             patch(
                 'apps.jobber.services.background_sync._consume_pending_resync',
                 side_effect=RuntimeError('simulated failure inside marker consume'),
             ), \
             self.assertLogs('apps.jobber.services.background_sync', level='ERROR') as logs:
            _run_tenant_sync_safely(account, ['jobs'])  # must not raise

        self.assertTrue(
            any('unexpected error in _run_tenant_sync_safely' in message for message in logs.output),
            f"expected the outer safety net to log the error; got: {logs.output}",
        )

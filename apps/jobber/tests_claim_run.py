"""
Functional (not concurrency) checks for _claim_run() -- confirms adding
the JobberAccount lock didn't change its existing, already-correct
single-threaded behavior. The actual concurrency race this function was
just fixed for cannot be reproduced on SQLite (see claim_race_test.py's
own docstring for why); this only covers what a single-threaded sandbox
test can.

Run via:
    DJANGO_SETTINGS_MODULE=tech_track_pro.settings_migration_test \
        python manage.py test apps.jobber.tests_claim_run
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.jobber.models import JobberAccount, JobberSyncRun
from apps.jobber.services.sync import _claim_run
from apps.tenants.models import Tenant
from helpers.constants import JOBBER_SYNC_STATUS


class ClaimRunTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create()
        self.account = JobberAccount.objects.create(tenant=self.tenant, access_token='x', refresh_token='y')

    def test_returns_none_while_a_fresh_running_row_exists(self):
        JobberSyncRun.objects.create(
            tenant=self.tenant, status=JOBBER_SYNC_STATUS[0][0], claimed_at=timezone.now(),
        )
        self.assertIsNone(_claim_run(self.tenant))

    def test_reclaims_a_stuck_running_row(self):
        stuck = JobberSyncRun.objects.create(
            tenant=self.tenant, status=JOBBER_SYNC_STATUS[0][0],
            claimed_at=timezone.now() - timedelta(minutes=10),  # older than SYNC_RUN_STALE_AFTER (5 min)
        )

        new_run = _claim_run(self.tenant)

        self.assertIsNotNone(new_run)
        self.assertNotEqual(new_run.pk, stuck.pk)
        self.assertEqual(new_run.status, JOBBER_SYNC_STATUS[0][0])
        stuck.refresh_from_db()
        self.assertEqual(stuck.status, JOBBER_SYNC_STATUS[3][0])

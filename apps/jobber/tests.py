"""
Sandbox tests for cross_job_callback_detection.py's event-driven,
self-correcting rescan/sticky-match design. Run against the throwaway
settings_migration_test SQLite setup, never the real database:

    DJANGO_SETTINGS_MODULE=tech_track_pro.settings_migration_test \
        python manage.py test apps.jobber

Each test builds the minimal real rows needed (Tenant, JobberClient,
JobberUser, JobberJob, JobberTimeSheetEntry) and calls the real,
unmodified detect_cross_job_callbacks()/_needs_rescan() directly --
nothing here is mocked except the live Jobber API call
(fetch_job_visits_for_callback_detection), which has no real account to
call in a sandbox test.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.jobber.api.technician_stats import (
    _accumulate_technician_cross_job_callback_stats,
    _merge_callback_stats,
)
from apps.jobber.models import JobberClient, JobberJob, JobberTimeSheetEntry, JobberUser, JobberVisit
from apps.jobber.services import client
from apps.jobber.services.cross_job_callback_detection import (
    CROSS_JOB_CALLBACK_AMOUNT_RESCAN_CEILING_DAYS,
    CROSS_JOB_CALLBACK_WINDOW_DAYS,
    _needs_rescan,
    detect_cross_job_callbacks,
)
from apps.tenants.models import Tenant

_PROPERTY_ID = 'prop-1'
_SERVICE_TYPE = 'Standard Outlet Install'


class CrossJobCallbackDetectionTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create()
        self.client_row = JobberClient.objects.create(
            tenant=self.tenant, jobber_id='client-1', name='Test Client', synced_at=timezone.now(),
        )
        self.user = JobberUser.objects.create(
            tenant=self.tenant, jobber_id='user-1', name='Test Tech', synced_at=timezone.now(),
        )
        # account=None is fine for every test here EXCEPT the live-fetch
        # ones, which patch fetch_job_visits_for_callback_detection
        # directly and never actually touch `account`.
        self.account = None

    def _make_job(self, job_number, created_at, completed_at=None, total=None,
                   title='Standard job', service_type=_SERVICE_TYPE, property_id=_PROPERTY_ID):
        return JobberJob.objects.create(
            tenant=self.tenant,
            client=self.client_row,
            jobber_id=f'job-{job_number}',
            job_number=job_number,
            title=title,
            description=title,
            job_status='archived' if completed_at else 'requires_invoicing',
            status_display='Archived' if completed_at else 'Requires Invoicing',
            service_type=service_type,
            total=total if total is not None else Decimal('0'),
            jobber_created_at=created_at,
            completed_at=completed_at,
            property_id=property_id,
            synced_at=timezone.now(),
        )

    def _log_hours(self, job, seconds, rate=None):
        return JobberTimeSheetEntry.objects.create(
            tenant=self.tenant,
            job=job,
            user=self.user,
            jobber_id=f'entry-{job.id}-{seconds}',
            final_duration_seconds=seconds,
            labour_rate=rate,
            jobber_created_at=job.jobber_created_at,
            synced_at=timezone.now(),
        )

    # ── 1. Sticky match ──────────────────────────────────────────────────

    def test_sticky_match_never_changes_once_set(self):
        now = timezone.now()
        earlier_a = self._make_job(1, created_at=now - timedelta(days=25), completed_at=now - timedelta(days=20), total=Decimal('1800'))
        self._log_hours(earlier_a, 4 * 3600)
        later = self._make_job(2, created_at=now - timedelta(days=1), title='Follow-up visit — no charge')
        self._log_hours(later, 5 * 3600)

        detect_cross_job_callbacks(self.account, self.tenant)
        later.refresh_from_db()
        self.assertEqual(later.cross_job_callback_of_id, earlier_a.id)

        # A NEARER earlier job appears after the fact.
        earlier_b = self._make_job(3, created_at=now - timedelta(days=25), completed_at=now - timedelta(days=2), total=Decimal('900'))
        self._log_hours(earlier_b, 2 * 3600)

        detect_cross_job_callbacks(self.account, self.tenant)
        later.refresh_from_db()
        self.assertEqual(
            later.cross_job_callback_of_id, earlier_a.id,
            "match must stay sticky to the ORIGINAL earlier job, never switch to a nearer one found later",
        )

    # ── 2. unknown -> estimated -> labour mode (detection stops estimating) ──

    def test_amount_unknown_then_estimated_then_labour_mode(self):
        now = timezone.now()
        earlier = self._make_job(1, created_at=now - timedelta(days=25), completed_at=now - timedelta(days=20), total=Decimal('1800'))
        self._log_hours(earlier, 4 * 3600)
        later = self._make_job(2, created_at=now - timedelta(days=1), title='Follow-up visit — no charge')

        with patch.object(client, 'fetch_job_visits_for_callback_detection', return_value=[]):
            detect_cross_job_callbacks(self.account, self.tenant)
        later.refresh_from_db()
        self.assertEqual(later.cross_job_callback_of_id, earlier.id)
        self.assertIsNone(later.cross_job_callback_bled_amount)
        self.assertFalse(later.cross_job_callback_bled_amount_is_estimated)

        scheduled_start = now - timedelta(hours=2)
        scheduled_end = scheduled_start + timedelta(hours=1, minutes=30)
        fake_visit = {
            'id': 'visit-later', 'createdAt': now.isoformat(),
            'startAt': scheduled_start.isoformat(), 'endAt': scheduled_end.isoformat(),
        }
        with patch.object(client, 'fetch_job_visits_for_callback_detection', return_value=[fake_visit]):
            detect_cross_job_callbacks(self.account, self.tenant)
        later.refresh_from_db()
        self.assertEqual(later.cross_job_callback_of_id, earlier.id, "sticky -- still the same earlier job")
        self.assertIsNotNone(later.cross_job_callback_bled_amount)
        self.assertTrue(later.cross_job_callback_bled_amount_is_estimated)
        expected_estimated = (Decimal('1800') / Decimal('4')) * Decimal('1.5')
        self.assertEqual(later.cross_job_callback_bled_amount, expected_estimated)

        # A first real entry appears -- the whole job flips to labour mode.
        # Detection must now skip its own live fetch and estimate entirely
        # (the per-person figure is computed fresh, at read time, by
        # technician_stats.py, never stored here) -- it only clears the
        # now-stale estimate it had stored a moment ago.
        self._log_hours(later, 5 * 3600)
        with patch.object(client, 'fetch_job_visits_for_callback_detection') as fetch_mock:
            detect_cross_job_callbacks(self.account, self.tenant)
        fetch_mock.assert_not_called()
        later.refresh_from_db()
        self.assertEqual(later.cross_job_callback_of_id, earlier.id, "sticky -- still the same earlier job")
        self.assertIsNone(later.cross_job_callback_bled_amount, "labour mode has nothing to estimate -- the stale estimate must be cleared, not kept")
        self.assertFalse(later.cross_job_callback_bled_amount_is_estimated)

    # ── quantization: non-round hours must not rewrite every pass ───────

    def test_amount_quantized_to_cents_and_stable_across_repeated_passes(self):
        now = timezone.now()
        earlier = self._make_job(1, created_at=now - timedelta(days=25), completed_at=now - timedelta(days=20), total=Decimal('1800'))
        self._log_hours(earlier, int(3.25 * 3600))  # a deliberately non-round rate: 1800/3.25 repeats
        later = self._make_job(2, created_at=now - timedelta(days=1), title='Follow-up visit — no charge')
        # later stays in ESTIMATE mode (no real hours of its own) -- a
        # deliberately non-round scheduled duration to exercise the SAME
        # quantization concern on the estimate-mode fallback.
        scheduled_start = now - timedelta(hours=2)
        scheduled_end = scheduled_start + timedelta(hours=1, minutes=20)
        fake_visit = {
            'id': 'visit-later', 'createdAt': now.isoformat(),
            'startAt': scheduled_start.isoformat(), 'endAt': scheduled_end.isoformat(),
        }

        with patch.object(client, 'fetch_job_visits_for_callback_detection', return_value=[fake_visit]):
            detect_cross_job_callbacks(self.account, self.tenant)
        later.refresh_from_db()
        amount_after_first_pass = later.cross_job_callback_bled_amount
        self.assertIsNotNone(amount_after_first_pass)
        self.assertTrue(later.cross_job_callback_bled_amount_is_estimated)
        self.assertEqual(
            amount_after_first_pass, amount_after_first_pass.quantize(Decimal('0.01')),
            "stored amount must already be quantized to 2 decimal places, matching the field's own decimal_places=2",
        )

        # Nothing about the underlying data changes -- a second real pass
        # must compute the IDENTICAL quantized value AND must not even
        # issue a save() for it (the real "otherwise every pass rewrites"
        # concern -- value stability alone doesn't prove that).
        with patch.object(client, 'fetch_job_visits_for_callback_detection', return_value=[fake_visit]):
            with patch.object(JobberJob, 'save', autospec=True, side_effect=JobberJob.save) as save_spy:
                detect_cross_job_callbacks(self.account, self.tenant)
        later.refresh_from_db()
        self.assertEqual(
            later.cross_job_callback_bled_amount, amount_after_first_pass,
            "an unchanged input must recompute to the exact same quantized amount on every pass, never rewrite to a new value",
        )
        later_saves = [c for c in save_spy.call_args_list if c.args[0].id == later.id]
        self.assertEqual(
            len(later_saves), 0,
            "an unchanged input must not call save() again at all -- a mismatched, un-quantized "
            "comparison would otherwise rewrite this row on every single pass",
        )

    # ── 3. failed fetch, while still in estimate mode, keeps the prior amount ──

    def test_failed_fetch_keeps_prior_amount(self):
        # Scoped to ESTIMATE mode specifically -- `later` never gets a real
        # logged entry anywhere in this test, so the live fetch stays the
        # only path to its amount throughout.
        now = timezone.now()
        earlier = self._make_job(1, created_at=now - timedelta(days=25), completed_at=now - timedelta(days=20), total=Decimal('1800'))
        self._log_hours(earlier, 4 * 3600)
        later = self._make_job(2, created_at=now - timedelta(days=1), title='Follow-up visit — no charge')

        scheduled_start = now - timedelta(hours=2)
        scheduled_end = scheduled_start + timedelta(hours=1, minutes=30)
        fake_visit = {
            'id': 'visit-later', 'createdAt': now.isoformat(),
            'startAt': scheduled_start.isoformat(), 'endAt': scheduled_end.isoformat(),
        }
        with patch.object(client, 'fetch_job_visits_for_callback_detection', return_value=[fake_visit]):
            detect_cross_job_callbacks(self.account, self.tenant)
        later.refresh_from_db()
        prior_amount = later.cross_job_callback_bled_amount
        self.assertIsNotNone(prior_amount)
        self.assertTrue(later.cross_job_callback_bled_amount_is_estimated)

        with patch.object(client, 'fetch_job_visits_for_callback_detection', side_effect=client.JobberAPIError('boom')):
            detect_cross_job_callbacks(self.account, self.tenant)
        later.refresh_from_db()
        self.assertEqual(later.cross_job_callback_bled_amount, prior_amount, "a failed live fetch must never overwrite the prior stored amount")
        self.assertTrue(later.cross_job_callback_bled_amount_is_estimated)

    # ── 4. unmatched job >30 days stops ─────────────────────────────────

    def test_unmatched_job_older_than_30_days_never_matches(self):
        now = timezone.now()
        old_later = self._make_job(
            2, created_at=now - timedelta(days=CROSS_JOB_CALLBACK_WINDOW_DAYS + 5),
            title='Follow-up visit — no charge',
        )
        # A real, otherwise-qualifying earlier job -- completed well
        # within what WOULD be a valid 30-day gap from old_later's own
        # creation, so the only reason this shouldn't match is the age
        # cutoff itself, not a genuinely missing candidate.
        earlier = self._make_job(
            1, created_at=now - timedelta(days=CROSS_JOB_CALLBACK_WINDOW_DAYS + 20),
            completed_at=now - timedelta(days=CROSS_JOB_CALLBACK_WINDOW_DAYS + 3),
            total=Decimal('1800'),
        )
        self._log_hours(earlier, 4 * 3600)

        self.assertFalse(_needs_rescan(old_later, now))
        detect_cross_job_callbacks(self.account, self.tenant)
        old_later.refresh_from_db()
        self.assertIsNone(old_later.cross_job_callback_of)

    # ── 5. matched job, unknown amount, stops at 90 days ────────────────

    def test_matched_unknown_amount_stops_rescanning_past_90_days(self):
        now = timezone.now()
        earlier = self._make_job(1, created_at=now - timedelta(days=200), completed_at=now - timedelta(days=150), total=Decimal('1800'))
        self._log_hours(earlier, 4 * 3600)
        later = self._make_job(
            2, created_at=now - timedelta(days=CROSS_JOB_CALLBACK_AMOUNT_RESCAN_CEILING_DAYS + 5),
            title='Follow-up visit — no charge',
        )
        later.cross_job_callback_of = earlier
        later.cross_job_callback_bled_amount = None
        later.cross_job_callback_bled_amount_is_estimated = False
        later.save(update_fields=['cross_job_callback_of', 'cross_job_callback_bled_amount', 'cross_job_callback_bled_amount_is_estimated'])

        self.assertFalse(_needs_rescan(later, now))

        # Real hours appear that WOULD resolve the amount if recomputed.
        self._log_hours(later, 5 * 3600)
        detect_cross_job_callbacks(self.account, self.tenant)
        later.refresh_from_db()
        self.assertIsNone(later.cross_job_callback_bled_amount, "past the 90-day ceiling, the amount must stay unknown, not get recomputed")

    # ── 5b. a legacy non-estimated amount with no real hours still needs rescan ──

    def test_legacy_non_estimated_amount_with_no_hours_still_gets_rescanned(self):
        """
        _needs_rescan()'s amount-rescan branch must check this job's REAL
        current hours, never the stored cross_job_callback_bled_amount/
        _is_estimated fields themselves -- a job can carry a non-null,
        non-estimated amount left over from before this hybrid existed,
        while genuinely still having zero real logged hours. That job is
        NOT settled; it must still be rescanned.
        """
        now = timezone.now()
        earlier = self._make_job(1, created_at=now - timedelta(days=200), completed_at=now - timedelta(days=150), total=Decimal('1800'))
        self._log_hours(earlier, 4 * 3600)
        later = self._make_job(
            2, created_at=now - timedelta(days=CROSS_JOB_CALLBACK_WINDOW_DAYS + 5),
            title='Follow-up visit — no charge',
        )
        later.cross_job_callback_of = earlier
        later.cross_job_callback_bled_amount = Decimal('500.00')
        later.cross_job_callback_bled_amount_is_estimated = False
        later.save(update_fields=['cross_job_callback_of', 'cross_job_callback_bled_amount', 'cross_job_callback_bled_amount_is_estimated'])

        self.assertTrue(
            _needs_rescan(later, now),
            "a legacy non-estimated stored amount with no real hours logged must still read as unsettled -- it's not actually confirmed",
        )

    # ── 6. mode transition is visible at READ time, before any rescan ───

    def test_matched_job_in_estimate_mode_reads_as_labour_mode_once_hours_appear(self):
        """
        A job matched and stored with a real `~` estimate must have its
        READ-TIME cost reflect labour mode the instant a first real entry
        appears, with no detection pass in between -- the read path
        (_accumulate_technician_cross_job_callback_stats()) checks this
        job's REAL current hours fresh on every call, never trusting the
        now-stale stored estimate.
        """
        now = timezone.now()
        earlier = self._make_job(1, created_at=now - timedelta(days=25), completed_at=now - timedelta(days=20), total=Decimal('1800'))
        self._log_hours(earlier, 4 * 3600)
        later = self._make_job(2, created_at=now - timedelta(days=5), title='Follow-up visit — no charge')

        scheduled_start = now - timedelta(hours=2)
        scheduled_end = scheduled_start + timedelta(hours=1, minutes=30)
        fake_visit = {
            'id': 'visit-later', 'createdAt': now.isoformat(),
            'startAt': scheduled_start.isoformat(), 'endAt': scheduled_end.isoformat(),
        }
        with patch.object(client, 'fetch_job_visits_for_callback_detection', return_value=[fake_visit]):
            detect_cross_job_callbacks(self.account, self.tenant)
        later.refresh_from_db()
        self.assertTrue(later.cross_job_callback_bled_amount_is_estimated)
        stale_estimate = later.cross_job_callback_bled_amount
        self.assertIsNotNone(stale_estimate)

        # A first real entry appears -- deliberately no detection pass
        # runs again before reading the stats below.
        self._log_hours(later, 2 * 3600, rate=Decimal('30'))

        period_start = now - timedelta(days=90)
        stats = _accumulate_technician_cross_job_callback_stats(self.tenant.id, period_start)
        self.assertIn(self.user.id, stats)
        self.assertEqual(
            stats[self.user.id]['callback_dollars_lost'], 60.0,
            "the real, confirmed labour-mode figure (2h x $30), computed fresh -- never the stale stored ~ estimate",
        )
        self.assertFalse(stats[self.user.id]['has_estimated_callback_cost'])
        self.assertNotEqual(Decimal(str(stats[self.user.id]['callback_dollars_lost'])), stale_estimate)


class CrossJobCallbackPerJobIsolationTests(TestCase):
    """One bad job must not stop the rest of the same pass from processing."""

    def setUp(self):
        self.tenant = Tenant.objects.create()
        self.client_row = JobberClient.objects.create(
            tenant=self.tenant, jobber_id='client-1', name='Test Client', synced_at=timezone.now(),
        )
        self.account = None

    def _make_job(self, job_number, created_at, completed_at=None, total=None, title='Standard job'):
        return JobberJob.objects.create(
            tenant=self.tenant,
            client=self.client_row,
            jobber_id=f'job-{job_number}',
            job_number=job_number,
            title=title,
            description=title,
            job_status='archived' if completed_at else 'requires_invoicing',
            status_display='Archived' if completed_at else 'Requires Invoicing',
            service_type=_SERVICE_TYPE,
            total=total if total is not None else Decimal('0'),
            jobber_created_at=created_at,
            completed_at=completed_at,
            property_id=_PROPERTY_ID,
            synced_at=timezone.now(),
        )

    def test_one_job_raising_does_not_stop_the_others_in_the_same_pass(self):
        now = timezone.now()
        earlier = self._make_job(1, created_at=now - timedelta(days=25), completed_at=now - timedelta(days=20), total=Decimal('900'))
        broken_later = self._make_job(2, created_at=now - timedelta(days=2), title='Follow-up visit — no charge')
        healthy_later = self._make_job(3, created_at=now - timedelta(days=1), title='Follow-up visit — no charge')

        import apps.jobber.services.cross_job_callback_detection as detection_module
        real_compute = detection_module._compute_cross_job_bled_amount

        def flaky_compute(account, earlier_job, later_job):
            if later_job.id == broken_later.id:
                raise RuntimeError('simulated unexpected failure for this one job')
            return real_compute(account, earlier_job, later_job)

        with patch.object(detection_module, '_compute_cross_job_bled_amount', side_effect=flaky_compute):
            result = detect_cross_job_callbacks(self.account, self.tenant)

        self.assertEqual(result['failed'], 1)
        healthy_later.refresh_from_db()
        self.assertEqual(
            healthy_later.cross_job_callback_of_id, earlier.id,
            "a real, otherwise-valid job must still be processed even though a different job in the same pass raised",
        )
        broken_later.refresh_from_db()
        self.assertIsNone(
            broken_later.cross_job_callback_of,
            "the job that raised should be left unmatched this pass, not partially written",
        )


class CrossJobCallbackRealScenarioRegressionTests(TestCase):
    """
    Recreates the real, already-validated tenant_id=6 research scenario
    (research_alternative_callback_detection.py's own Rule D result --
    E1/E2 matched, F1/F2/G1 did not) as local fixtures, confirming the
    event-driven/sticky-match rewrite preserves the exact same real-
    world-validated matching decisions -- _find_earlier_match()'s own
    4-condition logic is unchanged by this rewrite; only candidate
    SELECTION and sticky-recompute behavior changed, and this is what
    proves that didn't silently alter any real verdict.
    """
    def setUp(self):
        self.tenant = Tenant.objects.create()
        self.client_row = JobberClient.objects.create(
            tenant=self.tenant, jobber_id='client-1', name='Test Client', synced_at=timezone.now(),
        )
        self.account = None

    def _make_job(self, job_number, created_at, completed_at=None, total=None,
                   title='Standard job', service_type=_SERVICE_TYPE, property_id=_PROPERTY_ID):
        return JobberJob.objects.create(
            tenant=self.tenant,
            client=self.client_row,
            jobber_id=f'job-{job_number}',
            job_number=job_number,
            title=title,
            description=title,
            job_status='archived' if completed_at else 'requires_invoicing',
            status_display='Archived' if completed_at else 'Requires Invoicing',
            service_type=service_type,
            total=total if total is not None else Decimal('0'),
            jobber_created_at=created_at,
            completed_at=completed_at,
            property_id=property_id,
            synced_at=timezone.now(),
        )

    def test_e1_e2_like_real_callback_matches(self):
        now = timezone.now()
        e1 = self._make_job(4, created_at=now - timedelta(days=10), completed_at=now - timedelta(days=5), total=Decimal('1800'))
        e2 = self._make_job(5, created_at=now - timedelta(days=1), title='Follow-up visit — no charge')
        detect_cross_job_callbacks(self.account, self.tenant)
        e2.refresh_from_db()
        self.assertEqual(e2.cross_job_callback_of_id, e1.id)

    def test_f1_f2_like_real_repeat_business_does_not_match_without_keyword(self):
        now = timezone.now()
        self._make_job(6, created_at=now - timedelta(days=10), completed_at=now - timedelta(days=5), total=Decimal('900'))
        f2 = self._make_job(7, created_at=now - timedelta(days=1), title='Standard Outlet Install')  # no keyword
        detect_cross_job_callbacks(self.account, self.tenant)
        f2.refresh_from_db()
        self.assertIsNone(f2.cross_job_callback_of, "real, legitimate repeat business at the same address/client must NOT be flagged without a real keyword")

    def test_g1_like_keyword_alone_does_not_match_without_address_or_client(self):
        now = timezone.now()
        self._make_job(10, created_at=now - timedelta(days=10), completed_at=now - timedelta(days=5), total=Decimal('500'), property_id='different-property')
        g1 = self._make_job(9, created_at=now - timedelta(days=1), title='Redo quote — material price changed')
        detect_cross_job_callbacks(self.account, self.tenant)
        g1.refresh_from_db()
        self.assertIsNone(g1.cross_job_callback_of, "a keyword hit alone, with no real address/client match, must NOT be flagged")

    def test_f3_like_safeguard_blocks_different_service_type(self):
        now = timezone.now()
        self._make_job(6, created_at=now - timedelta(days=10), completed_at=now - timedelta(days=5), total=Decimal('900'), service_type='Standard Outlet Install')
        f3 = self._make_job(
            8, created_at=now - timedelta(days=1), title='Follow-up visit — no charge',
            service_type='Panel Upgrade',  # same address/client, genuinely different work
        )
        detect_cross_job_callbacks(self.account, self.tenant)
        f3.refresh_from_db()
        self.assertIsNone(f3.cross_job_callback_of, "the service_type safeguard must block a match even with address+client+keyword all agreeing")


class CallbackLabourCostHybridTests(TestCase):
    """
    _accumulate_technician_cross_job_callback_stats()'s hybrid: labour
    mode (per-person hours x own rate, 3 real outcomes) when real hours
    are logged anywhere on the later job, today's stored estimate
    (honoured only when genuinely flagged estimated) otherwise. Calls the real
    accumulator (and _merge_callback_stats()) directly against real rows
    -- detect_cross_job_callbacks() itself (and its live-fetch estimate
    path) is exercised separately, by CrossJobCallbackDetectionTests
    above; these tests set cross_job_callback_of/
    cross_job_callback_bled_amount directly instead of running detection.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create()
        self.client_row = JobberClient.objects.create(
            tenant=self.tenant, jobber_id='client-hybrid', name='Hybrid Client', synced_at=timezone.now(),
        )
        self.hannah = JobberUser.objects.create(
            tenant=self.tenant, jobber_id='user-hannah', name='Hannah', synced_at=timezone.now(),
        )
        self.owen = JobberUser.objects.create(
            tenant=self.tenant, jobber_id='user-owen', name='Owen', synced_at=timezone.now(),
        )
        self.casey = JobberUser.objects.create(
            tenant=self.tenant, jobber_id='user-casey', name='Casey', synced_at=timezone.now(),
        )
        now = timezone.now()
        # A real row for cross_job_callback_of to point at -- its own
        # content is irrelevant to the accumulator under test, which only
        # ever reads it via the isnull filter.
        self.earlier = JobberJob.objects.create(
            tenant=self.tenant, client=self.client_row, jobber_id='job-earlier', job_number=1,
            title='Original job', description='Original job',
            job_status='archived', status_display='Archived',
            service_type=_SERVICE_TYPE, total=Decimal('1800'),
            jobber_created_at=now - timedelta(days=20), completed_at=now - timedelta(days=15),
            property_id=_PROPERTY_ID, synced_at=timezone.now(),
        )
        self.period_start = now - timedelta(days=90)
        self._next_job_number = 100

    def _make_callback_job(self, bled_amount=None, is_estimated=False):
        self._next_job_number += 1
        job_number = self._next_job_number
        return JobberJob.objects.create(
            tenant=self.tenant, client=self.client_row,
            jobber_id=f'job-cb-{job_number}', job_number=job_number,
            title='Follow-up visit — no charge', description='Follow-up visit — no charge',
            job_status='requires_invoicing', status_display='Requires Invoicing',
            service_type=_SERVICE_TYPE, total=Decimal('0'),
            jobber_created_at=timezone.now() - timedelta(days=1),
            property_id=_PROPERTY_ID, synced_at=timezone.now(),
            cross_job_callback_of=self.earlier,
            cross_job_callback_bled_amount=bled_amount,
            cross_job_callback_bled_amount_is_estimated=is_estimated,
        )

    def _assign(self, job, *users):
        visit = JobberVisit.objects.create(
            tenant=self.tenant, job=job,
            jobber_id=f'visit-{job.id}-{"-".join(str(u.id) for u in users)}',
            synced_at=timezone.now(),
        )
        visit.assigned_users.set(users)
        return visit

    def _log_hours(self, job, user, seconds, rate=None):
        return JobberTimeSheetEntry.objects.create(
            tenant=self.tenant, job=job, user=user,
            jobber_id=f'entry-{job.id}-{user.id}-{seconds}-{rate}',
            final_duration_seconds=seconds,
            labour_rate=rate,
            jobber_created_at=job.jobber_created_at,
            synced_at=timezone.now(),
        )

    # ── labour mode: own hours x own rate, never a split of a total ─────

    def test_labour_mode_confirmed_cost_accumulates_across_jobs(self):
        job1 = self._make_callback_job()
        self._assign(job1, self.hannah)
        self._log_hours(job1, self.hannah, 6 * 3600, rate=Decimal('40'))

        job2 = self._make_callback_job()
        self._assign(job2, self.hannah)
        self._log_hours(job2, self.hannah, 3 * 3600, rate=Decimal('40'))

        stats = _accumulate_technician_cross_job_callback_stats(self.tenant.id, self.period_start)

        self.assertEqual(stats[self.hannah.id]['callback_dollars_lost'], 360.0, "6h x $40 ($240) + 3h x $40 ($120)")
        self.assertEqual(stats[self.hannah.id]['callback_visits_done'], 2)
        self.assertFalse(stats[self.hannah.id]['has_unknown_callback_cost'])

    def test_labour_mode_two_assignees_get_own_card_not_proportional_split(self):
        job = self._make_callback_job()
        self._assign(job, self.hannah, self.owen)
        self._log_hours(job, self.hannah, 6 * 3600, rate=Decimal('40'))
        self._log_hours(job, self.owen, 2 * 3600, rate=Decimal('22'))

        stats = _accumulate_technician_cross_job_callback_stats(self.tenant.id, self.period_start)

        self.assertEqual(stats[self.hannah.id]['callback_dollars_lost'], 240.0, "Hannah's OWN hours x OWN rate, never a split of a job-level total")
        self.assertEqual(stats[self.owen.id]['callback_dollars_lost'], 44.0, "Owen's OWN hours x OWN rate")

    def test_labour_mode_three_per_person_outcomes_on_one_job(self):
        job = self._make_callback_job()
        # Owen: assigned, but no entries of his OWN -- case (a), someone
        # else (Hannah) logged real hours, so the job IS in labour mode.
        self._assign(job, self.hannah, self.owen)
        self._log_hours(job, self.hannah, 4 * 3600, rate=Decimal('40'))  # case (b)
        # Casey: a real logger who is NOT a formal assignee, with no
        # usable rate -- case (c), AND a real test of the union-of-
        # assignees-and-loggers population rule.
        self._log_hours(job, self.casey, 3 * 3600, rate=None)

        stats = _accumulate_technician_cross_job_callback_stats(self.tenant.id, self.period_start)

        self.assertEqual(stats[self.hannah.id]['callback_dollars_lost'], 160.0)
        self.assertFalse(stats[self.hannah.id]['has_unknown_callback_cost'])

        self.assertEqual(stats[self.owen.id]['callback_dollars_lost'], 0.0, "case (a): a real, confirmed $0, not unknown")
        self.assertFalse(stats[self.owen.id]['has_unknown_callback_cost'])
        self.assertEqual(stats[self.owen.id]['callback_visits_done'], 1)

        self.assertEqual(stats[self.casey.id]['callback_dollars_lost'], 0.0)
        self.assertTrue(stats[self.casey.id]['has_unknown_callback_cost'], "case (c): real entries, no usable rate -- genuinely unknown")
        self.assertEqual(stats[self.casey.id]['callback_visits_done'], 1, "a non-assignee real logger still gets full visit credit")

    def test_deleting_all_entries_returns_a_job_to_estimate_mode(self):
        job = self._make_callback_job(bled_amount=Decimal('150.00'), is_estimated=True)
        self._assign(job, self.hannah)
        entry = self._log_hours(job, self.hannah, 4 * 3600, rate=Decimal('40'))

        stats = _accumulate_technician_cross_job_callback_stats(self.tenant.id, self.period_start)
        self.assertEqual(stats[self.hannah.id]['callback_dollars_lost'], 160.0, "labour mode first -- 4h x $40")
        self.assertFalse(stats[self.hannah.id]['has_estimated_callback_cost'])

        # The only real entry on this job is removed -- the job has zero
        # real logged hours again, so it must read back as estimate mode,
        # honouring the stored `~` amount once more.
        entry.delete()
        stats = _accumulate_technician_cross_job_callback_stats(self.tenant.id, self.period_start)
        self.assertEqual(stats[self.hannah.id]['callback_dollars_lost'], 150.0, "back to the stored estimate, no real hours left anywhere on the job")
        self.assertTrue(stats[self.hannah.id]['has_estimated_callback_cost'])

    # ── estimate mode ─────────────────────────────────────────────────

    def test_zero_second_entry_stays_in_estimate_mode(self):
        job = self._make_callback_job(bled_amount=Decimal('150.00'), is_estimated=True)
        self._assign(job, self.hannah)
        # A 0-second entry is NOT logged hours -- must not flip the job
        # into labour mode.
        self._log_hours(job, self.hannah, 0, rate=Decimal('40'))

        stats = _accumulate_technician_cross_job_callback_stats(self.tenant.id, self.period_start)

        self.assertEqual(stats[self.hannah.id]['callback_dollars_lost'], 150.0)
        self.assertTrue(stats[self.hannah.id]['has_estimated_callback_cost'])
        self.assertFalse(stats[self.hannah.id]['has_unknown_callback_cost'])

    def test_estimate_mode_two_assignees_equal_split_both_flagged(self):
        job = self._make_callback_job(bled_amount=Decimal('300.00'), is_estimated=True)
        self._assign(job, self.hannah, self.owen)

        stats = _accumulate_technician_cross_job_callback_stats(self.tenant.id, self.period_start)

        self.assertEqual(stats[self.hannah.id]['callback_dollars_lost'], 150.0, "$300 estimate split equally across 2 real assignees")
        self.assertEqual(stats[self.owen.id]['callback_dollars_lost'], 150.0)
        self.assertTrue(stats[self.hannah.id]['has_estimated_callback_cost'])
        self.assertTrue(stats[self.owen.id]['has_estimated_callback_cost'])

    def test_legacy_stored_non_estimated_amount_is_unknown(self):
        # Genuinely zero logged hours (estimate-mode eligible), but the
        # stored amount is NOT flagged estimated -- a leftover of the old
        # per-job-total formula, not a real confirmed figure under the
        # new hybrid. Must be treated as unknown, never reused.
        job = self._make_callback_job(bled_amount=Decimal('500.00'), is_estimated=False)
        self._assign(job, self.hannah)

        stats = _accumulate_technician_cross_job_callback_stats(self.tenant.id, self.period_start)

        self.assertEqual(stats[self.hannah.id]['callback_dollars_lost'], 0.0)
        self.assertTrue(stats[self.hannah.id]['has_unknown_callback_cost'])
        self.assertFalse(stats[self.hannah.id]['has_estimated_callback_cost'])

    # ── Decimal/float merge boundary ──────────────────────────────────

    def test_merge_callback_stats_combines_old_float_and_new_decimal_boundary(self):
        job = self._make_callback_job()
        self._assign(job, self.hannah)
        self._log_hours(job, self.hannah, 6 * 3600, rate=Decimal('40'))

        new_stats = _accumulate_technician_cross_job_callback_stats(self.tenant.id, self.period_start)
        old_stats = {
            self.hannah.id: {
                'callback_visits_done': 1,
                'callback_dollars_lost': 100.0,
                'has_unknown_callback_cost': False,
                'has_estimated_callback_cost': False,
            },
        }

        # Must not raise TypeError (Decimal + float) -- both accumulators'
        # own dicts are plain floats at this boundary.
        merged = _merge_callback_stats(old_stats, new_stats)

        self.assertEqual(merged[self.hannah.id]['callback_dollars_lost'], 340.0)
        self.assertEqual(merged[self.hannah.id]['callback_visits_done'], 2)

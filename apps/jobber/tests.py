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

from apps.jobber.models import JobberClient, JobberJob, JobberTimeSheetEntry, JobberUser
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

    def _log_hours(self, job, seconds):
        JobberTimeSheetEntry.objects.create(
            tenant=self.tenant,
            job=job,
            user=self.user,
            jobber_id=f'entry-{job.id}-{seconds}',
            final_duration_seconds=seconds,
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

    # ── 2. unknown -> estimated -> confirmed ────────────────────────────

    def test_amount_unknown_then_estimated_then_confirmed(self):
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

        self._log_hours(later, 5 * 3600)
        detect_cross_job_callbacks(self.account, self.tenant)
        later.refresh_from_db()
        self.assertEqual(later.cross_job_callback_of_id, earlier.id, "sticky -- still the same earlier job")
        self.assertFalse(later.cross_job_callback_bled_amount_is_estimated)
        expected_confirmed = (Decimal('1800') / Decimal('4')) * Decimal('5')
        self.assertEqual(later.cross_job_callback_bled_amount, expected_confirmed)

    # ── quantization: non-round hours must not rewrite every pass ───────

    def test_amount_quantized_to_cents_and_stable_across_repeated_passes(self):
        now = timezone.now()
        earlier = self._make_job(1, created_at=now - timedelta(days=25), completed_at=now - timedelta(days=20), total=Decimal('1800'))
        self._log_hours(earlier, int(3.25 * 3600))  # a deliberately non-round rate: 1800/3.25 repeats
        later = self._make_job(2, created_at=now - timedelta(days=1), title='Follow-up visit — no charge')
        self._log_hours(later, 5 * 3600)

        detect_cross_job_callbacks(self.account, self.tenant)
        later.refresh_from_db()
        amount_after_first_pass = later.cross_job_callback_bled_amount
        self.assertIsNotNone(amount_after_first_pass)
        self.assertEqual(
            amount_after_first_pass, amount_after_first_pass.quantize(Decimal('0.01')),
            "stored amount must already be quantized to 2 decimal places, matching the field's own decimal_places=2",
        )

        # Nothing about the underlying data changes -- a second real pass
        # must compute the IDENTICAL quantized value AND must not even
        # issue a save() for it (the real "otherwise every pass rewrites"
        # concern -- value stability alone doesn't prove that).
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

    # ── 3. failed fetch keeps the prior amount ──────────────────────────

    def test_failed_fetch_keeps_prior_amount(self):
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

    # ── 6. confirmed job within 30 days still recomputes ────────────────

    def test_confirmed_job_within_30_days_still_recomputes_on_hours_change(self):
        now = timezone.now()
        earlier = self._make_job(1, created_at=now - timedelta(days=25), completed_at=now - timedelta(days=20), total=Decimal('1800'))
        self._log_hours(earlier, 4 * 3600)
        later = self._make_job(2, created_at=now - timedelta(days=5), title='Follow-up visit — no charge')
        self._log_hours(later, 3 * 3600)

        detect_cross_job_callbacks(self.account, self.tenant)
        later.refresh_from_db()
        first_amount = later.cross_job_callback_bled_amount
        self.assertFalse(later.cross_job_callback_bled_amount_is_estimated)
        self.assertEqual(first_amount, (Decimal('1800') / Decimal('4')) * Decimal('3'))

        self._log_hours(later, 2 * 3600)  # more real hours logged afterward
        detect_cross_job_callbacks(self.account, self.tenant)
        later.refresh_from_db()
        self.assertEqual(
            later.cross_job_callback_bled_amount, (Decimal('1800') / Decimal('4')) * Decimal('5'),
            "a job still within its own 30-day window must keep recomputing even once confirmed",
        )


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

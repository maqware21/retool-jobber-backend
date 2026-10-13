"""
Regression test for store_tokens()'s update_fields fix -- confirms a
real reconnect (an existing, previously-disconnected JobberAccount row)
actually persists BOTH the reactivation and the new tokens, not just
the tokens. Run against the throwaway settings_migration_test SQLite
setup:

    DJANGO_SETTINGS_MODULE=tech_track_pro.settings_migration_test \
        python manage.py test apps.jobber.tests_oauth
"""
from django.test import TestCase

from apps.jobber.api.oauth import JobberCallbackView
from apps.jobber.models import JobberAccount
from apps.tenants.models import Tenant
from apps.users.models import User


class ReconnectPersistsBothActivationAndTokensTests(TestCase):
    def test_reconnecting_an_inactive_account_leaves_it_active_with_tokens_saved(self):
        tenant = Tenant.objects.create()
        user = User.objects.create(first_name='Test', email='reconnect-test@example.com', tenant=tenant)
        existing = JobberAccount.objects.create(
            tenant=tenant, access_token='old-token', refresh_token='old-refresh',
            is_active=False,
        )

        view = JobberCallbackView()
        account = view._link_account(user, {
            'access_token': 'new-token',
            'refresh_token': 'new-refresh',
            'token_type': 'bearer',
        })

        self.assertEqual(account.pk, existing.pk, "must reuse the existing row, not create a new one")
        existing.refresh_from_db()
        self.assertTrue(existing.is_active, "reconnecting must actually persist the reactivation, not just set it in memory")
        self.assertEqual(existing.access_token, 'new-token')
        self.assertEqual(existing.refresh_token, 'new-refresh')

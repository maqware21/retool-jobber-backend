from datetime import timedelta

from django.db import models
from django.utils import timezone

from helpers.models import DateModel

# Refresh a little before the real expiry so an in-flight request never races
# the token going stale.
TOKEN_EXPIRY_LEEWAY = timedelta(seconds=60)


class JobberAccount(DateModel):
    """
    The OAuth link between one VoltPro Tenant and one connected Jobber account.

    Holds the access/refresh tokens issued by Jobber's OAuth 2.0 flow. One row
    per tenant — a tenant re-connecting overwrites the same row (see
    ``store_tokens``). Tokens are never exposed through the API.
    """

    tenant = models.OneToOneField(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='jobber_account',
    )

    # Jobber's own account/company identifier (filled once we query the API).
    jobber_account_id = models.CharField(max_length=255, null=True, blank=True)

    access_token = models.TextField()
    refresh_token = models.TextField()
    token_type = models.CharField(max_length=40, default='bearer')
    # Space-separated scopes actually granted by the Jobber admin.
    scope = models.TextField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'jobber_accounts'
        verbose_name = 'jobber account'
        verbose_name_plural = 'jobber accounts'

    def __str__(self):
        return f"JobberAccount(tenant={self.tenant_id}, account={self.jobber_account_id})"

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def is_expired(self):
        """True when the access token is missing an expiry or is within the leeway window."""
        if not self.expires_at:
            return True
        return timezone.now() >= (self.expires_at - TOKEN_EXPIRY_LEEWAY)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def store_tokens(self, token_data):
        """
        Persist a token payload from Jobber's token endpoint.

        Handles both the initial code exchange and refreshes. Jobber does not
        always return a fresh ``refresh_token`` on refresh, so the existing one
        is kept when absent.
        """
        self.access_token = token_data['access_token']
        if token_data.get('refresh_token'):
            self.refresh_token = token_data['refresh_token']
        self.token_type = token_data.get('token_type', 'bearer')
        if token_data.get('scope'):
            self.scope = token_data['scope']

        expires_in = token_data.get('expires_in')
        if expires_in:
            self.expires_at = timezone.now() + timedelta(seconds=int(expires_in))

        self.save()
        return self

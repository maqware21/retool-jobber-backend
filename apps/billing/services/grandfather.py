from apps.billing.models import Subscription
from apps.tenants.models import Tenant


def grandfather_missing_tenants(dry_run=False):
    """
    Give every tenant without a Subscription an exempt one, and return the
    tenant ids affected. Safe to run repeatedly: a tenant that already has
    a row is never touched. Soft-deleted tenants are included.
    """
    missing = list(
        Tenant.all_objects
        .filter(subscription__isnull=True)
        .values_list('pk', flat=True)
    )
    if not dry_run:
        Subscription.objects.bulk_create(
            [Subscription(tenant_id=pk, billing_exempt=True) for pk in missing]
        )
    return missing

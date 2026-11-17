from django.db import migrations


def exempt_existing_tenants(apps, schema_editor):
    """Give every tenant that has no Subscription an exempt one."""
    Tenant = apps.get_model('tenants', 'Tenant')
    Subscription = apps.get_model('billing', 'Subscription')

    # Soft-deleted tenants are included: the historical Tenant model has
    # a plain manager, so every row is visible here.
    missing = Tenant.objects.filter(subscription__isnull=True).values_list('pk', flat=True)
    Subscription.objects.bulk_create(
        [Subscription(tenant_id=pk, billing_exempt=True) for pk in missing]
    )


def remove_exempt_rows(apps, schema_editor):
    """
    Delete exempt rows that were never linked to Stripe. This also removes
    exempt rows added after this migration ran (for example internal
    accounts), which is the intended trade for a clean reverse.
    """
    Subscription = apps.get_model('billing', 'Subscription')
    Subscription.objects.filter(
        billing_exempt=True,
        stripe_customer_id__isnull=True,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(exempt_existing_tenants, remove_exempt_rows),
    ]

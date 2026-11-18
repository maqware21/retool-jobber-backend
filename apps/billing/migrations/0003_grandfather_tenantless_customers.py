"""
Optional step, kept separate from 0002 so it can be dropped on its own:
existing customer users who never connected Jobber have no tenant, and
the lock rule denies a customer with no tenant. This gives each of them
an empty tenant with an exempt Subscription so they stay unaffected.

To decline this grandfathering, delete this file before it is applied
(nothing else depends on it). Customers without a tenant are then locked
once enforcement is on.
"""
from django.db import migrations

# Marks the rows this migration creates, so the reverse removes exactly
# those and nothing a person or another code path made.
MARKER = 'grandfathered: customer user with no tenant'


def exempt_tenantless_customers(apps, schema_editor):
    User = apps.get_model('users', 'User')
    Tenant = apps.get_model('tenants', 'Tenant')
    Subscription = apps.get_model('billing', 'Subscription')

    users = (
        User.objects
        .filter(tenant__isnull=True, is_superuser=False, user_permissions__codename='customer')
        .exclude(user_permissions__codename='admin')
        .distinct()
    )
    for user in users:
        tenant = Tenant.objects.create()
        User.objects.filter(pk=user.pk).update(tenant_id=tenant.pk)
        Subscription.objects.create(
            tenant=tenant,
            billing_exempt=True,
            billing_email=user.email,
            review_note=MARKER,
        )


def _is_referenced(tenant):
    """
    True when any model other than User and Subscription points at the
    tenant. Reverse relations come from the migration state, which
    includes every app that references Tenant (see dependencies).
    """
    for rel in tenant._meta.related_objects:
        if rel.related_model._meta.model_name in ('user', 'subscription'):
            continue
        if rel.related_model._default_manager.filter(**{rel.field.name: tenant}).exists():
            return True
    return False


def remove_marked_rows(apps, schema_editor):
    """
    Undo the forward step for tenants nothing else uses. A tenant that has
    since gained data (for example a connected Jobber account) is left
    exactly as it is, exempt row included, because deleting it would
    cascade into that data.
    """
    User = apps.get_model('users', 'User')
    Tenant = apps.get_model('tenants', 'Tenant')
    Subscription = apps.get_model('billing', 'Subscription')

    marked = Subscription.objects.filter(
        review_note=MARKER,
        billing_exempt=True,
        stripe_customer_id__isnull=True,
    )
    for subscription in marked:
        tenant = Tenant.objects.get(pk=subscription.tenant_id)
        if _is_referenced(tenant):
            continue
        User.objects.filter(tenant_id=tenant.pk).update(tenant=None)
        subscription.delete()
        tenant.delete()


class Migration(migrations.Migration):
    # The jobber, goals and alerts dependencies put every model that
    # references Tenant into the migration state, which the reverse needs.
    dependencies = [
        ("billing", "0002_grandfather_existing_tenants"),
        ("users", "0001_initial"),
        ("tenants", "0001_initial"),
        ("jobber", "0015_jobberaccount_pending_resync_marked_at"),
        ("goals", "0002_teamannualgoal_techniciannualgoal"),
        ("alerts", "0004_alter_alertrule_rule_type"),
    ]

    operations = [
        migrations.RunPython(exempt_tenantless_customers, remove_marked_rows),
    ]

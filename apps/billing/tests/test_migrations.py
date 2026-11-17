from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

BILLING_0001 = ('billing', '0001_initial')
BILLING_0002 = ('billing', '0002_grandfather_existing_tenants')
BILLING_0003 = ('billing', '0003_grandfather_tenantless_customers')
MARKER = 'grandfathered: customer user with no tenant'


class MigrationTestCase(TransactionTestCase):
    """
    Moves the billing schema to a named migration and returns the models as
    they stand there. Every other app is at its latest migration, so the
    returned state holds those apps' models too.
    """

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([target])
        others = [n for n in executor.loader.graph.leaf_nodes() if n[0] != 'billing']
        return executor.loader.project_state([target] + others).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()


class ExemptExistingTenantsMigrationTests(MigrationTestCase):
    def setUp(self):
        super().setUp()
        self.apps = self.migrate(BILLING_0001)
        self.Tenant = self.apps.get_model('tenants', 'Tenant')
        self.Subscription = self.apps.get_model('billing', 'Subscription')

    def test_forward_exempts_only_tenants_without_a_row(self):
        bare = self.Tenant.objects.create(business_name='bare')
        paying = self.Tenant.objects.create(business_name='paying')
        self.Subscription.objects.create(
            tenant=paying, status='active', stripe_customer_id='cus_1',
        )

        apps = self.migrate(BILLING_0002)
        Subscription = apps.get_model('billing', 'Subscription')

        self.assertTrue(Subscription.objects.get(tenant_id=bare.pk).billing_exempt)
        paying_row = Subscription.objects.get(tenant_id=paying.pk)
        self.assertFalse(paying_row.billing_exempt)
        self.assertEqual(paying_row.status, 'active')
        self.assertEqual(Subscription.objects.count(), 2)

    def test_forward_includes_soft_deleted_tenants(self):
        from django.utils import timezone
        gone = self.Tenant.objects.create(deleted=timezone.now())

        apps = self.migrate(BILLING_0002)

        self.assertTrue(
            apps.get_model('billing', 'Subscription').objects.get(tenant_id=gone.pk).billing_exempt
        )

    def test_forward_on_no_tenants_is_a_no_op(self):
        apps = self.migrate(BILLING_0002)
        self.assertEqual(apps.get_model('billing', 'Subscription').objects.count(), 0)

    def test_reverse_deletes_only_exempt_rows_without_a_stripe_customer(self):
        bare = self.Tenant.objects.create()
        paying = self.Tenant.objects.create()
        linked_exempt = self.Tenant.objects.create()
        self.Subscription.objects.create(tenant=paying, status='active', stripe_customer_id='cus_1')
        self.Subscription.objects.create(
            tenant=linked_exempt, billing_exempt=True, stripe_customer_id='cus_2',
        )

        self.migrate(BILLING_0002)
        apps = self.migrate(BILLING_0001)
        Subscription = apps.get_model('billing', 'Subscription')

        remaining = set(Subscription.objects.values_list('tenant_id', flat=True))
        self.assertEqual(remaining, {paying.pk, linked_exempt.pk})
        self.assertNotIn(bare.pk, remaining)


class ExemptTenantlessCustomersMigrationTests(MigrationTestCase):
    def setUp(self):
        super().setUp()
        self.apps = self.migrate(BILLING_0002)
        self.User = self.apps.get_model('users', 'User')
        self.Tenant = self.apps.get_model('tenants', 'Tenant')
        self.Subscription = self.apps.get_model('billing', 'Subscription')

        ContentType = self.apps.get_model('contenttypes', 'ContentType')
        Permission = self.apps.get_model('auth', 'Permission')
        content_type, _ = ContentType.objects.get_or_create(app_label='users', model='user')
        self.customer_perm, _ = Permission.objects.get_or_create(
            codename='customer', content_type=content_type, defaults={'name': 'customer'},
        )
        self.admin_perm, _ = Permission.objects.get_or_create(
            codename='admin', content_type=content_type, defaults={'name': 'admin'},
        )

    def make_user(self, email, perm=None, tenant=None, **extra):
        user = self.User.objects.create(
            email=email, first_name='T', password='x', tenant=tenant, **extra,
        )
        if perm is not None:
            user.user_permissions.add(perm)
        return user

    def test_forward_exempts_only_tenantless_customers(self):
        lone = self.make_user('lone@example.com', self.customer_perm)
        inactive = self.make_user('inactive@example.com', self.customer_perm, is_active=False)
        owned_tenant = self.Tenant.objects.create()
        owned = self.make_user('owned@example.com', self.customer_perm, tenant=owned_tenant)
        admin = self.make_user('admin@example.com', self.admin_perm)
        superuser = self.make_user('super@example.com', self.customer_perm, is_superuser=True)
        nobody = self.make_user('nobody@example.com')
        both = self.make_user('both@example.com', self.customer_perm)
        both.user_permissions.add(self.admin_perm)

        apps = self.migrate(BILLING_0003)
        User = apps.get_model('users', 'User')
        Subscription = apps.get_model('billing', 'Subscription')

        for user in (lone, inactive):
            tenant_id = User.objects.get(pk=user.pk).tenant_id
            self.assertIsNotNone(tenant_id, user.email)
            row = Subscription.objects.get(tenant_id=tenant_id)
            self.assertTrue(row.billing_exempt)
            self.assertEqual(row.billing_email, user.email)
            self.assertEqual(row.review_note, MARKER)

        self.assertEqual(User.objects.get(pk=owned.pk).tenant_id, owned_tenant.pk)
        for user in (admin, superuser, nobody, both):
            self.assertIsNone(User.objects.get(pk=user.pk).tenant_id, user.email)

        # The pre-existing tenant keeps the exempt row 0002 gave it; the
        # two new tenants get one each.
        self.assertEqual(Subscription.objects.filter(review_note=MARKER).count(), 2)

    def test_forward_with_no_candidates_changes_nothing(self):
        self.make_user('admin@example.com', self.admin_perm)
        apps = self.migrate(BILLING_0003)
        self.assertEqual(apps.get_model('tenants', 'Tenant').objects.count(), 0)
        self.assertEqual(apps.get_model('billing', 'Subscription').objects.count(), 0)

    def test_reverse_removes_what_forward_created(self):
        lone = self.make_user('lone@example.com', self.customer_perm)
        owned_tenant = self.Tenant.objects.create()
        owned = self.make_user('owned@example.com', self.customer_perm, tenant=owned_tenant)
        self.Subscription.objects.create(tenant=owned_tenant, billing_exempt=True)

        self.migrate(BILLING_0003)
        apps = self.migrate(BILLING_0002)
        User = apps.get_model('users', 'User')

        self.assertIsNone(User.objects.get(pk=lone.pk).tenant_id)
        self.assertEqual(User.objects.get(pk=owned.pk).tenant_id, owned_tenant.pk)
        self.assertEqual(apps.get_model('tenants', 'Tenant').objects.count(), 1)
        Subscription = apps.get_model('billing', 'Subscription')
        self.assertEqual(Subscription.objects.filter(review_note=MARKER).count(), 0)
        self.assertEqual(Subscription.objects.count(), 1)

    def test_reverse_keeps_a_tenant_that_has_since_connected_jobber(self):
        lone = self.make_user('lone@example.com', self.customer_perm)

        apps = self.migrate(BILLING_0003)
        User = apps.get_model('users', 'User')
        tenant_id = User.objects.get(pk=lone.pk).tenant_id
        apps.get_model('jobber', 'JobberAccount').objects.create(
            tenant_id=tenant_id, access_token='', refresh_token='',
        )

        apps = self.migrate(BILLING_0002)

        self.assertEqual(apps.get_model('users', 'User').objects.get(pk=lone.pk).tenant_id, tenant_id)
        self.assertTrue(apps.get_model('tenants', 'Tenant').objects.filter(pk=tenant_id).exists())
        self.assertTrue(apps.get_model('jobber', 'JobberAccount').objects.filter(tenant_id=tenant_id).exists())
        self.assertTrue(
            apps.get_model('billing', 'Subscription').objects.filter(tenant_id=tenant_id).exists()
        )

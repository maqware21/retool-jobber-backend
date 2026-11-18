import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("tenants", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Subscription",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("billing_exempt", models.BooleanField(default=False)),
                (
                    "billing_email",
                    models.EmailField(
                        blank=True, db_index=True, default="", max_length=255
                    ),
                ),
                (
                    "stripe_customer_id",
                    models.CharField(
                        blank=True, max_length=64, null=True, unique=True
                    ),
                ),
                (
                    "stripe_subscription_id",
                    models.CharField(
                        blank=True, max_length=64, null=True, unique=True
                    ),
                ),
                (
                    "stripe_checkout_session_id",
                    models.CharField(
                        blank=True, max_length=128, null=True, unique=True
                    ),
                ),
                ("status", models.CharField(blank=True, default="", max_length=32)),
                ("trial_end", models.DateTimeField(blank=True, null=True)),
                ("current_period_end", models.DateTimeField(blank=True, null=True)),
                ("cancel_at_period_end", models.BooleanField(default=False)),
                ("canceled_at", models.DateTimeField(blank=True, null=True)),
                (
                    "card_fingerprint",
                    models.CharField(
                        blank=True, db_index=True, default="", max_length=64
                    ),
                ),
                ("welcome_email_sent_at", models.DateTimeField(blank=True, null=True)),
                ("needs_review", models.BooleanField(default=False)),
                ("review_note", models.TextField(blank=True, default="")),
                (
                    "tenant",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="subscription",
                        to="tenants.tenant",
                    ),
                ),
            ],
            options={
                "verbose_name": "subscription",
                "verbose_name_plural": "subscriptions",
                "db_table": "billing_subscriptions",
            },
        ),
        migrations.CreateModel(
            name="StripeEvent",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("event_id", models.CharField(max_length=64, unique=True)),
                ("event_type", models.CharField(max_length=100)),
                ("stripe_created", models.DateTimeField()),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                ("note", models.TextField(blank=True, default="")),
            ],
            options={
                "verbose_name": "Stripe event",
                "verbose_name_plural": "Stripe events",
                "db_table": "billing_stripe_events",
            },
        ),
    ]

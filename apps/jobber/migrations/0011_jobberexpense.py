# Hand-written (this project's migrations are never makemigrations-
# generated and never run by the assistant — see settings_migration_test.py's
# own docstring for why, and verify against that throwaway SQLite setup
# before this is run for real).
#
# New JobberExpense model (2026-09-14, approved cost_breakdown_dynamic_
# categories_proposal.md) — a local mirror of Jobber's real Expense
# entity, for the Accounts panel's real "Total Expenses (YTD)" stat.
# Same shape/conventions as JobberTimeSheetEntry's own migration
# (0004_jobbertimesheetentry.py) — DateModel base, tenant FK, a nullable
# job FK (SET_NULL — Jobber allows a genuinely job-less expense).

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("jobber", "0010_jobberjob_line_item_cost"),
        ("tenants", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="JobberExpense",
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
                ("jobber_id", models.CharField(db_index=True, max_length=255)),
                ("title", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True, null=True)),
                ("incurred_at", models.DateTimeField(blank=True, null=True)),
                ("total", models.DecimalField(decimal_places=2, max_digits=12)),
                ("synced_at", models.DateTimeField()),
                (
                    "job",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="expenses",
                        to="jobber.jobberjob",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="jobber_expenses",
                        to="tenants.tenant",
                    ),
                ),
            ],
            options={
                "verbose_name": "jobber expense",
                "verbose_name_plural": "jobber expenses",
                "db_table": "jobber_expenses",
            },
        ),
        migrations.AddConstraint(
            model_name="jobberexpense",
            constraint=models.UniqueConstraint(
                fields=("tenant", "jobber_id"),
                name="unique_jobber_expense_tenant_jobber_id",
            ),
        ),
    ]

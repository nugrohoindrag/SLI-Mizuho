from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


DEFAULT_RATES = (
    ("PNPB1", "PNPB 0 - 50jt", "0", "50000000", True, "50000", 1),
    ("PNPB2", "PNPB 50jt - 100jt", "50000000", "100000000", False, "100000", 2),
    ("PNPB3", "PNPB 100jt - 250jt", "100000000", "250000000", False, "200000", 3),
    ("PNPB4", "PNPB 250jt - 500jt", "250000000", "500000000", False, "450000", 4),
    ("PNPB5", "PNPB 500jt - 1M", "500000000", "1000000000", False, "850000", 5),
    ("PNPB6", "PNPB 1M - 100M", "1000000000", "100000000000", False, "1800000", 6),
    ("PNPB7", "PNPB 100M - 500M", "100000000000", "500000000000", False, "3500000", 7),
    ("PNPB8", "PNPB 500M - 1 T", "500000000000", "1000000000000", False, "6400000", 8),
    ("PNPB9", "PNPB > 1 T", "1000000000000", None, False, "12000000", 9),
)


def seed_default_rates(apps, schema_editor):
    PnbpRate = apps.get_model("AdminPnbp", "PnbpRate")
    PnbpRate.objects.bulk_create(
        [
            PnbpRate(
                code_key=code_key,
                label=label,
                min_amount=Decimal(min_amount),
                max_amount=Decimal(max_amount) if max_amount is not None else None,
                min_inclusive=min_inclusive,
                fee=Decimal(fee),
                is_active=True,
                sort_order=sort_order,
            )
            for code_key, label, min_amount, max_amount, min_inclusive, fee, sort_order in DEFAULT_RATES
        ],
        ignore_conflicts=True,
    )


def remove_default_rates(apps, schema_editor):
    PnbpRate = apps.get_model("AdminPnbp", "PnbpRate")
    PnbpRate.objects.filter(code_key__in=[row[0] for row in DEFAULT_RATES]).delete()


class Migration(migrations.Migration):
    initial = True

    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="PnbpRate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code_key", models.CharField(max_length=50, unique=True)),
                ("label", models.CharField(max_length=100)),
                ("min_amount", models.DecimalField(decimal_places=2, max_digits=20)),
                ("max_amount", models.DecimalField(blank=True, decimal_places=2, max_digits=20, null=True)),
                ("min_inclusive", models.BooleanField(default=False)),
                ("fee", models.DecimalField(decimal_places=2, max_digits=20)),
                ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_pnbp_rates",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="updated_pnbp_rates",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "pnbp_rates",
                "ordering": ("sort_order", "min_amount", "id"),
                "constraints": (
                    models.CheckConstraint(
                        condition=models.Q(min_amount__gte=Decimal("0")),
                        name="pnbp_rate_min_nonnegative",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(fee__gte=Decimal("0")),
                        name="pnbp_rate_fee_nonnegative",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(max_amount__isnull=True) | models.Q(max_amount__gt=models.F("min_amount")),
                        name="pnbp_rate_valid_bounds",
                    ),
                ),
            },
        ),
        migrations.RunPython(seed_default_rates, remove_default_rates),
    ]

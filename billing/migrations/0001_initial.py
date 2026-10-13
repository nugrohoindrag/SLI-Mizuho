from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BillingPaymentUpload",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file_name", models.CharField(max_length=255)),
                ("file_path", models.CharField(blank=True, default="", max_length=255)),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                ("total_rows", models.PositiveIntegerField(default=0)),
                ("paid_count", models.PositiveIntegerField(default=0)),
                ("unpaid_count", models.PositiveIntegerField(default=0)),
                ("already_paid_count", models.PositiveIntegerField(default=0)),
                ("not_found_count", models.PositiveIntegerField(default=0)),
                ("invalid_count", models.PositiveIntegerField(default=0)),
                ("details", models.JSONField(blank=True, default=dict)),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "billing_payment_uploads",
                "ordering": ["-uploaded_at"],
            },
        ),
    ]

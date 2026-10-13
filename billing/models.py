from django.conf import settings
from django.db import models


class BillingPaymentUpload(models.Model):
    """Log upload Excel pembayaran dari klien (siapa, kapan, file, hasil)."""

    file_name = models.CharField(max_length=255)
    file_path = models.CharField(max_length=255, blank=True, default="")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, models.SET_NULL, null=True, blank=True, related_name="+"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    total_rows = models.PositiveIntegerField(default=0)
    paid_count = models.PositiveIntegerField(default=0)
    unpaid_count = models.PositiveIntegerField(default=0)
    already_paid_count = models.PositiveIntegerField(default=0)
    not_found_count = models.PositiveIntegerField(default=0)
    invalid_count = models.PositiveIntegerField(default=0)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "billing_payment_uploads"
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.file_name} ({self.uploaded_at:%Y-%m-%d %H:%M})"

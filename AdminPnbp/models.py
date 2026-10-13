from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class PnbpRate(models.Model):
    code_key = models.CharField(max_length=50, unique=True)
    label = models.CharField(max_length=100)
    min_amount = models.DecimalField(max_digits=20, decimal_places=2)
    max_amount = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)
    min_inclusive = models.BooleanField(default=False)
    fee = models.DecimalField(max_digits=20, decimal_places=2)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_pnbp_rates",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_pnbp_rates",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pnbp_rates"
        ordering = ("sort_order", "min_amount", "id")
        constraints = (
            models.CheckConstraint(
                condition=Q(min_amount__gte=Decimal("0")),
                name="pnbp_rate_min_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(fee__gte=Decimal("0")),
                name="pnbp_rate_fee_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(max_amount__isnull=True) | Q(max_amount__gt=models.F("min_amount")),
                name="pnbp_rate_valid_bounds",
            ),
        )

    def __str__(self):
        return f"{self.code_key} - {self.label}"

    def clean_configuration_fields(self):
        self.code_key = (self.code_key or "").strip().upper()
        self.label = (self.label or "").strip()

        if self.min_amount is None or self.min_amount < 0:
            raise ValidationError({"min_amount": "Batas bawah harus bernilai nol atau lebih."})
        if self.max_amount is not None and self.max_amount <= self.min_amount:
            raise ValidationError({"max_amount": "Batas atas harus lebih besar dari batas bawah."})
        if self.fee is None or self.fee < 0:
            raise ValidationError({"fee": "Biaya PNBP harus bernilai nol atau lebih."})

    def clean(self):
        super().clean()
        self.clean_configuration_fields()
        if not self.is_active:
            return

        for other in type(self).objects.filter(is_active=True).exclude(pk=self.pk):
            if self._overlaps(other):
                raise ValidationError(
                    {"non_field_errors": f"Rentang bertumpang tindih dengan {other.code_key} ({other.label})."}
                )

    def _overlaps(self, other):
        if self.max_amount is not None:
            if self.max_amount < other.min_amount:
                return False
            if self.max_amount == other.min_amount and not other.min_inclusive:
                return False
        if other.max_amount is not None:
            if other.max_amount < self.min_amount:
                return False
            if other.max_amount == self.min_amount and not self.min_inclusive:
                return False
        return True

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

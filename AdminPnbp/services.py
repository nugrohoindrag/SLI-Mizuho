from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError

from .models import PnbpRate


class PnbpConfigurationError(ValidationError):
    pass


@dataclass(frozen=True)
class ResolvedPnbpRate:
    code_key: str
    label: str
    fee: Decimal


def _amount(value):
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PnbpConfigurationError("Nilai penjaminan tidak valid.") from exc
    if amount < 0:
        raise PnbpConfigurationError("Nilai penjaminan tidak boleh negatif.")
    return amount


def validate_active_rate_set(rates):
    active_rates = sorted(
        (rate for rate in rates if rate.is_active),
        key=lambda rate: (rate.min_amount, rate.sort_order, rate.pk or 0),
    )
    if not active_rates:
        raise PnbpConfigurationError("Minimal satu rentang PNBP harus aktif.")

    first = active_rates[0]
    if first.min_amount != Decimal("0") or not first.min_inclusive:
        raise PnbpConfigurationError(
            "Rentang PNBP aktif pertama harus dimulai dari Rp0 dan bersifat inklusif."
        )

    for index, rate in enumerate(active_rates):
        is_last = index == len(active_rates) - 1
        if is_last:
            if rate.max_amount is not None:
                raise PnbpConfigurationError(
                    "Rentang PNBP aktif terakhir harus tanpa batas atas."
                )
            continue

        if rate.max_amount is None:
            raise PnbpConfigurationError(
                f"{rate.code_key} tanpa batas atas tetapi bukan rentang terakhir."
            )

        following = active_rates[index + 1]
        if following.min_amount != rate.max_amount:
            raise PnbpConfigurationError(
                f"Ada gap antara {rate.code_key} dan {following.code_key}."
            )
        if following.min_inclusive:
            raise PnbpConfigurationError(
                f"Batas {following.code_key} bertumpang tindih dengan {rate.code_key}."
            )

    return active_rates


class PnbpRateResolver:
    def __init__(self, rates):
        self.rates = validate_active_rate_set(rates)

    @classmethod
    def from_database(cls, using="default"):
        rates = list(
            PnbpRate.objects.using(using)
            .filter(is_active=True)
            .order_by("min_amount", "sort_order", "id")
        )
        return cls(rates)

    def resolve(self, guarantee_value):
        amount = _amount(guarantee_value)
        if amount is None:
            return None

        matches = []
        for rate in self.rates:
            lower_matches = (
                amount >= rate.min_amount
                if rate.min_inclusive
                else amount > rate.min_amount
            )
            upper_matches = rate.max_amount is None or amount <= rate.max_amount
            if lower_matches and upper_matches:
                matches.append(rate)

        if len(matches) != 1:
            raise PnbpConfigurationError(
                f"Nilai penjaminan {amount} tidak memiliki tepat satu rentang PNBP aktif."
            )

        rate = matches[0]
        return ResolvedPnbpRate(
            code_key=rate.code_key,
            label=rate.label,
            fee=rate.fee,
        )

    def fee_for(self, guarantee_value):
        resolved = self.resolve(guarantee_value)
        return resolved.fee if resolved else None


def calculate_pnbp(guarantee_value, using="default"):
    return PnbpRateResolver.from_database(using=using).fee_for(guarantee_value)


def apply_dynamic_pnbp(instance, using="default", resolver=None):
    if str(getattr(instance, "is_st_used", "")) != "1":
        return False

    resolver = resolver or PnbpRateResolver.from_database(using=using)
    fee = resolver.fee_for(getattr(instance, "guarantee_value", None))
    value = format(fee, "f") if fee is not None else None
    if getattr(instance, "pnbp_fee", None) == value:
        return False
    instance.pnbp_fee = value
    return True

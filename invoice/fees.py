from decimal import Decimal, InvalidOperation

from .models import CommonCodes

SERVICE_FEE_KEY = 'BIAYA_JASA'
SERVICE_FEE_TIER2_KEY = 'BIAYA_JASA_2'
SERVICE_FEE_DEFAULT = Decimal('70000')


def common_code_decimal(code_key, fallback):
    """
    Nilai numerik dari common_codes.code_value.
    Jangan pakai .first(): Django menambah ORDER BY id, padahal common_codes tidak punya kolom id.
    """
    vals = list(CommonCodes.objects.filter(code_key=code_key).values_list('code_value', flat=True)[:1])
    try:
        return Decimal(str(vals[0]).strip())
    except (IndexError, InvalidOperation):
        return Decimal(fallback)


def flat_service_fee():
    """Biaya jasa per akta (Mizuho: flat, tanpa tier volume bulanan)."""
    return common_code_decimal(SERVICE_FEE_KEY, SERVICE_FEE_DEFAULT)

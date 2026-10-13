from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase

from invoice import fees
from invoice.models import CommonCodes


class CommonCodeDecimalTests(SimpleTestCase):
    def _patch(self, values):
        qs = mock.MagicMock()
        qs.filter.return_value.values_list.return_value.__getitem__.return_value = values
        return mock.patch.object(fees.CommonCodes, 'objects', qs)

    def test_reads_value(self):
        with self._patch(['75000']):
            self.assertEqual(fees.common_code_decimal('BIAYA_JASA', 70000), Decimal('75000'))

    def test_fallback_when_missing_or_invalid(self):
        for values in ([], [None], [''], ['abc']):
            with self._patch(values):
                self.assertEqual(fees.flat_service_fee(), Decimal('70000'))

    def test_query_has_no_order_by_id(self):
        # common_codes tidak punya kolom id; .first() akan menambah ORDER BY id dan gagal
        qs = CommonCodes.objects.filter(code_key='BIAYA_JASA').values_list('code_value', flat=True)[:1]
        self.assertNotIn('ORDER BY', str(qs.query))

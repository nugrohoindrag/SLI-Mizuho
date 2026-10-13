from datetime import date
from types import SimpleNamespace

from django.test import SimpleTestCase

from .views import _document_minute_offset, _eligible_consumers_qs, mizuho_deed_placeholders


def _consumer(**kw):
    base = dict(
        spb_date=date(2024, 3, 3), name_on_bpkb='SHINTA', debtor_name='HAMID', house_number='B 1111 HKE',
        marital_status='SINGLE', gender='MALE', spouse_name='-', director_gender=None,
        director_birth_place=None, director_birth_date=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class MizuhoDeedPlaceholderTests(SimpleTestCase):
    def test_single(self):
        ph = mizuho_deed_placeholders(_consumer())
        self.assertEqual(ph['tgl_kuasa'], '03-03-2024')
        self.assertTrue(ph['terbilang_tgl_kuasa'])
        self.assertEqual(ph['nama_bpkb'], 'SHINTA')
        self.assertEqual(ph['no_polisi'], 'B 1111 HKE')
        self.assertIs(ph['ada_pasangan'], False)
        self.assertNotIn('pasangan_nama', ph)

    def test_married_prints_spouse(self):
        ph = mizuho_deed_placeholders(_consumer(
            marital_status='Married', spouse_name='SITI', director_gender='FEMALE',
            director_birth_place='BOGOR', director_birth_date=date(1990, 1, 2),
        ))
        self.assertIs(ph['ada_pasangan'], True)
        self.assertEqual(ph['pasangan_sebutan'], 'istri')
        self.assertEqual(ph['pasangan_nama'], 'SITI')
        self.assertEqual(ph['pasangan_tgl_lahir'], '02-01-1990')
        self.assertTrue(ph['terbilang_tgl_lahir_pasangan'])

    def test_married_without_spouse_data_is_left_blank_for_validation(self):
        ph = mizuho_deed_placeholders(_consumer(marital_status='MARRIED'))
        self.assertEqual(ph['pasangan_nama'], '')
        self.assertEqual(ph['pasangan_tgl_lahir'], '')
        self.assertEqual(ph['pasangan_sebutan'], 'istri')  # debitur MALE

    def test_fallbacks(self):
        ph = mizuho_deed_placeholders(_consumer(spb_date=None, name_on_bpkb='-', house_number=''))
        self.assertEqual(ph['tgl_kuasa'], '')
        self.assertEqual(ph['nama_bpkb'], 'HAMID')
        self.assertEqual(ph['no_polisi'], '-')

    def test_other_marital_status_has_no_spouse(self):
        for status in ('SINGLE', 'CERAI HIDUP', 'WIDOW', ''):
            self.assertIs(mizuho_deed_placeholders(_consumer(marital_status=status))['ada_pasangan'], False)


class EligibleConsumersTests(SimpleTestCase):
    def test_requires_complete_unprocessed_and_normal(self):
        where = str(_eligible_consumers_qs().query).split('WHERE', 1)[1]
        self.assertIn('"document_status" = 1', where)
        self.assertIn('"st_process" = 0', where)
        self.assertIn('"is_st_used" = 1', where)


class AktaMinuteTests(SimpleTestCase):
    def test_five_per_minute(self):
        offsets = [_document_minute_offset(i, 5) for i in range(12)]
        self.assertEqual(offsets, [0] * 5 + [1] * 5 + [2] * 2)

    def test_ten_per_minute(self):
        self.assertEqual(_document_minute_offset(9, 10), 0)
        self.assertEqual(_document_minute_offset(10, 10), 1)

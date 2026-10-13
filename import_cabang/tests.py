from django.test import SimpleTestCase

from .views import (
    AGREEMENT_NUMBER_RE,
    FIELD_MAPPING_NORMALIZED,
    MIZUHO_DATE_FIELDS,
    MIZUHO_REQUIRED_FIELDS,
    branch_code_from_agreement,
    mizuho_row_warnings,
    normalize_date_str,
    normalize_header,
)

# Header baris 1 file "contoh format baku order fidusia.xlsx" (kolom A–BR)
MIZUHO_HEADERS = [
    'BRANCH', 'NAMA CABANG', 'PEMBERI FIDUSIA', 'JENIS KELAMIN PEMBERI FIDUSIA',
    'MARITAL STATUS PEMBERI FIDUSIA', 'TEMPAT LAHIR', 'TANGGAL LAHIR', 'PEKERJAAN',
    'ALAMAT', 'RT', 'RW', 'KELURAHAN', 'KECAMATAN', 'KABUPATEN', 'PROVINSI',
    'IDENTITAS NUMBER PEMBERI FIDUSIA', 'TANGGAL KUASA', 'PASANGAN PEMBERI FIDUSIA',
    'JENIS KELAMIN PASANGAN PEMBERI FIDUSIA', 'MARITAL STATUS PASANGAN PEMBERI FIDUSIA',
    'TEMPAT LAHIR PASANGAN', 'TANGGAL LAHIR PASANGAN', 'ALAMAT FIDUSIA', 'RT FIDUSIA',
    'RW FIDUSIA', 'KELURAHAN FIDUSIA', 'KECAMATAN FIDUSIA', 'KABUPATEN FIDUSIA',
    'PROVINSI FIDUSIA', 'IDENTITAS NUMBER FIDUSIA', 'NOMOR KONTRAK', 'TANGGAL KONTRAK',
    'NAMA DEBITUR', 'JENIS KELAMIN DEBITUR', 'MARITAL STATUS DEBITUR', 'TEMPAT LAHIR DEBITUR',
    'TANGGAL LAHIR DEBITUR', 'ALAMAT DEBITUR', 'RT DEBITUR', 'RW DEBITUR', 'KELURAHAN DEBITUR',
    'KECAMATAN DEBITUR', 'KABUPATEN DEBITUR', 'PROVINSI DEBITUR', 'IDENTITAS NUMBER DEBITUR',
    'HUTANG POKOK', 'NILAI JAMINAN', 'NILAI BARANG', 'MERK', 'TYPE', 'TAHUN', 'WARNA',
    'NOMOR RANGKA', 'NOMOR MESIN', 'NOMOR BPKB/ COVER NOTE', 'NAMA_BPKB', 'ATAS NAMA KUITANSI',
    'NO AKTA', 'AKTA DATE', 'NO SERTIFIKAT', 'SERTIFIKAT DATE', 'TENOR', 'MULAI ANGSURAN',
    'BERAKHIR ANGSURAN', 'NO BPKB', 'NO POL', 'NAMA BPKB', 'KODE POS PEMBERI FIDUSIA',
    'NO HANDPHONE PEMBERI FIDUSIA', 'TIPE',
]


class MizuhoFieldMappingTests(SimpleTestCase):
    def test_all_70_headers_are_recognized(self):
        self.assertEqual(len(MIZUHO_HEADERS), 70)
        unknown = [h for h in MIZUHO_HEADERS if normalize_header(h) not in FIELD_MAPPING_NORMALIZED]
        self.assertEqual(unknown, [])

    def test_reused_taf_columns(self):
        m = lambda h: FIELD_MAPPING_NORMALIZED[normalize_header(h)]
        self.assertEqual(m('TANGGAL KUASA'), 'spb_date')
        self.assertEqual(m('JENIS KELAMIN PASANGAN PEMBERI FIDUSIA'), 'director_gender')
        self.assertEqual(m('TANGGAL LAHIR PASANGAN'), 'director_birth_date')
        self.assertEqual(m('NO POL'), 'house_number')

    def test_duplicate_and_debtor_columns_are_ignored(self):
        for h in ['NO BPKB', 'NAMA DEBITUR', 'ALAMAT FIDUSIA', 'NO AKTA', 'SERTIFIKAT DATE']:
            self.assertIsNone(FIELD_MAPPING_NORMALIZED[normalize_header(h)], h)
        # NAMA_BPKB (BD) dan NAMA BPKB (BO) bertabrakan setelah normalisasi; BO dibuang saat baca.
        self.assertEqual(normalize_header('NAMA_BPKB'), normalize_header('NAMA BPKB'))

    def test_required_and_date_fields_are_mapped(self):
        mapped = set(v for v in FIELD_MAPPING_NORMALIZED.values() if v)
        self.assertTrue(set(MIZUHO_REQUIRED_FIELDS) <= mapped)
        self.assertTrue(set(MIZUHO_DATE_FIELDS) <= mapped)


class MizuhoValueTests(SimpleTestCase):
    def test_branch_code_from_agreement(self):
        self.assertEqual(branch_code_from_agreement('0015014718-001'), '0015')
        self.assertEqual(branch_code_from_agreement(' 0101999-001 '), '0101')
        self.assertEqual(branch_code_from_agreement('AB12-001'), '')
        self.assertEqual(branch_code_from_agreement(None), '')

    def test_normalize_date_str(self):
        self.assertEqual(normalize_date_str('2001-10-14 00:00:00'), '2001-10-14')
        self.assertEqual(normalize_date_str('14/10/2001'), '2001-10-14')
        self.assertEqual(normalize_date_str('14-10-2001'), '2001-10-14')
        self.assertEqual(normalize_date_str(''), '')
        self.assertEqual(normalize_date_str('bukan tanggal'), 'bukan tanggal')

    def test_agreement_number_allows_mizuho_suffix(self):
        self.assertTrue(AGREEMENT_NUMBER_RE.fullmatch('0015014718-001'))
        self.assertTrue(AGREEMENT_NUMBER_RE.fullmatch('260030021195'))
        self.assertFalse(AGREEMENT_NUMBER_RE.fullmatch('0015 014718'))
        self.assertFalse(AGREEMENT_NUMBER_RE.fullmatch('0015014718-'))

    def test_row_warnings(self):
        clean = {'id_number': '3175226821000051', 'chassis_number': 'MK3NCTTATNJ003311', 'address': 'JL.JAMBU NO 110'}
        self.assertEqual(mizuho_row_warnings(clean), [])

        w = mizuho_row_warnings({**clean, 'id_number': '317522682100005'})
        self.assertEqual(len(w), 1)
        self.assertIn('16 digit', w[0])

        w = mizuho_row_warnings({**clean, 'chassis_number': 'MHKS6GJ3JHJ7780947'})
        self.assertEqual(len(w), 1)
        self.assertIn('17 karakter', w[0])

        w = mizuho_row_warnings({**clean, 'address': 'JL KHAIRUL NO 5 RT 003 RW 004'})
        self.assertEqual(len(w), 1)
        self.assertIn('RT/RW', w[0])

    def test_row_warnings_ignore_empty_values(self):
        self.assertEqual(mizuho_row_warnings({'id_number': '-', 'chassis_number': '', 'address': None}), [])

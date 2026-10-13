from datetime import date, datetime

from django.test import SimpleTestCase

from . import services as s

HEADER = tuple(s.EXPORT_HEADERS)
TODAY = date(2026, 10, 13)


def _row(no_inv, no_kon, tgl):
    row = [None] * len(HEADER)
    row[HEADER.index(s.COL_INVOICE)] = no_inv
    row[HEADER.index(s.COL_KONTRAK)] = no_kon
    row[HEADER.index(s.COL_TGL_BAYAR)] = tgl
    return tuple(row)


class PaymentDateTests(SimpleTestCase):
    def test_formats(self):
        self.assertEqual(s.parse_payment_date('07-10-2026'), date(2026, 10, 7))
        self.assertEqual(s.parse_payment_date('07/10/2026'), date(2026, 10, 7))
        self.assertEqual(s.parse_payment_date('2026-10-07'), date(2026, 10, 7))
        self.assertEqual(s.parse_payment_date(datetime(2026, 10, 7, 0, 0)), date(2026, 10, 7))
        self.assertIsNone(s.parse_payment_date(''))
        with self.assertRaises(ValueError):
            s.parse_payment_date('kemarin')


class ParsePaymentRowsTests(SimpleTestCase):
    def test_partial_payment(self):
        rows = [
            HEADER,
            _row('INV-01', '0015014718-001', datetime(2026, 10, 7)),
            _row('INV-01', '0015007150-001', '08-10-2026'),
            _row('INV-01', '0015012717-001', None),       # belum dibayar -> tetap outstanding
            _row(None, None, None),                        # baris kosong diabaikan
        ]
        entries, unpaid, errors = s.parse_payment_rows(rows, today=TODAY)
        self.assertEqual(errors, [])
        self.assertEqual(entries, [
            (2, 'INV-01', '0015014718-001', date(2026, 10, 7)),
            (3, 'INV-01', '0015007150-001', date(2026, 10, 8)),
        ])
        self.assertEqual(unpaid, [4])

    def test_header_must_come_from_export(self):
        entries, unpaid, errors = s.parse_payment_rows([('NO', 'KONTRAK'), ('1', 'A')], today=TODAY)
        self.assertEqual((entries, unpaid), ([], []))
        self.assertIn('TANGGAL BAYAR', errors[0])

    def test_row_errors(self):
        rows = [
            HEADER,
            _row('INV-01', None, '07-10-2026'),
            _row('INV-01', 'K-1', '31-02-2026'),
            _row('INV-01', 'K-2', '14-10-2026'),          # setelah hari ini
        ]
        entries, unpaid, errors = s.parse_payment_rows(rows, today=TODAY)
        self.assertEqual(entries, [])
        self.assertEqual(len(errors), 3)
        self.assertIn('Baris 2', errors[0])
        self.assertIn('Baris 3', errors[1])
        self.assertIn('melebihi hari ini', errors[2])

    def test_header_case_insensitive_and_numeric_contract(self):
        header = tuple(h.lower() for h in HEADER)
        rows = [header, _row('INV-02', 15014718.0, '01-10-2026')]
        entries, _, errors = s.parse_payment_rows(rows, today=TODAY)
        self.assertEqual(errors, [])
        self.assertEqual(entries[0][2], '15014718')


class ExportWorkbookTests(SimpleTestCase):
    def test_headers_and_formats(self):
        row = {
            'no_invoice': 'INV-01', 'tgl_inv': date(2026, 10, 10), 'branch_code': '0015', 'branch_name': 'SERANG',
            'agreement_number': '0015014718-001', 'debtor_name': 'HAMID', 'document_number': 401,
            'document_date': date(2026, 10, 1), 'certificate_number': 'W10', 'certificate_date': date(2026, 10, 3),
            'service_fee': 70000, 'pnbp_fee': 450000, 'total': 520000, 'tgl_receive_payment': None,
        }
        ws = s.build_export_workbook([row], 'Billing').active
        self.assertEqual([c.value for c in ws[1]], s.EXPORT_HEADERS)
        self.assertEqual(ws['E2'].value, '0015014718-001')
        self.assertEqual(ws['G2'].value, '401')
        self.assertEqual(ws['B2'].number_format, 'DD-MM-YYYY')
        self.assertIsNone(ws['N2'].value)  # TANGGAL BAYAR kosong untuk diisi klien

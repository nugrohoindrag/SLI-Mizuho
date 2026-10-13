import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from import_cabang.models import Consumer
from .views import (
    DOCUMENT_STATUS_LABELS, OTD_TRANSITIONS, ST_USED_NORMAL, ST_USED_OTD,
    ConsumerDataJSON, apply_doc_filter, parse_otd_rows,
)

TEMPLATE = Path(settings.BASE_DIR) / 'templates' / 'operator' / 'page-operator-prekonsumen.html'


class TableColumnsTests(SimpleTestCase):
    """Urutan kolom DataTables harus sama dengan COLUMN_MAP agar sorting tidak meleset."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.html = TEMPLATE.read_text(encoding='utf-8')

    def test_order_map_matches_column_map(self):
        block = re.search(r'const ORDER_MAP = \[(.*?)\];', self.html, re.S).group(1)
        order_map = re.findall(r"'([a-z_]+)'", block)
        self.assertEqual(order_map, ConsumerDataJSON.COLUMN_MAP)

    def test_datatable_columns_follow_order_map(self):
        cols = re.search(r'columns: \[(.*?)\n\s*\],\s*fixedHeader', self.html, re.S).group(1)
        data_keys = re.findall(r"data: '([a-z_]+)'", cols)
        self.assertEqual(data_keys, ConsumerDataJSON.COLUMN_MAP)

    def test_thead_count_matches_columns(self):
        thead = re.search(r'<thead.*?</thead>', self.html, re.S).group(0)
        # +1 untuk kolom Aksi
        self.assertEqual(thead.count('<th>'), len(ConsumerDataJSON.COLUMN_MAP) + 1)


class DocFilterTests(SimpleTestCase):
    def _where(self, qs):
        return str(qs.query).split('WHERE', 1)[-1] if 'WHERE' in str(qs.query) else ''

    def test_all_does_not_filter(self):
        qs = Consumer.objects.all()
        self.assertEqual(self._where(apply_doc_filter(qs, 'all')), '')
        self.assertEqual(self._where(apply_doc_filter(qs, None)), '')

    def test_complete_excludes_otd_and_cancel(self):
        where = self._where(apply_doc_filter(Consumer.objects.all(), 'Complete'))
        self.assertIn('"document_status" = 1', where)
        self.assertIn('"is_st_used" = 1', where)


class OtdParseTests(SimpleTestCase):
    def test_valid_rows(self):
        rows = [
            ('NOMOR KONTRAK', 'STATUS'),
            ('0015014718-001', 'otd'),
            (12345.0, ' Normal '),
            (None, None),
        ]
        entries, errors = parse_otd_rows(rows)
        self.assertEqual(errors, [])
        self.assertEqual(entries, [(2, '0015014718-001', 'OTD'), (3, '12345', 'NORMAL')])

    def test_header_mismatch(self):
        entries, errors = parse_otd_rows([('No Perjanjian', 'Status'), ('1', 'OTD')])
        self.assertEqual(entries, [])
        self.assertEqual(len(errors), 1)

    def test_row_errors(self):
        rows = [('NOMOR KONTRAK', 'STATUS'), (None, 'OTD'), ('A-1', 'CANCEL')]
        entries, errors = parse_otd_rows(rows)
        self.assertEqual(entries, [])
        self.assertIn('Baris 2', errors[0])
        self.assertIn('Baris 3', errors[1])

    def test_empty_file(self):
        self.assertEqual(parse_otd_rows([]), ([], ['File kosong.']))

    def test_transitions_never_touch_cancel(self):
        self.assertEqual(OTD_TRANSITIONS['OTD'], (ST_USED_NORMAL, ST_USED_OTD))
        self.assertEqual(OTD_TRANSITIONS['NORMAL'], (ST_USED_OTD, ST_USED_NORMAL))

    def test_need_revisi_label(self):
        self.assertEqual(DOCUMENT_STATUS_LABELS['3'], 'Need Revisi')

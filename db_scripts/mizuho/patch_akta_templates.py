"""
Patch template akta untuk Mizuho (requirement §7). Idempoten, jalankan dari root repo:

    python db_scripts/mizuho/patch_akta_templates.py

T1  Semua template: "Toyota Astra Financial Services" -> "Mizuho Leasing Indonesia Tbk"
    (frasa "Service Head" tetap). Frasa selalu utuh dalam satu <w:t>, jadi diganti per-run.
T2  Template Konvensional - Personal - customer (+ Salinan): tanggal Surat Kuasa
    Membebankan Jaminan Fidusia memakai {{tgl_kuasa}} (sebelumnya tanggal perjanjian).
T3  Idem: klausul persetujuan pasangan, hanya tercetak bila {{ada_pasangan}}.
T4  Idem: baris "Atas Nama BPKB" di uraian obyek jaminan.
T5  Idem: baris "Nomor Polisi" di uraian obyek jaminan.

Redaksi T2–T5 adalah DRAF dan wajib direview notaris sebelum go-live.
"""
import copy
import glob
import os
import shutil
import tempfile
import zipfile

from docx import Document
from docx.text.paragraph import Paragraph
from docx.text.run import Run

OLD_NAME = 'Toyota Astra Financial Services'
NEW_NAME = 'Mizuho Leasing Indonesia Tbk'

CUSTOMER_TEMPLATES = [
    'media/templatesakta/individual/New BPKB - Draft Akta Konvensional - Personal - customer.docx',
    'media/templatesakta-salinan/individual/New BPKB - Draft Akta Konvensional - Personal - customer - Salinan.docx',
]

SPOUSE_CLAUSE = (
    '{% if ada_pasangan %}, yang dalam melakukan tindakan hukum dalam akta ini telah mendapat '
    'persetujuan dari {{pasangan_sebutan}}nya, {{pasangan_nama}}, lahir di {{pasangan_tempat_lahir}}, '
    'tanggal {{pasangan_tgl_lahir}} ({{terbilang_tgl_lahir_pasangan}}){% endif %}'
)


def replace_company_name(path):
    """T1: ganti teks di semua part word/*.xml; entri zip lain disalin apa adanya."""
    changed = 0
    fd, tmp = tempfile.mkstemp(suffix='.docx')
    os.close(fd)
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename.startswith('word/') and item.filename.endswith('.xml'):
                text = data.decode('utf8')
                n = text.count(OLD_NAME)
                if n:
                    changed += n
                    data = text.replace(OLD_NAME, NEW_NAME).encode('utf8')
            dst.writestr(item, data)
    if changed:
        shutil.move(tmp, path)
    else:
        os.remove(tmp)
    return changed


def _insert_run_before(anchor_run, text):
    """Run baru dengan format (rPr) sama seperti anchor; Run.text mengosongkan isi lama."""
    new_r = copy.deepcopy(anchor_run._r)
    anchor_run._r.addprevious(new_r)
    Run(new_r, anchor_run._parent).text = text


def _clone_paragraph_after(para, label, field):
    """Salin paragraf 'Tahun : {{tahun}},' jadi '<label>\t: {{field}},' tepat setelahnya."""
    new_p = copy.deepcopy(para._p)
    para._p.addnext(new_p)
    new_para = Paragraph(new_p, para._parent)
    runs = new_para.runs
    runs[0].text = f'{label}\t: '
    for r in runs:
        if r.text == 'tahun':
            r.text = field
    return new_para


def patch_customer_template(path):
    doc = Document(path)
    paras = doc.paragraphs
    done = []
    full_text = '\n'.join(p.text for p in paras)

    # T2 — tanggal SKMJF = tanggal kuasa
    if '{{tgl_kuasa}}' not in full_text:
        p = next(p for p in paras if p.text.startswith('Fidusia, dibawah tangan, tertanggal'))
        for r in p.runs:
            if r.text == 'tgl_perjanjian':
                r.text = 'tgl_kuasa'
            elif r.text == 'terbilang_tgl_perjanjian':
                r.text = 'terbilang_tgl_kuasa'
        assert '{{tgl_kuasa}}' in p.text and '{{terbilang_tgl_kuasa}}' in p.text, path
        done.append('T2')

    # T3 — klausul pasangan setelah NIK debitur
    if 'ada_pasangan' not in full_text:
        p = next(p for p in paras if p.text.startswith('a.penerima kuasa dari') and '{{ktp_debitur}}' in p.text)
        runs = p.runs
        idx = max(i for i, r in enumerate(runs) if r.text == 'debitur')  # run terakhir milik {{ktp_debitur}}
        anchor = next(r for r in runs[idx + 1:] if r.text.startswith(';'))
        _insert_run_before(anchor, SPOUSE_CLAUSE)
        assert SPOUSE_CLAUSE in p.text, path
        done.append('T3')

    # T4/T5 — Nomor Polisi & Atas Nama BPKB setelah baris Tahun
    if '{{no_polisi}}' not in full_text:
        p_tahun = next(p for p in paras if p.text.startswith('Tahun') and '{{tahun}}' in p.text)
        p_bpkb = _clone_paragraph_after(p_tahun, 'Atas Nama BPKB', 'nama_bpkb')
        _clone_paragraph_after(p_tahun, 'Nomor Polisi', 'no_polisi')
        assert '{{nama_bpkb}}' in p_bpkb.text, path
        done.extend(['T5', 'T4'])

    if done:
        doc.save(path)
    return done


def main():
    files = sorted(glob.glob('media/templatesakta*/**/*.docx', recursive=True))
    total = 0
    for f in files:
        n = replace_company_name(f)
        total += n
        if n:
            print(f'T1 {n:3d}x  {f}')
    print(f'T1 total: {total} penggantian di {len(files)} file')

    for f in CUSTOMER_TEMPLATES:
        print('T2-T5', patch_customer_template(f) or 'sudah terpasang', f)

    leftovers = [f for f in files if OLD_NAME.encode() in b''.join(
        zipfile.ZipFile(f).read(n) for n in zipfile.ZipFile(f).namelist() if n.startswith('word/'))]
    assert not leftovers, leftovers


if __name__ == '__main__':
    main()

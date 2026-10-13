import re, os
from django.forms.models import model_to_dict
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from collections import Counter, defaultdict
from assignaktaMU.models import VehicleUnit
from common.const.id_calendar import BULAN_ID, HARI_ID
from common.enum.choices import AktaType, DocType, AgreementType, CustomerType
from common.util.id_formatters import safe_split_birth_info, tanggal_terbilang, terbilang_tanggal_lahir, format_rupiah, \
    get_region_name_or_placeholder, terbilang_angka, get_nationality_full, tanggal_indo, get_time_zone_full, terbilang_unit
from import_cabang.models import Consumer, Branch, NotaryBranchArea, BranchDecree
from .registry import get_consumer_model, get_document_model, get_log_model
from .serializers import ConsumerSerializer
from warkahDokument.models import LegalDocument
from AdminNotaries.models import Notary
from AdminProxies.models import AdminProxy
from AdminRecipients.models import Recipient
from .models import Witness
from django.shortcuts import get_object_or_404, redirect, render
from accesslog.models import ActivityLog
from AdminMenu.auth_helpers import class_permission_required, get_user_permissions, get_user_role_names, permission_required, user_has_permission
from rest_framework.permissions import IsAuthenticated
from django.http import JsonResponse, Http404, FileResponse, HttpResponse
from datetime import date, datetime, time, timedelta
from django.utils.timezone import localdate, make_aware, now
from django.utils import timezone
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Upper, Trim, Replace
from docxtpl import DocxTemplate, RichText
from django.conf import settings
from num2words import num2words
from django.db.models import Q, Value, F, Subquery, OuterRef
import pandas as pd
from django.views.decorators.http import require_GET
from django.db import transaction
from django.core.exceptions import FieldDoesNotExist, FieldError
from io import BytesIO
from openpyxl import load_workbook
from openpyxl.styles import Font, Border, Alignment
from openpyxl.utils import get_column_letter
from types import SimpleNamespace
from AktaSertifikat.models import AktaMinutaLog, AktaMinutaLogOC, AktaMinutaLogMU, AktaMinutaLogPF
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views import View
from django.contrib.auth.decorators import login_required
from common.const.role_map import has_role, ROLE_KEYS
from AdminUser.branch_access import get_primary_user_branch_code, get_user_branch_codes, user_is_branch_restricted
from invoice.models import CommonCodes

@login_required(login_url="")
@permission_required("read-akta")
def get_notaris_list(request):
    notaris = Notary.objects.filter(is_active=1).values('id', 'firm_name')
    return JsonResponse(list(notaris), safe=False)

def _normalize_name(name: str) -> str:
    """Ambil inti nama: sebelum koma, buang . dan ,, rapikan spasi, uppercase."""
    if not name:
        return ''
    head = name.split(',')[0]
    head = re.sub(r'[.,]', '', head)
    return ' '.join(head.split()).upper()

def _user_branch_code(user):
    return get_primary_user_branch_code(user)

def _user_branch_codes(user):
    return get_user_branch_codes(user)

def _is_superadmin(user):
    role_names = list(get_user_role_names(user))
    return (
        has_role(role_names, ROLE_KEYS["SUPERADMIN"])
        or has_role(role_names, ROLE_KEYS["HO"])
        or has_role(role_names, ROLE_KEYS["OPERATOR"])
        or has_role(role_names, ROLE_KEYS["WARKAH"])
    )

def _user_is_staff_cabang(user):
    """Check if user has staff_cabang role"""
    return user_is_branch_restricted(user)

def _scope_legal_documents_to_user_branches(qs, user):
    if not _user_is_staff_cabang(user):
        return qs
    user_branches = _user_branch_codes(user)
    if not user_branches:
        return qs.none()
    return qs.filter(
        Q(consumer__branch__branch_code__in=user_branches)
        | Q(consumer__branch_id__in=user_branches)
    )

def _consumer_belongs_to_user_branches(consumer, user):
    if not _user_is_staff_cabang(user):
        return True
    user_branches = {str(branch) for branch in _user_branch_codes(user)}
    consumer_branch = None
    if getattr(consumer, "branch", None):
        consumer_branch = getattr(consumer.branch, "branch_code", None)
    consumer_branch = consumer_branch or getattr(consumer, "branch_code", None) or getattr(consumer, "branch_id", None)
    return bool(consumer_branch and str(consumer_branch) in user_branches)

def _eligible_consumers_qs():
    # Siap assign: dokumen Complete, belum di-generate, dan normal (bukan OTD/cancel)
    return Consumer.objects.filter(document_status='1', st_process='0', is_st_used=1)

AKTA_PER_MINUTE_KEY = 'interval_minute'
AKTA_PER_MINUTE_DEFAULT = 5

def _akta_per_minute():
    """Jumlah nomor akta per menit jam akta, dari common_codes.interval_minute."""
    # Jangan pakai .first(): menambah ORDER BY id, padahal common_codes tidak punya kolom id.
    vals = list(CommonCodes.objects
                .filter(code_key=AKTA_PER_MINUTE_KEY)
                .values_list('code_value', flat=True)[:1])
    try:
        n = int(str(vals[0]).strip())
        return n if n > 0 else AKTA_PER_MINUTE_DEFAULT
    except (IndexError, TypeError, ValueError):
        return AKTA_PER_MINUTE_DEFAULT

def _document_minute_offset(idx, per_minute):
    """Akta ke-idx (0-based) jatuh di menit ke-berapa setelah jam mulai."""
    return idx // per_minute

_FEMALE = {'FEMALE', 'F', 'P', 'PEREMPUAN', 'WANITA'}
_MALE = {'MALE', 'M', 'L', 'LAKI-LAKI', 'LAKI LAKI', 'PRIA'}

def _blank(v):
    return v is None or str(v).strip() in ('', '-')

def _spouse_title(consumer):
    """'istri' / 'suami' dari jenis kelamin pasangan; fallback kebalikan jenis kelamin debitur."""
    spouse = str(getattr(consumer, 'director_gender', '') or '').strip().upper()
    if spouse in _FEMALE:
        return 'istri'
    if spouse in _MALE:
        return 'suami'
    debtor = str(getattr(consumer, 'gender', '') or '').strip().upper()
    if debtor in _MALE:
        return 'istri'
    if debtor in _FEMALE:
        return 'suami'
    return 'suami/istri'

def mizuho_deed_placeholders(consumer):
    """
    Placeholder tambahan akta Mizuho (requirement §7.2 T2–T5).
    Kolom TAF dipakai ulang: spb_date = tanggal kuasa, director_* = pasangan, house_number = no. polisi.
    Nilai kosong ('') sengaja dibiarkan agar _validate_non_empty menolak akta yang datanya belum lengkap.
    """
    spb_date = getattr(consumer, 'spb_date', None)
    ph = {
        'tgl_kuasa': spb_date.strftime('%d-%m-%Y') if spb_date else '',
        'terbilang_tgl_kuasa': terbilang_tanggal_lahir(spb_date.strftime('%d-%m-%Y')) if spb_date else '',
        'nama_bpkb': (consumer.name_on_bpkb if not _blank(consumer.name_on_bpkb) else consumer.debtor_name) or '',
        # Unit baru bisa belum punya no. polisi: cetak '-' daripada memblokir akta.
        'no_polisi': consumer.house_number if not _blank(consumer.house_number) else '-',
        'ada_pasangan': str(consumer.marital_status or '').strip().upper() == 'MARRIED',
    }
    if ph['ada_pasangan']:
        birth = getattr(consumer, 'director_birth_date', None)
        ph.update({
            'pasangan_sebutan': _spouse_title(consumer),
            'pasangan_nama': '' if _blank(consumer.spouse_name) else consumer.spouse_name.strip(),
            'pasangan_tempat_lahir': '' if _blank(consumer.director_birth_place) else consumer.director_birth_place.strip(),
            'pasangan_tgl_lahir': birth.strftime('%d-%m-%Y') if birth else '',
            'terbilang_tgl_lahir_pasangan': terbilang_tanggal_lahir(birth.strftime('%d-%m-%Y')) if birth else '',
        })
    return ph

def _consumer_has_field(model, name: str) -> bool:
    try:
        model._meta.get_field(name)
        return True
    except FieldDoesNotExist:
        return False

def _eligible_consumers_for_notary(notary_id: int):
    """
    SELECT cs.*
    FROM consumers cs
    JOIN notary_branch_areas na ON
         (cs.branch_id   = na.branch_id)   -- jika Consumer punya FK branch
      OR (cs.branch_code = na.branch_id)   -- jika Consumer simpan kode string
    WHERE na.notary_id = :notary_id
    """
    # Ambil daftar 'branch_id' dari NotaryBranchArea (isinya = kode cabang karena db_column='branch_code')
    branch_ids_sq = NotaryBranchArea.objects.filter(
        notary_id=notary_id
    ).values('branch_id')

    if _consumer_has_field(Consumer, 'branch'):
        # Consumer punya FK 'branch' → gunakan branch_id__in
        qs = _eligible_consumers_qs().filter(branch_id__in=Subquery(branch_ids_sq))
    elif _consumer_has_field(Consumer, 'branch_code'):
        # Consumer simpan string 'branch_code' → cocokkan ke branch_ids_sq (yang berisi kode)
        qs = _eligible_consumers_qs().filter(branch_code__in=Subquery(branch_ids_sq))
    else:
        # Skema lain—kasih pesan jelas
        raise FieldError("Consumer tidak memiliki field 'branch' maupun 'branch_code'.")

    # Postgres: aman pakai distinct('id'); jika bukan Postgres, ganti ke .distinct()
    return qs.distinct('id').order_by('id')

def _match_by_notary_name(consumers, notary: Notary):
    """Filter list[Consumer] berdasarkan kecocokan nama notaris (paling toleran)."""
    core = _normalize_name(getattr(notary, 'full_name', '') or notary.firm_name)
    if not core:
        return []

    core_up = core
    matched = []
    for c in consumers:
        dn = _normalize_name(c.director_notary_name)
        if not dn:
            continue
        # longgar: startswith, two-way contains, dan token overlap minimal 2
        tokens_core = set(core_up.split())
        tokens_dn   = set(dn.split())
        if (
            dn.startswith(core_up) or core_up.startswith(dn) or
            (core_up in dn) or (dn in core_up) or
            len(tokens_core & tokens_dn) >= 2
        ):
            matched.append(c)
    return matched

def _group_notary_names(consumers):
    """
    Buat ringkasan nama notaris di data consumer (untuk diagnosa/fallback UI).
    Return: list dict sorted by count desc.
    """
    counter = Counter(_normalize_name(c.director_notary_name) for c in consumers if c.director_notary_name)
    groups = [{'name_norm': k, 'count': v} for k, v in counter.most_common()]
    return groups

# ---- View ------------------------------------------------------------------
class AssignGenerateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    # =========================
    # PREVIEW: GET
    # =========================
    @class_permission_required('read-akta')
    def get(self, request):
        """
        /assignakta/generate/?notary=<NOTARY_ID>&mock_date=YYYY-MM-DD
        Optional:
          - ?debug=1    : kembalikan diagnostik jika kosong
          - ?fallback=1 : abaikan filter notaris (tampilkan SEMUA eligible global) — untuk diagnosa
        """ 

        notary_id = request.query_params.get('notary')
        date_str  = request.query_params.get('mock_date')
        debug     = request.query_params.get('debug') == '1'
        fallback  = request.query_params.get('fallback') == '1'

        if not notary_id:
            return Response({'error': 'Parameter notary wajib diisi (ID).'}, status=400)
        try:
            notary = Notary.objects.get(id=notary_id)
        except Notary.DoesNotExist:
            return Response({'error': 'Notaris tidak ditemukan.'}, status=404)

        if fallback:
            base = list(_eligible_consumers_qs().order_by('id'))
            return Response({
                'info': 'Fallback ON: mengabaikan filter notaris. Berikut semua consumer eligible secara global.',
                'notary_id': notary.id,
                'consumers': [{
                    'consumer_id': c.id,
                    'agreement_number': getattr(c, 'agreement_number', None),
                    'branch_code': getattr(c, 'branch_code', None),
                } for c in base]
            }, status=200)

        consumers_qs = _eligible_consumers_for_notary(notary.id)

        docs_qs = LegalDocument.objects.select_related('consumer', 'notary').filter(
            consumer__in=consumers_qs,
            notary=notary
        )

        if date_str:
            try:
                filter_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                return Response({'error': 'Format tanggal salah. Gunakan YYYY-MM-DD.'}, status=400)
            start_dt = datetime.combine(filter_date, time.min)
            end_dt   = datetime.combine(filter_date, time.max)
            docs_qs = docs_qs.filter(created_at__range=(start_dt, end_dt))

        if docs_qs.exists():
            result = [{
                'id': d.id,
                'document_number': d.document_number,
                'document_time': d.document_time.strftime('%H:%M') if d.document_time else '',
                'consumer_id': d.consumer.id if d.consumer else None
            } for d in docs_qs]
            return Response(result, status=200)

        consumers = list(consumers_qs)
        if consumers:
            consumers_payload = [{
                'consumer_id': c.id,
                'agreement_number': getattr(c, 'agreement_number', None),
                'branch_code': getattr(c, 'branch_code', None) if hasattr(c, 'branch_code') else getattr(c, 'branch_id', None),
            } for c in consumers]
            return Response({
                'info': 'Belum ada LegalDocument di tanggal/filter ini. Berikut calon consumer yang eligible untuk notaris (via join branch).',
                'consumers': consumers_payload
            }, status=200)

        if debug:
            total_global = _eligible_consumers_qs().count()
            return Response({
                'message': 'Tidak ada consumer eligible untuk notaris ini (berdasarkan mapping branch).',
                'debug': {
                    'notary_id': notary.id,
                    'notary_firm_name': notary.firm_name,
                    'eligible_global_count': total_global,
                }
            }, status=404)

        return Response({'message': 'Tidak ada consumer eligible untuk notaris ini (via join).'}, status=404)

    # =========================
    # GENERATE: POST
    # =========================
    @class_permission_required('create-akta')
    def post(self, request):
        """
        Body JSON:
        { notaris, nomor_awal, nomor_akhir, jam_mulai, tanggal, ignore_notary_name? }
        Catatan: 'ignore_notary_name' = abaikan filter notaris untuk Consumer (eligible global),
        tapi AdminProxy tetap milik notaris yang dipilih.
        """

        notaris_id   = request.data.get('notaris')
        nomor_awal   = request.data.get('nomor_awal')
        nomor_akhir  = request.data.get('nomor_akhir')
        jam_mulai    = request.data.get('jam_mulai')
        tanggal_in   = request.data.get('tanggal')
        ignore_name  = bool(request.data.get('ignore_notary_name'))
        per_poa_quota = int(request.data.get('alokasi_per_adminproxy', 20)) # Ganti Nama PK Tiap 20 Data

        if not (notaris_id and nomor_awal and nomor_akhir and jam_mulai and tanggal_in):
            return Response({'error': 'Semua field (notaris, nomor_awal, nomor_akhir, jam_mulai, tanggal) wajib diisi.'}, status=400)

        try:
            start_num = int(nomor_awal); end_num = int(nomor_akhir)
        except ValueError:
            return Response({'error': 'Nomor awal dan akhir harus berupa angka.'}, status=400)
        if end_num < start_num:
            return Response({'error': 'Nomor akhir tidak boleh lebih kecil dari nomor awal.'}, status=400)

        try:
            jam_obj = datetime.strptime(jam_mulai, "%H:%M").time()
        except ValueError:
            return Response({'error': 'Format jam_mulai harus HH:MM.'}, status=400)

        doc_date = None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
            try:
                doc_date = datetime.strptime(tanggal_in, fmt).date()
                break
            except ValueError:
                pass
        if doc_date is None:
            return Response({'error': 'Format tanggal tidak valid. Gunakan YYYY-MM-DD atau DD/MM/YYYY.'}, status=400)
        
        try:
            recipient = (
                Recipient.objects
                .filter(decree_date__lte=doc_date)
                .filter(Q(decree_date_end__isnull=True) | Q(decree_date_end__gte=doc_date))
                .order_by('-decree_date')
                .first()
            )

            if not recipient:
                return Response({
                    'error': 'Tidak ditemukan Recipient yang berlaku pada tanggal dokumen ini.'
                }, status=400)
        except Recipient.DoesNotExist:
            return Response({
                'error': 'Recipient default tidak ditemukan.'
            }, status=400)

        if recipient.decree_date and doc_date < recipient.decree_date:
            return Response({
                'error': 'Tanggal dokumen lebih kecil dari tanggal SK Recipient.'
            }, status=400)

        if recipient.decree_date_end and doc_date > recipient.decree_date_end:
            return Response({
                'error': 'SK Recipient sudah tidak berlaku pada tanggal dokumen ini.'
            }, status=400)

        try:
            notaris = Notary.objects.get(id=notaris_id)
        except Notary.DoesNotExist:
            return Response({'error': 'Notaris tidak ditemukan.'}, status=404)

        # === Ambil daftar AdminProxy (POA) aktif milik notaris pada tanggal dokumen ===
        doc_key = int(doc_date.strftime('%Y%m%d'))
        poa_qs = AdminProxy.objects.filter(notary_id=notaris.id, is_active=1)

        poa_list = list(
            poa_qs.filter(
                Q(active_from__isnull=True) | Q(active_from__lte=doc_key),
                Q(active_until__isnull=True) | Q(active_until__gte=doc_key),
            ).order_by('-active_from', '-id')
        )
        # fallback: kalau tidak ada yang pas window tanggal, pakai yang aktif saja
        if not poa_list:
            poa_list = list(poa_qs.order_by('-active_from', '-id'))

        # Kalau wajib ada POA, buka komentar di bawah:
        # if not poa_list:
        #     return Response({'error': 'Tidak ada AdminProxy aktif untuk notaris pada tanggal tersebut.'}, status=400)

        # === Ambil consumers eligible ===
        base_qs = _eligible_consumers_qs().order_by('id') if ignore_name else _eligible_consumers_for_notary(notaris.id)
        consumers_all = list(base_qs)
        if not consumers_all:
            return Response({'error': 'Tidak ada data siap assign (dokumen Complete, belum di-generate, bukan OTD).'}, status=400)

        available_count = (end_num - start_num + 1)
        consumers = consumers_all[:available_count]
        if not consumers:
            return Response({'error': 'Range nomor terlalu kecil untuk data yang tersedia.'}, status=400)
        
        assigned_poa_for_idx = None
        if poa_list:
            total_docs = len(consumers)
            pcount = len(poa_list)
            base = total_docs // pcount           # jatah dasar
            rem  = total_docs % pcount            # sisa dibagi ke proxy awal
            # contoh: total=21, pcount=2 -> counts=[11,10]
            counts = [base + (1 if i < rem else 0) for i in range(pcount)]
            # petakan index dokumen -> index poa; ex: [0,0,...(11x), 1,1,...(10x)]
            assigned_poa_for_idx = [i for i, c in enumerate(counts) for _ in range(c)]

        # OPSI: jika TIDAK ingin wrap-around (murni 20 per POA, sisanya tidak dibuat), un-comment:
        # if poa_list:
        #     max_capacity = len(poa_list) * 20
        #     consumers = consumers[:max_capacity]

        pad_len_awal  = len(str(nomor_awal))
        pad_len_akhir = len(str(nomor_akhir))
        use_padding   = (str(nomor_awal).startswith('0') or str(nomor_akhir).startswith('0'))
        pad_len       = max(pad_len_awal, pad_len_akhir)

        if use_padding:
            nomor_range = [str(i).zfill(pad_len) for i in range(start_num, start_num + len(consumers))]
        else:
            nomor_range = [str(i) for i in range(start_num, start_num + len(consumers))]
            
        base_dt = datetime.combine(doc_date, jam_obj)
        per_minute = _akta_per_minute()

        success, failed = [], []
        per_poa_counter = {}

        for idx, cons in enumerate(consumers):
            try:
                with transaction.atomic():
                    nomor = nomor_range[idx]

                    # per_minute akta per menit (common_codes.interval_minute); batch berikutnya +1 menit
                    doc_dt = base_dt + timedelta(minutes=_document_minute_offset(idx, per_minute))

                    # === ALOKASI ADIL PER ADMINPROXY (10/10, 10/11, dst) ===
                    current_poa = None
                    power_receiver_name_val = None
                    if poa_list and assigned_poa_for_idx:
                        poa_index = assigned_poa_for_idx[idx]
                        current_poa = poa_list[poa_index]
                        power_receiver_name_val = current_poa.full_name
                        per_poa_counter[current_poa.id] = per_poa_counter.get(current_poa.id, 0) + 1

                    # Recalculate and persist PNBP before it is snapshotted to the
                    # legal document. The surrounding transaction keeps the old
                    # all-or-nothing behavior if document creation fails.
                    cons.st_process = '1'
                    cons.save(update_fields=['st_process'])

                    legal_doc = LegalDocument.objects.create(
                        consumer=cons,
                        notary=notaris,
                        document_number=nomor,
                        document_date=doc_date,
                        document_time=doc_dt.time(),
                        recipient=recipient,
                        power_receiver_name=power_receiver_name_val,
                        power_user=current_poa if current_poa else None,
                        pnbp_fee=cons.pnbp_fee,
                    )

                    success.append({
                        'legal_document_id': legal_doc.id,
                        'consumer_id': cons.id,
                        'agreement_number': getattr(cons, 'agreement_number', None),
                        'document_number': nomor,
                        'document_time': doc_dt.strftime('%H:%M:%S'),
                        'document_date': str(doc_date),
                        'branch_code': getattr(cons, 'branch_code', None) if hasattr(cons, 'branch_code') else getattr(cons, 'branch_id', None),
                        'power_receiver_name': power_receiver_name_val,
                        'power_user_id': current_poa.id if current_poa else None,
                    })
            except Exception as e:
                failed.append(f"Gagal membuat LegalDocument untuk Consumer ID {cons.id}: {str(e)}")

        # ringkasan alokasi per AdminProxy
        poa_summary = []
        if poa_list and per_poa_counter:
            poa_map = {p.id: p for p in poa_list}
            for poa_id, cnt in per_poa_counter.items():
                p = poa_map.get(poa_id)
                poa_summary.append({
                    'power_user_id': poa_id,
                    'power_receiver_name': p.full_name if p else None,
                    'count_assigned': cnt,
                })

        return Response({
            'message': f'{len(success)} Dokumen berhasil dibuat.',
            'sukses': success,
            'gagal': failed,
            'summary_per_adminproxy': poa_summary,
            'rotation_rule': 'Alokasi merata per AdminProxy (contoh: 20 data & 2 proxy -> 10/10; jika ganjil -> 10/11, dst).'
        }, status=200 if success else 400)

# class LegalDocumentTodayAPIView(APIView):
#     permission_classes = [IsAuthenticated]

#     @class_permission_required('read-akta')
#     def get(self, request, notary_id):
#         date_str = request.query_params.get('mock_date')

#         if date_str:
#             try:
#                 filter_date = datetime.strptime(date_str, "%Y-%m-%d").date()
#             except ValueError:
#                 return Response({'error': 'Format tanggal salah. Gunakan YYYY-MM-DD.'}, status=400)
#         else:
#             latest_doc = LegalDocument.objects.filter(
#                 notary_id=notary_id,
#                 created_at__isnull=False
#             ).order_by('-created_at').first()

#             if not latest_doc:
#                 return Response({'message': 'Tidak ada dokumen untuk notaris ini.'}, status=404)

#             filter_date = latest_doc.created_at.date()

#         start_datetime = datetime.combine(filter_date, time.min)
#         end_datetime = datetime.combine(filter_date, time.max)

#         docs = LegalDocument.objects.select_related('consumer', 'notary').filter(
#             created_at__range=(start_datetime, end_datetime),
#             notary_id=notary_id
#         )

#         result = []
#         for doc in docs:
#             result.append({
#                 'id': doc.id,
#                 'document_number': doc.document_number,
#                 'document_time': doc.document_time.strftime('%H:%M') if doc.document_time else '',
#                 'consumer_id': doc.consumer.id if doc.consumer else None
#             })

#         if result:
#             return Response(result)

#         return Response({'message': 'Tidak ada dokumen untuk notaris ini pada tanggal tersebut.'}, status=404)

class LegalDocumentUpsertAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @class_permission_required('create-akta')
    def post(self, request):
        legal_doc_id = request.data.get('id')  # pastikan dikirim dari JS
        document_number = request.data.get('document_number')
        document_time = request.data.get('document_time')  # format "HH:MM"

        if not legal_doc_id:
            return Response({'error': 'ID dokumen tidak ditemukan.'}, status=400)

        try:
            doc = LegalDocument.objects.get(id=legal_doc_id)

            # Update nomor dan waktu
            doc.document_number = document_number

            if document_time:
                hour, minute = map(int, document_time.split(':'))
                doc.document_time = time(hour=hour, minute=minute)

            doc.save()

            return Response({'message': 'LegalDocument berhasil diperbarui'})
        except LegalDocument.DoesNotExist:
            return Response({'error': 'LegalDocument tidak ditemukan'}, status=404)
        except Exception as e:
            return Response({'error': str(e)}, status=500)

class AssignListCreateAPIView(LoginRequiredMixin, View):
    login_url = '/login/'

    @class_permission_required('read-akta')
    def get(self, request):
        page_size = min(int(request.GET.get('initial', 100)), 500)

        latest_doc = (
            LegalDocument.objects
            .filter(consumer_id=OuterRef('pk'))
            .order_by('-document_date', '-id')
        )

        consumers = (
            Consumer.objects
            .filter(st_process='1')
            .annotate(
                latest_doc_id=Subquery(latest_doc.values('id')[:1]),
                latest_doc_number=Subquery(latest_doc.values('document_number')[:1]),
                latest_doc_date=Subquery(latest_doc.values('document_date')[:1]),
                latest_doc_time=Subquery(latest_doc.values('document_time')[:1]),
            )
            .only('id', 'debtor_name', 'agreement_number')
            .order_by('-latest_doc_date', '-id')[:page_size]
        )

        branches = Branch.objects.filter(
            consumer__uploader=request.user
        ).only('id','name').distinct()

        user_permissions = list(get_user_permissions(request.user))
        user_roles = list(get_user_role_names(request.user))

        roles_lower = [r.lower() for r in user_roles]
        perms_lower = [p.lower() for p in user_permissions]

        return render(request, 'operator/page-operator-assignakta.html', {
            'consumers': consumers,
            'branches': branches,
            'user_permissions': perms_lower,
            'user_roles': roles_lower,
            'is_notaris': 'notaris' in roles_lower,
            'is_warkah': 'warkah' in roles_lower,
            'data_list': consumers.count(),
        })

    @class_permission_required('create-akta')
    def post(self, request):
        data = request.POST.copy()

        for k in ('deed_number', 'deed_date', 'deed_documents'):
            data.pop(k, None)

        serializer = ConsumerSerializer(data=data, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return redirect('operator-assign-akta')

        return render(request, 'operator/page-operator-assignakta.html', {
            'errors': serializer.errors
        })

class AssignDataJSON(APIView):
    permission_classes = [IsAuthenticated]

    @class_permission_required('read-akta')
    def get(self, request):
        def _to_int(v, d):
            try:
                return int(v)
            except:
                return d

        draw   = _to_int(request.GET.get('draw', 1), 1)
        start  = _to_int(request.GET.get('start', 0), 0)
        length = _to_int(request.GET.get('length', 50), 50)
        length = min(max(length, 1), 500)

        notary_name = (request.GET.get('notary_name') or '').strip()
        mock_date   = request.GET.get('mock_date')
        search_val  = (request.GET.get('search') or '').strip()

        order_col   = (request.GET.get('order_col') or 'id').strip()
        order_dir   = (request.GET.get('order_dir') or 'desc').lower()
        ORDER_MAP = {
            'id': 'id',
            'director_notary_name': 'notary__firm_name',
            'agreement_number': 'consumer__agreement_number',
            'debtor_name': 'consumer__debtor_name',
            'deed_number': 'document_number',
            'deed_date': 'document_date',
            'document_time': 'document_time',
            'voucher_code': 'voucher_code',
            'certificate_number': 'certificate_number',
            'certificate_date': 'certificate_date',
        }
        order_field = ORDER_MAP.get(order_col, 'id')
        if order_dir == 'desc':
            order_field = '-' + order_field

        user_roles = [r.lower() for r in get_user_role_names(request.user)]

        qs_scope = (
            LegalDocument.objects
            .select_related('consumer', 'notary')
            .filter(consumer__st_process='1')
        )

        is_notary = any(role_name in ['notaris', 'notary'] for role_name in user_roles)
        if is_notary:
            qs_scope = qs_scope.filter(notary__user=request.user)
        elif _user_is_staff_cabang(request.user):
            user_branches = _user_branch_codes(request.user)
            if not user_branches:
                qs_scope = qs_scope.none()
            else:
                qs_scope = qs_scope.filter(consumer__branch__branch_code__in=user_branches)

        records_total = qs_scope.count()

        qs_base = qs_scope

        if notary_name:
            qs_base = qs_base.filter(notary__firm_name__icontains=notary_name)

        if mock_date:
            qs_base = qs_base.filter(document_date__lte=mock_date)

        if search_val:
            qs_base = qs_base.filter(
                Q(notary__firm_name__icontains=search_val) |
                Q(consumer__agreement_number__icontains=search_val) |
                Q(consumer__debtor_name__icontains=search_val) |
                Q(document_number__icontains=search_val) |
                Q(voucher_code__icontains=search_val) |
                Q(certificate_number__icontains=search_val)
            )

        records_filtered = qs_base.count()

        qs_page = (
            qs_base
            .order_by(order_field, '-id')
            .values(
                'id',
                'voucher_code',
                'certificate_number',
                'certificate_date',
                'document_number',
                'document_date',
                'document_time',
                'consumer__id',
                'consumer__agreement_number',
                'consumer__debtor_name',
                'notary__firm_name',
            )[start:start + length]
        )

        data = []
        for row in qs_page:
            data.append({
                'id': row['consumer__id'],
                'director_notary_name': row['notary__firm_name'] or '',
                'agreement_number': row['consumer__agreement_number'] or '',
                'debtor_name': row['consumer__debtor_name'] or '',
                'deed_number': row['document_number'] or '',
                'deed_date': (row['document_date'] and row['document_date'].strftime('%Y-%m-%d')) or '',
                'document_time': (row['document_time'] and row['document_time'].strftime('%H:%M')) or '',
                'voucher_code': row['voucher_code'] or '',
                'certificate_number': row['certificate_number'] or '',
                'certificate_date': (row['certificate_date'] and row['certificate_date'].strftime('%Y-%m-%d')) or '',
            })

        return JsonResponse({
            'draw': draw,
            'recordsTotal': records_total,
            'recordsFiltered': records_filtered,
            'data': data,
        }, safe=False)

class AssignDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, pk):
        return get_object_or_404(Consumer, pk=pk)

    @class_permission_required('read-akta')
    def get(self, request, pk):
        consumer = self.get_object(pk)
        if not _consumer_belongs_to_user_branches(consumer, request.user):
            return Response({"detail": "Forbidden branch scope."}, status=status.HTTP_403_FORBIDDEN)
        serializer = ConsumerSerializer(consumer)
        return Response(serializer.data)

    @class_permission_required('update-akta')
    def put(self, request, pk):
        consumer = self.get_object(pk)
        if not _consumer_belongs_to_user_branches(consumer, request.user):
            return Response({"detail": "Forbidden branch scope."}, status=status.HTTP_403_FORBIDDEN)
        serializer = ConsumerSerializer(consumer, data=request.data, context={'request': request}, partial=True)
        if serializer.is_valid():
            updated_consumer = serializer.save()
            return Response(ConsumerSerializer(updated_consumer).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @class_permission_required('delete-akta')
    def delete(self, request, pk):
        try:
            consumer = self.get_object(pk)
            if not _consumer_belongs_to_user_branches(consumer, request.user):
                return Response({"detail": "Forbidden branch scope."}, status=status.HTTP_403_FORBIDDEN)
            consumer.delete()

            ActivityLog.objects.create(
                log_name='CONSUMER_UPLOAD',
                description='deleted',
                causer=request.user,
                causer_type='AdminUser.User',
                subject_id=pk,
                subject_type='Consumer',
                properties={"message": "Consumer deleted."}
            )

            return Response({"detail": "Consumer deleted."}, status=status.HTTP_204_NO_CONTENT)
        except ProtectedError:
            return Response({"error": "Tidak bisa hapus karena masih ada data terkait."}, status=status.HTTP_400_BAD_REQUEST)
        
def to_int_safe(v):
    """Ubah nilai ke angka bulat tanpa error. Kosong → 0, desimal dibulatkan."""
    try:
        if v in (None, '', 0):
            return 0
        return int(round(float(v)))
    except (ValueError, TypeError):
        return 0

def fmt_ddmmyyyy(v):
    if not v:
        return ""
    if isinstance(v, datetime):
        v = v.date()
    if isinstance(v, date):
        return v.strftime("%d-%m-%Y")
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return ""
        # Tambahkan format ISO & varian umum
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%m/%d/%Y"):
            try:
                return datetime.strptime(v, fmt).strftime("%d-%m-%Y")
            except ValueError:
                pass
        return ""  # fallback: tidak bisa parse → kosong
    return ""

@login_required(login_url='')
@permission_required('read-akta')
@require_GET
def export_excel_by_date_range(request):
    start_date = request.GET.get('start_date')
    end_date   = request.GET.get('end_date')

    if not start_date or not end_date:
        return HttpResponse("Parameter 'start_date' dan 'end_date' wajib diisi.", status=400)

    try:
        start_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_obj   = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        return HttpResponse("Format tanggal tidak valid. Gunakan YYYY-MM-DD.", status=400)

    if start_obj > end_obj:
        return HttpResponse("Rentang tanggal tidak valid: start_date > end_date.", status=400)

    qs = (
        LegalDocument.objects
        .filter(document_date__range=(start_obj, end_obj))
        .select_related(
            'notary',
            'business_entity',
            'business_entity_director',
            'consumer',
            'power_user',
        )
        .order_by('id')
    )

    user_roles = [r.lower() for r in get_user_role_names(request.user)]
    if 'notaris' in user_roles or 'notary' in user_roles:
        qs = qs.filter(notary__user=request.user)
    qs = _scope_legal_documents_to_user_branches(qs, request.user)

    if not qs.exists():
        return HttpResponse("Tidak ada data di rentang tanggal tersebut.", status=404)

    def g(obj, path, default=""):
        """
        Safe getter: g(d, 'consumer.full_name')
        """
        cur = obj
        for p in path.split('.'):
            if cur is None:
                return default
            cur = getattr(cur, p, None)
        return cur if cur is not None else default

    def join_normal(*parts):
        return " ".join(str(p).strip() for p in parts if p).strip()

    rows = []
    for d in qs:
        cof   = getattr(d, 'consumer', None)
        be    = getattr(d, 'business_entity', None)
        bedir = getattr(d, 'business_entity_director', None)
        user  = getattr(d, 'power_user', None)
        consumer_id = getattr(d, 'consumer_id', None)

        jenis_nasabah       = g(cof, 'customer_type')
        pemberi_fidusia     = g(cof, 'fiducia_provider')
        nama_pada_bpkb      = g(cof, 'name_on_bpkb')
        nama_debitur        = g(cof, 'debtor_name')
        tgl_lahir_bpkb      = g(cof, 'bpkb_birth_date')
        tempat_lahir_bpkb   = g(cof, 'bpkb_birth_place')
        pekerjaan_bpkb      = g(cof, 'bpkb_occupation')
        kewarganegaraan_bpkb = g(cof, 'bpkb_nationality')
        alamat_bpkb         = g(cof, 'bpkb_address')
        rt_bpkb             = g(cof, 'bpkb_rt')
        rw_bpkb             = g(cof, 'bpkb_rw')
        kelurahan_bpkb      = g(cof, 'bpkb_subdistrict')
        kecamatan_bpkb      = g(cof, 'bpkb_district')
        kabupaten_bpkb      = g(cof, 'bpkb_city')
        provinsi_bpkb       = g(cof, 'bpkb_province')
        kode_pos_bpkb       = g(cof, 'bpkb_postal_code')
        ktp_bpkb            = g(cof, 'bpkb_ktp')
        rt_rw               = g(cof, 'rt_rw')
        rt_rw_pj            = g(cof, 'director_rt_rw')
        tenor               = g(cof, 'tenor')
        tgl_awal_cicilan    = g(cof, 'go_live_date')
        tgl_akhir_cicilan    = g(cof, 'installment_end_date')
        nilai_hutang_pokok = to_int_safe(getattr(cof, 'principal_loan', None))

        nilai_penjaminan    = to_int_safe(getattr(cof, 'guarantee_value', None))
        nilai_objek_otr     = to_int_safe(getattr(cof, 'otr_value', None))

        kondisi_kendaraan   = g(cof, 'vehicle_condition')
        merk                = g(cof, 'brand')
        tipe_raw            = g(cof, 'type')
        model               = g(cof, 'model')
        tipe_gabungan       = join_normal(tipe_raw, model)
        warna               = g(cof, 'color')
        tahun_perakitan     = g(cof, 'assembly_year')
        tahun_pembuatan     = g(cof, 'production_year')
        no_rangka           = g(cof, 'chassis_number')
        no_mesin            = g(cof, 'engine_number')
        no_bpkb             = g(cof, 'bpkb_number')

        jenis_perjanjian    = g(cof, 'agreement_type')
        nama_perjanjian     = g(cof, 'agreement_name')
        no_perjanjian       = g(cof, 'agreement_number')
        tgl_perjanjian      = g(cof, 'agreement_date')
        tgl_perjanjian      = g(cof, 'agreement_date')
        nama_cabang         = g(cof, 'branch.branch_code') or g(cof, 'branch.code')

        row = {
            'id'                    : consumer_id,
            'Notaris'               : d.notary.firm_name if getattr(d, 'notary', None) else "",
            'nama_cabang'           : nama_cabang,
            'jenis_nasabah'         : jenis_nasabah,
            'pemberi_fidusia'       : pemberi_fidusia,
            'nama_pada_bpkb'        : nama_pada_bpkb,
            'nama_debitur'          : nama_debitur,
            'tgl_lahir_bpkb'        : g(cof, 'bpkb_birth_date'),
            'tempat_lahir_bpkb'     : g(cof, 'bpkb_birth_place'),
            'pekerjaan_bpkb'        : g(cof, 'bpkb_occupation'),
            'kewarganegaraan_bpkb'  : g(cof, 'bpkb_nationality'),
            'alamat_bpkb'           : g(cof, 'bpkb_address'),
            'rt_bpkb'               : g(cof, 'bpkb_rt'),
            'rw_bpkb'               : g(cof, 'bpkb_rw'),
            'kelurahan_bpkb'        : g(cof, 'bpkb_subdistrict'),
            'kecamatan_bpkb'        : g(cof, 'bpkb_district'),
            'kabupaten_bpkb'        : g(cof, 'bpkb_city'),
            'provinsi_bpkb'         : g(cof, 'bpkb_province'),
            'kode_pos_bpkb'         : g(cof, 'bpkb_postal_code'),
            'ktp_bpkb'              : g(cof, 'bpkb_ktp'),
            'jenis_kelamin'         : g(cof, 'gender'),
            'status_pernikahan'     : g(cof, 'marital_status'),
            'tmp_lahir'             : g(cof, 'birth_place'),
            'tgl_lahir'             : g(cof, 'birth_date'),
            'pekerjaan'             : g(cof, 'occupation'),
            'kewarganegaraan'       : g(cof, 'nationality'),
            'jenis_identitas'       : g(cof, 'id_type'),
            'no_identitas'          : g(cof, 'id_number'),
            'alamat'                : g(cof, 'address'),
            'rt_rw'                 : rt_rw,
            'kelurahan'             : g(cof, 'subdistrict'),
            'kecamatan'             : g(cof, 'district'),
            'kota'                  : g(cof, 'city'),
            'provinsi'              : g(cof, 'province'),
            'kode_pos'              : g(cof, 'postal_code'),
            'tgl_upload'            : g(cof, 'created_at'),

            'jenis_badan'           : g(cof, 'company_type'),
            'npwp_badan'            : g(cof, 'company_npwp'),
            'no_akta_badan'         : g(cof, 'deed_number'),
            'tgl_akta_badan'        : g(cof, 'deed_date'),
            'notaris_badan'         : g(cof, 'director_notary_name'),
            'kedudukan_notaris'     : g(cof, 'notary_city'),

            'nama_pj_badan'         : g(cof, 'company_director_name'),
            'jk_pj_badan'           : g(cof, 'director_gender'),
            'status_pj_badan'       : g(cof, 'director_status'),
            'tempat_lahir_pj_badan' : g(cof, 'director_birth_place'),
            'tgl_lahir_pj_badan'    : g(cof, 'director_birth_date'),
            'pekerjaan_pj_badan'    : g(cof, 'director_occupation'),
            'kewarganegaraan_pj_badan': g(cof, 'director_nationality'),
            'typeid_pj_badan'       : g(cof, 'director_id_type'),
            'noid_pj_badan'         : g(cof, 'director_id_number'),
            'alamat_pj_badan'       : g(cof, 'director_address'),
            'rt_rw_pj_badan'        : rt_rw_pj,
            'kelurahan_pj_badan'    : g(cof, 'director_subdistrict'),
            'kecamatan_pj_badan'    : g(cof, 'director_district'),
            'kota_pj_badan'         : g(cof, 'director_city'),
            'provinsi_pj_badan'     : g(cof, 'director_province'),
            'jabatan_pj_badan'      : g(cof, 'director_position'),

            'jenis_perjanjian'      : jenis_perjanjian,
            'nama_perjanjian'       : nama_perjanjian,
            'no_perjanjian'         : no_perjanjian,
            'tgl_perjanjian'        : tgl_perjanjian,

            'nilai_hutang_pokok'    : nilai_hutang_pokok,
            'nilai_penjaminan'      : nilai_penjaminan,
            'tgl_awal_cicilan'      : tgl_awal_cicilan,
            'tgl_akhir_cicilan'      : tgl_akhir_cicilan,
            'tenor'                 : tenor,

            'kondisi_kendaraan'     : kondisi_kendaraan,
            'merk'                  : merk,
            'tipe'                  : tipe_gabungan,
            'warna'                 : warna,
            'tahun_perakitan'       : tahun_perakitan,
            'tahun_pembuatan'       : tahun_pembuatan,
            'no_rangka'             : no_rangka,
            'no_mesin'              : no_mesin,
            'no_bpkb'               : no_bpkb,
            'nilai_objek_otr'       : nilai_objek_otr,

            'biaya_pnbp'            : getattr(d, 'pnbp_fee', None),
            'biaya_jasa'            : getattr(d, 'code_value', None),

            'no_akta'               : getattr(d, 'document_number', None),
            'tgl_akta'              : getattr(d, 'document_date', None),
            'jam_akta'              : (
                d.document_time.strftime('%H:%M') if d.document_time else ''
            ),
            'kd_voucher'            : getattr(d, 'voucher_code', None),
            'no_sertifikat'         : getattr(d, 'certificate_number', None),
            'tanggal_sertifikat'    : getattr(d, 'certificate_date', None),

            'nama': getattr(getattr(d, 'power_user', None), 'full_name', None)
        }

        date_fields = [
            'tgl_lahir_bpkb',
            'tgl_akta_badan',
            'tgl_lahir_pj_badan',
            'tgl_perjanjian',
            'tgl_awal_cicilan',
            'tgl_akhir_cicilan',
            'tgl_akta',
            'tanggal_sertifikat',
            'tgl_lahir',
            'tgl_upload',
        ]

        for k in date_fields:
            row[k] = fmt_ddmmyyyy(row.get(k))

        rows.append(row)

    headers = [
        'id','Notaris','nama_cabang','jenis_nasabah','pemberi_fidusia','nama_pada_bpkb','nama_debitur',
        'tgl_lahir_bpkb','tempat_lahir_bpkb','pekerjaan_bpkb','kewarganegaraan_bpkb','alamat_bpkb',
        'rt_bpkb','rw_bpkb','kelurahan_bpkb','kecamatan_bpkb','kabupaten_bpkb','provinsi_bpkb','kode_pos_bpkb','ktp_bpkb',
        'jenis_kelamin','status_pernikahan','tmp_lahir','tgl_lahir','pekerjaan','kewarganegaraan',
        'jenis_identitas','no_identitas','alamat','rt_rw','kelurahan','kecamatan','kota','provinsi','kode_pos',
        'jenis_badan','npwp_badan','no_akta_badan','tgl_akta_badan', 'notaris_badan', 'kedudukan_notaris',
        'nama_pj_badan','jk_pj_badan','status_pj_badan','tempat_lahir_pj_badan','tgl_lahir_pj_badan', 'pekerjaan_pj_badan',
        'kewarganegaraan_pj_badan','typeid_pj_badan','noid_pj_badan','alamat_pj_badan','rt_rw_pj_badan',
        'kelurahan_pj_badan','kecamatan_pj_badan','kota_pj_badan','provinsi_pj_badan','jabatan_pj_badan',
        'jenis_perjanjian','nama_perjanjian','no_perjanjian','tgl_perjanjian',
        'nilai_hutang_pokok','nilai_penjaminan','tgl_awal_cicilan','tgl_akhir_cicilan','tenor',
        'kondisi_kendaraan','merk','tipe','warna','tahun_perakitan','tahun_pembuatan','no_rangka','no_mesin',
        'no_bpkb','nilai_objek_otr',
        'biaya_pnbp','biaya_jasa','no_akta','tgl_akta', 'jam_akta','kd_voucher','no_sertifikat','tanggal_sertifikat','nama', 'tgl_upload'
    ]

    df = pd.DataFrame(rows)
    for h in headers:
        if h not in df.columns:
            df[h] = ""
    df = df.reindex(columns=headers)

    buf = BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)

    wb = load_workbook(buf)
    ws = wb.active

    for cell in ws[1]:
        cell.font = Font(bold=False)
        cell.alignment = Alignment(horizontal='left', vertical='center')
        cell.border = Border()

    ws.sheet_view.showGridLines = True

    for col_idx in range(1, ws.max_column + 1):
        col_letter = get_column_letter(col_idx)
        max_len = 0
        for cell in ws[col_letter]:
            val = "" if cell.value is None else str(cell.value)
            max_len = max(max_len, len(val))
        ws.column_dimensions[col_letter].width = max(10, min(max_len + 2, 60))

    final_output = BytesIO()
    wb.save(final_output)
    final_output.seek(0)

    filename = f"Export_Akta_{start_date}_-_{end_date}.xlsx"

    resp = HttpResponse(
        final_output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp

@login_required(login_url='')
@permission_required('read-akta')
@require_GET
def export_template_filtered(request):
    start_date = request.GET.get('start_date')
    end_date   = request.GET.get('end_date')

    if not start_date or not end_date:
        return HttpResponse("Parameter 'start_date' dan 'end_date' wajib diisi.", status=400)

    try:
        start_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_obj   = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        return HttpResponse("Format tanggal tidak valid. Gunakan YYYY-MM-DD.", status=400)

    if start_obj > end_obj:
        return HttpResponse("Rentang tanggal tidak valid: start_date > end_date.", status=400)

    qs = (
        LegalDocument.objects
        .filter(document_date__range=(start_obj, end_obj))
        .select_related(
            'notary',
            'business_entity',
            'business_entity_director',
            'consumer',
            'power_user',
            'recipient',  # <— sekarang bisa, karena model sudah dipetakan ke recipient_id
        )
        .order_by('id')
    )
    qs = _scope_legal_documents_to_user_branches(qs, request.user)
    if not qs.exists():
        return HttpResponse("Tidak ada data di rentang tanggal tersebut.", status=404)

    def g(obj, path, default=""):
        cur = obj
        for p in path.split('.'):
            if cur is None:
                return default
            cur = getattr(cur, p, None)
        return cur if cur is not None else default

    def join_normal(*parts):
        return " ".join(str(p).strip() for p in parts if p).strip()

    rows = []
    for idx, d in enumerate(qs, start=1):
        cof   = getattr(d, 'consumer', None)
        rec   = getattr(d, 'recipient', None)  # <— hasil join
        be    = getattr(d, 'business_entity', None)
        bedir = getattr(d, 'business_entity_director', None)
        user  = getattr(d, 'power_user', None)

        # ————— DATA PENERIMA (Recipient) —————
        sk_penerima          = g(rec, 'ahu_decree_number') or g(rec, 'decree_number')
        email                = g(rec, 'email')
        badan_hukum_penerima = g(rec, 'business_entity_name')
        province             = g(rec, 'province')
        city                 = g(rec, 'city')
        district             = g(rec, 'district')
        subdistrict          = g(rec, 'subdistrict')
        rt                   = g(rec, 'rt')
        rw                   = g(rec, 'rw')
        address              = g(rec, 'address')
        postal_code          = g(rec, 'postal_code')
        phone_number         = g(rec, 'phone_number')
        npwp_penerima        = g(rec, 'tax_number')

        # ————— DATA LAIN (Consumer/Vehicle/Agreement) —————
        jenis_nasabah       = g(cof, 'customer_type')
        nama_debitur        = g(cof, 'debtor_name')
        address_con         = g(cof, 'address')
        rt_con              = g(cof, 'rt')
        rw_con              = g(cof, 'rw')
        subdistrict_con     = g(cof, 'subdistrict')
        district_con        = g(cof, 'district')
        city_con            = g(cof, 'city')
        province_con        = g(cof, 'province')
        postal_code_con     = g(cof, 'postal_code')
        nilai_hutang_pokok  = to_int_safe(getattr(cof, 'principal_loan', None))
        nilai_penjaminan    = to_int_safe(getattr(cof, 'guarantee_value', None))
        nilai_objek_otr     = to_int_safe(getattr(cof, 'otr_value', None))

        merk                = g(cof, 'brand')
        tipe_raw            = g(cof, 'type')
        model               = g(cof, 'model')
        tipe_gabungan       = join_normal(tipe_raw, model)
        no_rangka           = g(cof, 'chassis_number')
        no_mesin            = g(cof, 'engine_number')

        jenis_perjanjian     = g(cof, 'agreement_type')
        no_perjanjian        = g(cof, 'agreement_number')
        tgl_perjanjian       = g(cof, 'agreement_date')
        go_live              = g(cof, 'go_live_date')
        tgl_perjanjian_akhir = g(cof, 'installment_end_date')
        jenis_kelamin        = g(cof, 'gender')
        
        nama_pemberi         = g(cof, 'name_on_bpkb' )
        provinsi_pemberi     = g(cof, 'bpkb_province')
        kota_pemberi         = g(cof, 'bpkb_city')
        nik_npwp_pemberi     = g(cof, 'id_number') 
        nik_npwp_pemberi_con = g(cof, 'bpkb_ktp') 
        npwp_pemberi         = g(cof, 'npwp_provider') #personal dari nik_npwp_pemberi tapi kalau badan dari company_npwp
        kecamatan_pemberi    = g(cof, 'bpkb_district')
        kelurahan_pemberi    = g(cof, 'bpkb_subdistrict')
        rt_pemberi           = g(cof, 'bpkb_rt')
        rw_pemberi           = g(cof, 'bpkb_rw')
        alamat_pemberi       = g(cof, 'bpkb_address')
        no_hp_pemberi        = g(cof, 'phone_number')
        kode_pos_pemberi     = g(cof, 'bpkb_postal_code')

        mapping_penggunaan = {
            'I': 'konsumtif',
            'C': 'produktif'
        }
        penggunaan = mapping_penggunaan.get(jenis_nasabah, 'unknown')
        
        mapping_jnsklmn = {
            'Female': 'Perempuan',
            'FEMALE': 'Perempuan',
            'Male': 'Laki - Laki',
            'MALE': 'Laki - Laki'
        }
        gender = mapping_jnsklmn.get(jenis_kelamin, 'unknown')
        
        mapping_pemberi = {
            'I': 'perorangan',
            'C': 'badan usaha'
        }
        pemberi = mapping_pemberi.get(jenis_nasabah, 'unknown')
        
        mapping_jenis = {
            'KONVE': 'Pembiayaan secara Fidusia',
            'SYARIAH': 'Pembiayaan Syariah dengan akad murabahah'
        }
        jenis = mapping_jenis.get(jenis_perjanjian, 'unknown')

        row = {
            'no'                    : "1",
            'penggunaan'            : penggunaan,
            'skala usaha'           : "kecil", 

            # — penerima (Recipient)
            'penerima fidusia'      : "Badan Usaha",
            'jenis usaha penerima'  : "Lembaga Keuangan Bukan Bank",
            'SK penerima'           : sk_penerima,
            'cabang penerima'       : "",
            'email penerima'        : email,
            'badan hukum penerima'  : "Perseroan Persekutuan Modal (PT)",
            'nama penerima'         : badan_hukum_penerima,
            'provinsi penerima'     : province,
            'kota penerima'         : city,
            'kecamatan penerima'    : district,
            'kelurahan penerima'    : subdistrict,
            'rw penerima'           : rw,
            'rt penerima'           : rt,
            'alamat penerima'       : address,
            'kode pos penerima'     : postal_code,
            'nik npwp penerima'     : npwp_penerima,  # <— isi dari Recipient.tax_number
            'no hp penerima'        : phone_number,

            # — akta
            'no akta'               : getattr(d, 'document_number', None),
            'tgl akta'              : getattr(d, 'document_date', None),

            # — pemberi (belum ada mapping di model ini)
            'pemberi fidusia'       : pemberi,
            'jenis kelamin pemberi' : gender,
            'nama pemberi'          : nama_pemberi,
            'provinsi pemberi'      : provinsi_pemberi,
            'kota pemberi'          : kota_pemberi,
            'nik npwp pemberi'      : nik_npwp_pemberi,
            'npwp pemberi'          : npwp_pemberi,
            'kecamatan pemberi'     : kecamatan_pemberi,
            'kelurahan pemberi'     : kelurahan_pemberi,
            'rt pemberi'            : rt_pemberi,
            'rw pemberi'            : rw_pemberi,
            'alamat pemberi'        : alamat_pemberi,
            'no hp pemberi'         : no_hp_pemberi,
            'kode pos pemberi'      : kode_pos_pemberi,

            # — debitur & objek
            'nama debitur'          : nama_debitur,
            'roda'                  : "4",
            'merk'                  : merk,
            'tipe'                  : tipe_gabungan,
            'no rangka'             : no_rangka,
            'no mesin'              : no_mesin,
            'bukti obyek'           : (f"Surat Pernyataan Bersama Tanggal {tgl_perjanjian.day:02d} {BULAN_ID[tgl_perjanjian.month]} {tgl_perjanjian.year}"if tgl_perjanjian else ""),
            'nilai obyek'           : nilai_objek_otr,
            'jumlah hutang'         : nilai_hutang_pokok,

            # — perjanjian
            'nomor perjanjian'      : no_perjanjian,
            'nilai penjaminan'      : nilai_penjaminan,
            'tgl perjanjian awal'   : go_live,
            'tgl perjanjian akhir'  : tgl_perjanjian_akhir,
            'jenis perjanjian'      : jenis,
            'tgl perjanjian'        : tgl_perjanjian,
            'metode pembayaran'     : "BNI",

            # — notaris & id
            'nama notaris'          : d.notary.full_name if getattr(d, 'notary', None) else "",
            'id'                    : d.id,
        }
            
        def norm_str(s=None):
            return re.sub(r'\s+', ' ', (s or '')).strip().lower()

        # ————— Validasi sesuai fiducia_provider —————
        provider = norm_str(g(cof, 'fiducia_provider'))

        # if provider == 'atas nama customer':
        #     # Ketentuan: nama pemberi diisi dengan nama debitur
        #     row['nama pemberi'] = nama_debitur or row.get('nama pemberi', "")
        #     # Kosongkan nama debitur agar tidak ganda
        #     row['nama debitur'] = ""
        
        if provider == 'atas nama customer':
            # Ketentuan: nama pemberi diisi dengan nama debitur
            row['nama pemberi'] = nama_debitur

            # Ambil seluruh field alamat dari data debitur
            row['alamat pemberi'] = address_con
            row['rt pemberi'] = rt_con
            row['rw pemberi'] = rw_con
            row['kelurahan pemberi'] = subdistrict_con
            row['kecamatan pemberi'] = district_con
            row['kota pemberi'] = city_con
            row['provinsi pemberi'] = province_con
            row['kode pos pemberi'] = postal_code_con
            row['nik npwp pemberi'] = nik_npwp_pemberi
            row['npwp pemberi'] = nik_npwp_pemberi

            # Kosongkan data debitur agar tidak ganda
            row['nama debitur'] = ""

        elif provider == 'atas nama bpkb':
            # Pastikan semua field pemberi terisi (fallback ke "")
            wajib_pemberi = {
                'jenis kelamin pemberi': gender,
                'nama pemberi'         : nama_pemberi,
                'provinsi pemberi'     : provinsi_pemberi,
                'kota pemberi'         : kota_pemberi,
                'nik npwp pemberi'     : nik_npwp_pemberi_con,
                'npwp pemberi'         : nik_npwp_pemberi_con,
                'kecamatan pemberi'    : kecamatan_pemberi,
                'kelurahan pemberi'    : kelurahan_pemberi,
                'rt pemberi'           : rt_pemberi,
                'rw pemberi'           : rw_pemberi,
                'alamat pemberi'       : alamat_pemberi,
                'no hp pemberi'        : no_hp_pemberi,
                'kode pos pemberi'     : kode_pos_pemberi,
            }
            for k, v in wajib_pemberi.items():
                row[k] = v if v is not None else ""

            # Normalisasi untuk pembanding
            nama_pemberi_norm = norm_str(nama_pemberi)
            nama_debitur_norm = norm_str(nama_debitur)

            # === Logika pengisian nama debitur ===
            if nama_debitur_norm and nama_pemberi_norm:
                if nama_debitur_norm != nama_pemberi_norm:
                    # Jika beda → isi nama debitur apa adanya
                    row['nama debitur'] = nama_debitur or row.get('nama debitur', "")
                else:
                    # Jika sama → kosongkan nama debitur (cukup nama pemberi saja)
                    row['nama debitur'] = ""
            else:
                # Jika salah satu kosong → gunakan nama pemberi sebagai fallback
                row['nama debitur'] = nama_pemberi or row.get('nama debitur', "")

        else:
            # Provider lain: biarkan apa adanya (atau sesuaikan aturan lain jika ada)
            row['nama debitur'] = row.get('nama debitur', "")
                
        date_key_map = {
            'tgl_akta'            : 'tgl akta',
            'tgl_perjanjian'      : 'tgl perjanjian',
            'tgl_awal_cicilan'    : 'tgl perjanjian awal',
            'tgl_akhir_cicilan'   : 'tgl perjanjian akhir',
            'tanggal_sertifikat'  : 'tanggal sertifikat',
            'tgl_lahir'           : 'tgl lahir',
            'tgl_lahir_bpkb'      : 'tgl lahir bpkb',
            'tgl_akta_badan'      : 'tgl akta badan',
            'tgl_lahir_pj_badan'  : 'tgl lahir pj badan',
        }
        for src, dst in date_key_map.items():
            if dst in row:
                row[dst] = fmt_ddmmyyyy(row.get(dst))

        rows.append(row)

    headers = [
        'no','penggunaan','skala usaha','penerima fidusia','jenis usaha penerima','SK penerima','cabang penerima',
        'email penerima','badan hukum penerima','nama penerima','provinsi penerima','kota penerima','kecamatan penerima',
        'kelurahan penerima','rw penerima','rt penerima','alamat penerima','kode pos penerima','nik npwp penerima','no hp penerima','no akta','tgl akta',
        'pemberi fidusia','jenis kelamin pemberi','nama pemberi','provinsi pemberi',
        'kota pemberi','nik npwp pemberi','npwp pemberi','kecamatan pemberi','kelurahan pemberi',
        'rt pemberi','rw pemberi','alamat pemberi','no hp pemberi','kode pos pemberi',
        'nama debitur','roda','merk','tipe','no rangka',
        'no mesin','bukti obyek','nilai obyek','jumlah hutang',
        'nomor perjanjian','nilai penjaminan','tgl perjanjian awal','tgl perjanjian akhir',
        'jenis perjanjian','tgl perjanjian','metode pembayaran','nama notaris','id'
    ]

    df = pd.DataFrame(rows)
    for h in headers:
        if h not in df.columns:
            df[h] = ""
    df = df.reindex(columns=headers)

    filename = f"Export_Akta_{start_date}_-_{end_date}.xlsx"

    buf = BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)

    wb = load_workbook(buf)
    ws = wb.active

    for cell in ws[1]:
        cell.font = Font(bold=False)
        cell.border = Border()

    ws.sheet_view.showGridLines = True

    for col_idx in range(1, ws.max_column + 1):
        col_letter = get_column_letter(col_idx)
        max_len = 0
        for cell in ws[col_letter]:
            val = "" if cell.value is None else str(cell.value)
            max_len = max(max_len, len(val))
        ws.column_dimensions[col_letter].width = max(10, min(max_len + 2, 60))

    final_output = BytesIO()
    wb.save(final_output)
    final_output.seek(0)

    resp = HttpResponse(
        final_output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp

class AssignDownloadAktaDoc(APIView):
    permission_classes = [IsAuthenticated]

    # ========================= Helper Methods (Private) ========================= #
    def _permission_for_akta(self, akta_type: AktaType):
        return {
            AktaType.REGULER: 'read-akta',
            AktaType.MU: 'read-akta-mu',
            AktaType.OC: 'read-akta-oc',
            AktaType.PF: 'read-akta-pf',
        }.get(akta_type, 'read-akta')

    def _get_user_branch_code(self, user):
        if getattr(user, 'branch', None):
            return getattr(user.branch, 'branch_code', None)
        return getattr(user, 'branch_code', None) or getattr(user, 'branch_id', None)

    def _get_consumer_branch_code(self, consumer):
        if getattr(consumer, 'branch', None):
            return getattr(consumer.branch, 'branch_code', None)
        return getattr(consumer, 'branch_code', None) or getattr(consumer, 'branch_id', None)

    def _can_access_consumer(self, user, consumer):
        role_names = list(get_user_role_names(user))
        if (
            has_role(role_names, ROLE_KEYS["SUPERADMIN"])
            or has_role(role_names, ROLE_KEYS["HO"])
            or has_role(role_names, ROLE_KEYS["OPERATOR"])
            or has_role(role_names, ROLE_KEYS["WARKAH"])
        ):
            return True
        user_branches = _user_branch_codes(user)
        consumer_branch = self._get_consumer_branch_code(consumer)
        if not user_branches or not consumer_branch:
            return False
        return str(consumer_branch) in {str(branch) for branch in user_branches}

    def _get_models_and_instances(self, akta_type: AktaType, id: int):
        consumer_model = get_consumer_model(akta_type)
        legal_doc_model = get_document_model(akta_type)
        consumer = get_object_or_404(consumer_model, pk=id)
        legal_doc = get_object_or_404(legal_doc_model, consumer=consumer)
        vehicles = None
        if akta_type == AktaType.PF:
            agreement_number = consumer.new_agreement_number if consumer.new_agreement_number else ""
        else:
            agreement_number = consumer.agreement_number if consumer.agreement_number else ""
        if akta_type == AktaType.MU:
            vehicles = consumer_model.objects.filter(
                Q(agreement_number=agreement_number)
            )
        return consumer, legal_doc, vehicles

    def _get_recipient_info(self, document_date, legal_doc=None, consumer=None):
        def _compose_address(a, rt, rw, kel, kec, kota, prov, kodepos):
            parts = []
            if a: parts.append(str(a).strip().title())
            if rt and rw: parts.append(f"RT {rt}/RW {rw}")
            elif rt: parts.append(f"RT {rt}")
            elif rw: parts.append(f"RW {rw}")
            if kel: parts.append(kel)
            if kec: parts.append(kec)
            if kota: parts.append(kota)
            if prov: parts.append(prov)
            if kodepos: parts.append(str(kodepos))
            return ", ".join([p for p in parts if p])

        if isinstance(document_date, datetime):
            document_date = document_date.date()

        if not document_date:
            return None, JsonResponse({
                "success": False,
                "message": "Tanggal akta belum diisi.",
                "invalid_fields": ["tgl_akta"]
            }, status=400)

        recipient = None
        if legal_doc and getattr(legal_doc, "recipient_id", None):
            legal_doc_recipient = legal_doc.recipient
            recipient_start = getattr(legal_doc_recipient, "decree_date", None)
            recipient_end = getattr(legal_doc_recipient, "decree_date_end", None)
            if (
                (recipient_start is None or recipient_start <= document_date)
                and (recipient_end is None or recipient_end >= document_date)
            ):
                recipient = legal_doc_recipient

        if not recipient:
            recipient = (
                Recipient.objects
                .filter(decree_date__lte=document_date)
                .filter(Q(decree_date_end__isnull=True) | Q(decree_date_end__gte=document_date))
                .order_by("-decree_date")
                .first()
            )

        branch = None
        branch_decree = None
        if not recipient and consumer:
            branch = getattr(consumer, "branch", None)
            branch_code = self._get_consumer_branch_code(consumer)
            if branch_code:
                if not branch:
                    branch = Branch.objects.filter(branch_code=branch_code).first()

                branch_decree = (
                    BranchDecree.objects
                    .filter(branch_id=branch_code)
                    .filter(Q(start_date__isnull=True) | Q(start_date__lte=document_date))
                    .filter(Q(end_date__isnull=True) | Q(end_date__gte=document_date))
                    .order_by("-start_date", "-decree_date", "-id")
                    .first()
                )

        branch_start_date = getattr(branch, "start_date", None) if branch else None
        branch_end_date = getattr(branch, "end_date", None) if branch else None
        branch_is_effective = bool(
            branch
            and (branch_start_date is None or branch_start_date <= document_date)
            and (branch_end_date is None or branch_end_date >= document_date)
            and (getattr(branch, "decree_number", None) or getattr(branch, "decree_date", None))
        )

        if not recipient and (branch_decree or branch_is_effective):
            decree_number = ""
            decree_date = None
            if branch_decree:
                decree_number = branch_decree.decree_number or ""
                decree_date = branch_decree.decree_date or branch_decree.start_date
                branch = getattr(branch_decree, "branch", None) or branch
            elif branch:
                decree_number = getattr(branch, "decree_number", "") or ""
                decree_date = getattr(branch, "decree_date", None) or getattr(branch, "start_date", None)

            full_address = ""
            if branch:
                full_address = _compose_address(
                    getattr(branch, "address", ""),
                    getattr(branch, "rt", ""),
                    getattr(branch, "rw", ""),
                    getattr(branch, "subdistrict", ""),
                    getattr(branch, "district", ""),
                    getattr(branch, "city", ""),
                    "",
                    getattr(branch, "postal_code", ""),
                )

            pn = getattr(branch, "person_in_charge", "") if branch else ""
            recipient_date = decree_date.strftime("%d-%m-%Y") if decree_date else ""
            recipient_terbilang = terbilang_tanggal_lahir(recipient_date) if recipient_date else ""

            missing = []
            if not decree_number: missing.append("sk")
            if not recipient_date: missing.append("tgl_sk")
            if not recipient_terbilang: missing.append("terbilang_tgl_sk")
            if not full_address: missing.append("alamat_cabang")
            if missing:
                return None, JsonResponse({
                    "success": False,
                    "message": "Gagal menghasilkan dokumen. Mohon lengkapi data SK cabang yang diperlukan.",
                    "invalid_fields": missing
                }, status=400)

            return {
                "recipient": branch_decree or branch,
                "recipient_full_address": full_address,
                "recipient_sk": decree_number,
                "recipient_pn": pn or "-",
                "recipient_date": recipient_date,
                "recipient_terbilang": recipient_terbilang,
            }, None

        # 3) Kalau tetap tidak ketemu → error jelas
        if not recipient:
            return None, JsonResponse({
                "success": False,
                "message": "Penerima kuasa tidak ditemukan/berlaku untuk tanggal dokumen ini.",
                "invalid_fields": ["tgl_sk", "terbilang_tgl_sk", "sk", "alamat_cabang"]
            }, status=404)

        # 4) Pastikan alamat lengkap
        required_addr = [recipient.address, recipient.subdistrict, recipient.district, recipient.city, recipient.province]
        if not all(required_addr):
            return None, JsonResponse({
                "success": False,
                "message": "Alamat penerima kuasa belum lengkap (alamat/kel/kec/kota/prov).",
                "invalid_fields": ["alamat_cabang"]
            }, status=400)

        full_address = _compose_address(
            recipient.address, getattr(recipient, "rt", ""), getattr(recipient, "rw", ""),
            recipient.subdistrict, recipient.district, recipient.city,
            recipient.province, getattr(recipient, "postal_code", "")
        )

        # 5) Bangun nilai yang kamu pakai di placeholders (recipient_*)
        pn = recipient.pn or ""
        recipient_sk = recipient.decree_number or ""
        recipient_date = recipient.decree_date.strftime("%d-%m-%Y") if recipient.decree_date else ""
        recipient_terbilang = terbilang_tanggal_lahir(recipient.decree_date.strftime("%d-%m-%Y")) if recipient.decree_date else ""

        # 6) Validasi dini: kalau kosong, hentikan dengan pesan konsisten
        missing = []
        if not recipient_sk: missing.append("sk")
        if not pn: missing.append("pn")
        if not recipient_date: missing.append("tgl_sk")
        if not recipient_terbilang: missing.append("terbilang_tgl_sk")
        if not full_address: missing.append("alamat_cabang")
        if missing:
            return None, JsonResponse({
                "success": False,
                "message": "Gagal menghasilkan dokumen. Mohon lengkapi data yang diperlukan.",
                "invalid_fields": missing
            }, status=400)

        return {
            # kamu tetap bisa pakai nama 'recipient_*' karena placeholders kamu sudah memetakannya
            "recipient": recipient,
            "recipient_full_address": full_address,
            "recipient_sk": recipient_sk,
            "recipient_pn": pn,
            "recipient_date": recipient_date,
            "recipient_terbilang": recipient_terbilang,
        }, None

    def _resolve_notary(self, legal_doc):
        if isinstance(legal_doc.notary, Notary):
            return legal_doc.notary, None
        if isinstance(legal_doc.notary, str):
            try:
                return Notary.objects.get(firm_name__iexact=legal_doc.notary.strip()), None
            except Notary.DoesNotExist:
                return None, JsonResponse({
                    "success": False,
                    "message": f"Notaris dengan nama '{legal_doc.notary}' tidak ditemukan di tabel Notary."
                }, status=404)
        return None, JsonResponse({
            "success": False,
            "message": "Data notaris tidak valid atau kosong."
        }, status=400)
        
    def _norm(self, s: str) -> str:
        return (s or "").strip().lower().replace(" ", "").replace("_", "")
    
    def _is_blank(self, s: str) -> bool:
        return (s is None) or (str(s).strip() == "")

    def _determine_types_and_template(self, consumer, akta_type: AktaType, doc_type: DocType):
        agreement_type_member = AgreementType._value2member_map_.get((consumer.agreement_type or "").lower())
        customer_type_member = CustomerType._value2member_map_.get((consumer.customer_type or "").upper())
        if not agreement_type_member:
            return None, None, None, JsonResponse({'success': False, 'message': f'Tipe perjanjian "{consumer.agreement_type}" tidak valid.'}, status=400)
        if not customer_type_member:
            return None, None, None, JsonResponse({'success': False, 'message': f'Tipe konsumen "{consumer.customer_type}" tidak valid.'}, status=400)

        document_type_suffix = " - Salinan" if doc_type == DocType.SALINAN else ""
        jenis_doc = ""
        template_file = ""
        if akta_type == AktaType.PF:
            # ====== PF ikut validasi dasar seperti non-PF (tanpa mengubah logika non-PF) ======
            def _n(v):
                try:
                    return self._norm(v)
                except Exception:
                    return (v or "").strip().lower()

            label_lower = (getattr(customer_type_member, 'label', '') or '').strip().lower()
            name_lower  = (getattr(customer_type_member, 'name', '') or '').strip().lower()

            # Anggap PT jika label/name mengandung "pt" atau "perseroan"
            is_pt = ('pt' in label_lower) or ('perseroan' in label_lower) or (name_lower in ('company', 'badan_usaha'))
            customer_type_raw = (getattr(consumer, 'customer_type', '') or '').strip().upper()
            use_pt_template   = is_pt and (customer_type_raw == 'C')

            # ==== SUBJECT PF ====
            choose_change = self._norm(getattr(consumer, 'choose_change', None))
            choose_mistake = self._norm(getattr(consumer, 'choose_mistake_2nama', None))
            choose_mistake_2nama = self._norm(getattr(consumer, 'choose_mistake_2nama', None)) or ""
            choose_change_name = getattr(consumer, 'choose_change_name', None)
            debtor_name_new  = ""
            bpkb_name_new    = ""
            debtor_name_old  = ""
            bpkb_name_old    = ""
            debtor_name_norm = ""
            bpkb_name_norm   = ""
            names_equal      = False

            if choose_change in ('perubahan1', 'perubahan2'):
                end_path = 'subject'
                if choose_change == 'perubahan1':
                    folder_name = end_path

                    template_file = "Perubahan Fidusia Subject - 1 Nama.docx"
                    jenis_doc = "subject:1nama"

                    debtor_name_old = _n(getattr(consumer, 'debtor_name', ''))
                    bpkb_name_old   = _n(getattr(consumer, 'name_on_bpkb', ''))

                    debtor_name_new = ""
                    bpkb_name_new   = ""
                    debtor_name_norm = debtor_name_old.strip().upper()
                    bpkb_name_norm   = bpkb_name_old.strip().upper()
                    names_equal = bool(
                        debtor_name_norm
                        and bpkb_name_norm
                        and debtor_name_norm == bpkb_name_norm
                    )

                else:
                    folder_name = os.path.join('templatesakta-pf', end_path, 'typo penulisan')

                    debtor_name_new = _n(getattr(consumer, 'new_debtor_name', None))
                    bpkb_name_new   = _n(getattr(consumer, 'new_name_on_bpkb', None))
                    debtor_name_old = _n(getattr(consumer, 'debtor_name', ''))
                    bpkb_name_old   = _n(getattr(consumer, 'name_on_bpkb', ''))

                    debtor_name  = debtor_name_new or debtor_name_old
                    name_on_bpkb = bpkb_name_new  or bpkb_name_old

                    debtor_name_norm = debtor_name.strip().upper() if debtor_name else ""
                    bpkb_name_norm   = name_on_bpkb.strip().upper() if name_on_bpkb else ""
                    names_equal = bool(
                        debtor_name_norm
                        and bpkb_name_norm
                        and debtor_name_norm == bpkb_name_norm
                    )

                    has_debtor_change = bool(debtor_name_new)
                    has_bpkb_change   = bool(bpkb_name_new)

                    # AMBIL LANGSUNG DARI FIELD DI CONSUMER
                    choose_mistake_2nama = (self._norm(getattr(consumer, 'choose_mistake_2nama', None)) or "").strip().lower()

                    # ==========================================================
                    # PRIORITAS: JIKA SUDAH PILIH 2 NAMA -> LANGSUNG PAKAI TEMPLATE 2 NAMA
                    # ==========================================================
                    if choose_mistake_2nama:
                        if choose_mistake_2nama in ('pilih_bpkb', 'pilihbpkb', 'bpkb'):
                            if use_pt_template:
                                template_file = "Perubahan Fidusia Subject - Typo Penulisan - PT - 2 Nama - BPKB.docx"
                            else:
                                template_file = "Perubahan Fidusia Subject - Typo Penulisan - 2 Nama - BPKB .docx"
                            jenis_doc = "subject:typo2nama:bpkb"

                        elif choose_mistake_2nama in ('pilih_debitur', 'debitur'):
                            if use_pt_template:
                                template_file = "Perubahan Fidusia Subject - Typo Penulisan - PT - 2 Nama - DEBITUR.docx"
                            else:
                                template_file = "Perubahan Fidusia Subject - Typo Penulisan - 2 Nama - DEBITUR .docx"
                            jenis_doc = "subject:typo2nama:debitur"

                        elif choose_mistake_2nama in (
                            'pilih_bpkb&debitur',
                            'pilihbpkb&debitur',
                            'pilihbpkbdebitur',
                            'both',
                            'bpkb&debitur'
                        ):
                            template_file = "Perubahan Fidusia Subject - Typo Penulisan - 2 Nama - BPKB & DEBITUR .docx"
                            jenis_doc = "subject:typo2nama:both"

                        else:
                            if use_pt_template:
                                template_file = "Perubahan Fidusia Subject - Typo Penulisan - PT - 2 Nama - BPKB.docx"
                            else:
                                template_file = "Perubahan Fidusia Subject - Typo Penulisan - 2 Nama - BPKB .docx"
                            jenis_doc = "subject:typo2nama:bpkb"

                    else:
                        if has_debtor_change and not has_bpkb_change:
                            if use_pt_template:
                                template_file = "Perubahan Fidusia Subject - Typo Penulisan - PT - 1 Nama - DEBITUR.docx"
                            else:
                                template_file = "Perubahan Fidusia Subject - Typo Penulisan- 1 Nama.docx"
                            jenis_doc = "subject:typo1nama:debitur"

                        elif has_bpkb_change and not has_debtor_change:
                            if use_pt_template:
                                template_file = "Perubahan Fidusia Subject - Typo Penulisan - PT - 1 Nama - BPKB.docx"
                            else:
                                template_file = "Perubahan Fidusia Subject - Typo Penulisan- 1 Nama.docx"
                            jenis_doc = "subject:typo1nama:bpkb"

                        else:
                            if self._is_blank(choose_change_name) and self._is_blank(choose_mistake):
                                if use_pt_template:
                                    template_file = "Perubahan Fidusia Subject - Typo Penulisan - PT - 2 Nama - BPKB.docx"
                                else:
                                    template_file = "Perubahan Fidusia Subject - Typo Penulisan - 2 Nama - BPKB .docx"
                                jenis_doc = "subject:typo2nama:bpkb"
                            else:
                                if choose_mistake in ('pilihbpkb', 'bpkb'):
                                    if use_pt_template:
                                        template_file = "Perubahan Fidusia Subject - Typo Penulisan - PT - 2 Nama - BPKB.docx"
                                    else:
                                        template_file = "Perubahan Fidusia Subject - Typo Penulisan - 2 Nama - BPKB .docx"
                                    jenis_doc = "subject:typo2nama:bpkb"
                                elif choose_mistake in (
                                    'pilihbpkb&debitur',
                                    'pilihbpkbdebitur',
                                    'both',
                                    'bpkb&debitur'
                                ):
                                    template_file = "Perubahan Fidusia Subject - Typo Penulisan - 2 Nama - BPKB & DEBITUR .docx"
                                    jenis_doc = "subject:typo2nama:both"
                                else:
                                    if use_pt_template:
                                        template_file = "Perubahan Fidusia Subject - Typo Penulisan - PT - 2 Nama - DEBITUR.docx"
                                    else:
                                        template_file = "Perubahan Fidusia Subject - Typo Penulisan - 2 Nama - DEBITUR .docx"
                                    jenis_doc = "subject:typo2nama:debitur"

                # ==========================================================
                # ===================== LOG INFORMASI ======================
                # ==========================================================
                print("=====================================")
                print("[INFO] Mengambil Template Akta (PF)")
                print(f"  - Provider         : {consumer.fiducia_provider}")
                print(f"  - Agreement Type   : {agreement_type_member.name}")
                print(f"  - Customer Type    : {customer_type_member.label} (folder: {end_path})")
                print(f"  - Folder Template  : {folder_name}")
                print(f"  - Customer_type    : {consumer.customer_type}")
                print(f"  - Old Debitur/BPKB : '{debtor_name_old}' / '{bpkb_name_old}'")
                print(f"  - New Debitur/BPKB : '{debtor_name_new}' / '{bpkb_name_new}'")
                print(f"  - Pilihan 2 Nama   : {choose_mistake_2nama}")
                print(f"  - Compare Names    : '{debtor_name_norm}' == '{bpkb_name_norm}' -> {names_equal} (INFO ONLY, TYPO tidak pakai)")
                print(f"  - Jenis Dokumen    : {jenis_doc}")
                print(f"  - Akta Type        : -pf")
                print(f"  - Document Type    : {doc_type.name}")
                print(f"  - File Template    : {template_file}")
                print("=====================================")

            else:
                # ==== OBJECT PF ====
                end_path = 'object'

                # 🧩 Ambil nama lama & baru (jika ada)
                debtor_name_new = _n(getattr(consumer, 'new_debtor_name', None))
                bpkb_name_new   = _n(getattr(consumer, 'new_name_on_bpkb', None))
                debtor_name_old = _n(getattr(consumer, 'debtor_name', ''))
                bpkb_name_old   = _n(getattr(consumer, 'name_on_bpkb', ''))

                # ✅ Gunakan data baru jika tersedia, fallback ke data lama
                debtor_name_norm = debtor_name_new if debtor_name_new else debtor_name_old
                bpkb_name_norm   = bpkb_name_new if bpkb_name_new else bpkb_name_old

                # Bandingkan nama untuk menentukan jumlah debitur
                names_equal = bool(debtor_name_norm and bpkb_name_norm and debtor_name_norm == bpkb_name_norm)

                # Helper deteksi company tokens (tanpa ganggu fungsi lain)
                _COMPANY_TOKENS = ('pt', 'cv', 'yayasan', 'firma', 'koperasi', 'pd', 'gereja')
                def _has_company_token(raw: str) -> bool:
                    s = (raw or '').strip().lower()
                    return any(tok in s for tok in _COMPANY_TOKENS)

                # Normalisasi provider & deteksi company/C
                provider_pf = (getattr(consumer, 'fiducia_provider', '') or '').strip().lower()
                ct_name  = (getattr(customer_type_member, 'name', '') or '').strip().lower()
                ct_label = (getattr(customer_type_member, 'label', '') or '').strip().lower()
                is_company_pf = (
                    ct_name in ('company', 'badan_usaha') or
                    any(k in ct_label for k in ('pt', 'perseroan', 'badan usaha', 'cv', 'firma', 'yayasan', 'koperasi', 'pd', 'gereja'))
                )

                special_pf_applied = False
                if provider_pf == 'atas nama customer' and not is_company_pf:
                    template_file = "Perubahan Fidusia Object - Personal - 1 Deb.docx"
                    jenis_doc = "object:personal:1deb"
                    special_pf_applied = True

                if not special_pf_applied:
                    if provider_pf == 'atas nama bpkb' and is_company_pf:
                        if names_equal:
                            template_file = "Perubahan Fidusia Object - PT - 1 Deb.docx"
                            jenis_doc = "object:pt:1deb"
                            special_pf_applied = True
                        elif _has_company_token(getattr(consumer, 'new_name_on_bpkb', None) or getattr(consumer, 'name_on_bpkb', '')):
                            template_file = "Perubahan Fidusia Object - PT - customer personal.docx"
                            jenis_doc = "object:pt:customer-personal"
                            special_pf_applied = True
                        elif _has_company_token(getattr(consumer, 'new_debtor_name', None) or getattr(consumer, 'debtor_name', '')):
                            template_file = "Perubahan Fidusia Object - PT - bpkb.docx"
                            jenis_doc = "object:pt:bpkb"
                            special_pf_applied = True

                if not special_pf_applied:
                    deb_suffix = "1 Deb" if names_equal else "2 Deb"
                    pf_customer_folder = "PT" if is_pt else "Personal"
                    template_file = f"Perubahan Fidusia Object - {pf_customer_folder} - {deb_suffix}.docx"
                    jenis_doc = f"object:{pf_customer_folder.lower()}:{'1deb' if names_equal else '2deb'}"

                print("=====================================")
                print("[INFO] Mengambil Template Akta (PF)")
                print(f"  - Provider         : {consumer.fiducia_provider}")
                print(f"  - Agreement Type   : {agreement_type_member.name}")
                print(f"  - Customer Type    : {customer_type_member.label} (folder: {end_path})")
                print(f"  - Old Debitur/BPKB : '{debtor_name_old}' / '{bpkb_name_old}'")
                print(f"  - New Debitur/BPKB : '{debtor_name_new}' / '{bpkb_name_new}'")
                print(f"  - Compare Names    : '{debtor_name_norm}' == '{bpkb_name_norm}' -> {names_equal}")
                print(f"  - Jenis Dokumen    : {jenis_doc}")
                print(f"  - Akta Type        : -pf")
                print(f"  - Document Type    : {doc_type.name}")
                print(f"  - File Template    : {template_file}")
                print("=====================================")

                aktaType = f"-{akta_type.value}"
                folder_name = (
                    f'templatesakta{aktaType}-salinan/{end_path}'
                    if doc_type == DocType.SALINAN
                    else f'templatesakta{aktaType}/{end_path}'
                )

        else:
            # =========================
            # Non-PF (Reguler / OC / lainnya)
            # =========================

            # --- helper: normalizer dan folder mapper ---
            def _n(v):
                try:
                    return self._norm(v)
                except Exception:
                    return (v or "").strip().lower()

            def _end_path_for_customer(ct_member):
                """
                Map tipe konsumen ke folder:
                - company -> 'company'
                - individual/personal -> 'individual'
                """
                name = (getattr(ct_member, 'name', '') or '').strip().lower()
                label = (getattr(ct_member, 'label', '') or '').strip().lower()
                if name in ('company', 'badan_usaha') or any(k in label for k in ('pt', 'perseroan', 'badan usaha', 'cv', 'firma')):
                    return 'company'
                return 'individual'

            provider = (consumer.fiducia_provider or "").strip().lower()
            debtor_name_norm = _n(getattr(consumer, 'debtor_name', ''))
            bpkb_name_norm   = _n(getattr(consumer, 'name_on_bpkb', ''))

            # --- tentukan folder lebih awal agar tahu ini Company/PT atau bukan ---
            end_path = _end_path_for_customer(customer_type_member)  # 'company' / 'individual'
            is_company = (end_path == 'company')
            label_lower = (getattr(customer_type_member, 'label', '') or '').strip().lower()
            is_pt = ('pt' in label_lower) or ('perseroan' in label_lower)
            names_different = bool(debtor_name_norm and bpkb_name_norm and debtor_name_norm != bpkb_name_norm)

            # ======== RULE TAMBAHAN (KHUSUS PT) ========
            # Prioritas khusus sesuai permintaan:
            # - Jika provider = "Atas Nama BPKB", customer company (C),
            #   dan name_on_bpkb mengandung salah satu kata {PT, CV, YAYASAN, FIRMA, KOPERASI, PD, GEREJA}
            #     -> pakai " - customer personal"
            # - Jika provider = "Atas Nama BPKB", customer company (C),
            #   dan debtor_name mengandung salah satu kata {PT, CV, YAYASAN, FIRMA, KOPERASI, PD, GEREJA}
            #     -> pakai " - bpkb"
            # Catatan: disisipkan minimal tanpa mengubah logika lain.

            _COMPANY_TOKENS = ('pt', 'cv', 'yayasan', 'firma', 'koperasi', 'pd', 'gereja', 'institut')

            def _has_company_token(raw: str) -> bool:
                s = (raw or '').strip().lower()
                return any(tok in s for tok in _COMPANY_TOKENS)

            # Prioritas aturan permintaan user (hanya bila jelas: provider persis "atas nama bpkb" & company)
            if is_company and provider == "atas nama bpkb":
                if debtor_name_norm == bpkb_name_norm:
                    jenis_doc = " - customer"
                elif _has_company_token(getattr(consumer, 'name_on_bpkb', '')):
                    jenis_doc = " - customer personal"
                elif _has_company_token(getattr(consumer, 'debtor_name', '')):
                    jenis_doc = " - bpkb"
                else:
                    # fallback ke logika lama di bawah
                    if is_pt and names_different:
                        jenis_doc = " - customer personal"
                    else:
                        if provider in ("atas nama bpkb", "bpkb") and is_company and debtor_name_norm and bpkb_name_norm and debtor_name_norm == bpkb_name_norm:
                            jenis_doc = " - customer"
                        else:
                            if provider in ("atas nama bpkb", "bpkb"):
                                if debtor_name_norm and bpkb_name_norm and debtor_name_norm == bpkb_name_norm:
                                    jenis_doc = " - bpkb - 1"   # sama -> 1 nama
                                else:
                                    jenis_doc = " - bpkb"       # beda -> 2 nama
                            elif provider in ("atas nama customer", "customer"):
                                jenis_doc = " - customer"
                            else:
                                jenis_doc = " - bpkb" if debtor_name_norm != bpkb_name_norm else " - customer"
            else:
                # === Logika lama tetap berjalan bila tidak masuk kasus prioritas di atas ===
                if is_company and is_pt and names_different:
                    jenis_doc = " - customer personal"
                else:
                    if provider in ("atas nama bpkb", "bpkb") and is_company and debtor_name_norm and bpkb_name_norm and debtor_name_norm == bpkb_name_norm:
                        jenis_doc = " - customer"
                    else:
                        if provider in ("atas nama bpkb", "bpkb"):
                            if debtor_name_norm and bpkb_name_norm and debtor_name_norm == bpkb_name_norm:
                                jenis_doc = " - bpkb - 1"   # sama -> 1 nama
                            else:
                                jenis_doc = " - bpkb"       # beda -> 2 nama
                        elif provider in ("atas nama customer", "customer"):
                            jenis_doc = " - customer"
                        else:
                            jenis_doc = " - bpkb" if debtor_name_norm != bpkb_name_norm else " - customer"

            # Mizuho (requirement §7.1): reguler perorangan selalu template "customer" (pemberi fidusia =
            # debitur), walau nama BPKB berbeda (umum pada unit bekas); nama BPKB dicetak di uraian obyek.
            if akta_type == AktaType.REGULER and not is_company:
                jenis_doc = " - customer"

            aktaType = "" if akta_type in (AktaType.REGULER, AktaType.OC) else f"-{akta_type.value}"

            template_file = (
                f"New BPKB - Draft Akta {agreement_type_member.name} - "
                f"{customer_type_member.label}{jenis_doc}{document_type_suffix}.docx"
            )
            folder_name = (
                f'templatesakta{aktaType}-salinan/{end_path}'
                if doc_type == DocType.SALINAN
                else f'templatesakta{aktaType}/{end_path}'
            )

            # 🔍 Print log ringkas & jelas
            print("=====================================")
            print("[INFO] Mengambil Template Akta (Non-PF)")
            print(f"  - Provider         : {consumer.fiducia_provider}")
            print(f"  - Debitur vs BPKB  : '{consumer.debtor_name}' vs '{consumer.name_on_bpkb}'")
            print(f"  - Agreement Type   : {agreement_type_member.name}")  # Konvensional / Syariah
            print(f"  - Customer Type    : {customer_type_member.label} (folder: {end_path})")
            print(f"  - Jenis Dokumen    : {jenis_doc}")
            print(f"  - Akta Type        : {aktaType or '(reguler/oc)'}")
            print(f"  - Document Type    : {doc_type.name}")
            print(f"  - Folder Template  : {folder_name}")
            print(f"  - File Template    : {template_file}")
            print("=====================================")

        # ================================== LOKASI FILE ==================================
        template_path = os.path.join(settings.BASE_DIR, 'media', folder_name, template_file)

        # Fallback 1: jika pakai " .docx"
        if not os.path.exists(template_path) and " .docx" in template_file:
            alt_template_file = template_file.replace(" .docx", ".docx")
            alt_template_path = os.path.join(settings.BASE_DIR, 'media', folder_name, alt_template_file)
            if os.path.exists(alt_template_path):
                template_file = alt_template_file
                template_path = alt_template_path

        # Fallback 2: khusus BPKB — jika varian '- 1' tidak ada, coba tanpa '- 1'; dan sebaliknya
        if not os.path.exists(template_path) and " - bpkb" in template_file:
            if " - 1.docx" in template_file:
                # coba varian tanpa - 1 (2 nama)
                alt_template_file = template_file.replace(" - 1.docx", ".docx")
            else:
                # coba varian dengan - 1 (1 nama)
                alt_template_file = template_file.replace(".docx", " - 1.docx")
            alt_template_path = os.path.join(settings.BASE_DIR, 'media', folder_name, alt_template_file)
            if os.path.exists(alt_template_path):
                print(f"[WARN] Fallback ke varian file: {alt_template_file}")
                template_file = alt_template_file
                template_path = alt_template_path

        # Jika tetap tidak ada, tampilkan isi folder untuk debugging
        if not os.path.exists(template_path):
            try:
                base_dir = os.path.join(settings.BASE_DIR, 'media', folder_name)
                print("[DEBUG] Folder:", base_dir)
                print("[DEBUG] Files:", os.listdir(base_dir))
            except Exception as e:
                print("[DEBUG] Cannot list dir:", e)
            return None, None, None, JsonResponse({'success': False, 'message': f'Template file tidak ditemukan: {template_path}'}, status=404)

        return customer_type_member, jenis_doc, template_path, None

    def _admin_birth_info(self, admin: AdminProxy):
        birth_place, birth_date_str = safe_split_birth_info(getattr(admin, 'birth_info', ''))
        birth_date = datetime.strptime(birth_date_str, '%d-%m-%Y') if birth_date_str else None

        return SimpleNamespace(
            user_tempat_lahir=birth_place.strip(),
            user_tanggal_lahir=birth_date.strftime('%d-%m-%Y') if birth_date else '',
            user_terbilang_ttl=terbilang_tanggal_lahir(birth_date_str) if birth_date_str else ''
        )
        
    def _witnesses_info(self, notary_instance: Notary):
        from dateutil import parser
        witnesses = Witness.objects.filter(notary_id=notary_instance.id)
        witness0 = witnesses[0] if len(witnesses) > 0 else None
        witness1 = witnesses[1] if len(witnesses) > 1 else None

        def parse_birth(w):
            if not w:
                return '', '', ''
            tempat, tgl = safe_split_birth_info(w.birth_info)
            tgl_num = ''
            try:
                dt = parser.parse((tgl or '').strip(), dayfirst=True)
                tgl_num = dt.strftime('%d-%m-%Y')            # <-- angka strip
            except Exception:
                tgl_num = (tgl or '').strip()                # fallback: tampilkan apa adanya
            return tempat.strip(), tgl_num, terbilang_tanggal_lahir(tgl or '')

        saksi0_tempat_lahir, saksi0_tgl_lahir, saksi0_terbilang_ttl = parse_birth(witness0)
        saksi1_tempat_lahir, saksi1_tgl_lahir, saksi1_terbilang_ttl = parse_birth(witness1)

        return witness0, witness1, {
            "saksi0_tempat_lahir": saksi0_tempat_lahir,
            "saksi0_tgl_lahir": saksi0_tgl_lahir,                 # <-- SUDAH angka
            "saksi0_terbilang_ttl": saksi0_terbilang_ttl,
            "saksi1_tempat_lahir": saksi1_tempat_lahir,
            "saksi1_tgl_lahir": saksi1_tgl_lahir,                 # <-- SUDAH angka
            "saksi1_terbilang_ttl": saksi1_terbilang_ttl,
        }

    def _current_time_info(self, document_date, document_time):
        tgl_hari_ini = document_date.strftime('%d-%m-%Y') if document_date else ''
        hari_ini = HARI_ID[document_date.weekday()] if document_date else ''
        terbilang_hari_ini = tanggal_terbilang(document_date) if document_date else ''
        pukul_akta = document_time.strftime('%H:%M') if document_time else ''

        terbilang_pukul = ''
        if document_time:
            jam = document_time.hour
            menit = document_time.minute
            if jam == 0 and menit == 0:
                terbilang_pukul = 'pukul dua belas malam'
            else:
                jam_terbilang = num2words(jam, lang='id')
                menit_terbilang = f"lewat {num2words(menit, lang='id')} menit" if menit > 0 else ''
                terbilang_pukul = f"pukul {jam_terbilang} {menit_terbilang}".strip()

        return {
            "tgl_hari_ini_str": tgl_hari_ini,
            "hari_ini": hari_ini,
            "terbilang_hari_ini": terbilang_hari_ini,
            "pukul_akta": pukul_akta,
            "terbilang_pukul": terbilang_pukul,
        }

    def _vehicle_placeholders(self, akta_type: AktaType, consumer, vehicles):
        if akta_type == AktaType.MU:
            if vehicles.count() == 0:
                return None, JsonResponse({
                    "success": False,
                    "message": "Tidak ada kendaraan yang ditemukan untuk konsumen ini."
                }, status=404)
            data = {
                'kendaraan_list': [
                    {
                        "merk": v.brand or "",
                        "type": f"{(v.type or '').strip()} {(v.model or '').strip()}".strip(),
                        "no_rangka": v.chassis_number or "",
                        "no_mesin": v.engine_number or "",
                        "warna": v.color or "",
                        "tahun": str(v.production_year) if v.production_year else "",
                    } for v in vehicles
                ]
            }
            return data, None
        # Non-MU
        return {
            "merk": consumer.brand or "",
            "type": f"{(consumer.type or '').strip()} {(consumer.model or '').strip()}".strip(),
            "no_rangka": consumer.chassis_number or "",
            "no_mesin": consumer.engine_number or "",
            "warna": consumer.color or "",
            "tahun": str(consumer.production_year) if consumer.production_year else "",
        }, None

    def _customer_placeholders(self, jenis_doc, customer_type: CustomerType, consumer, akta_type: AktaType):
        fiduciaProvider = (consumer.fiducia_provider or "").lower()
        placeholders = {}
        def _mirror_bpkb_to_debitur(ph):
            ph.update({
                "debitur_kewarganegaraan": (get_nationality_full(getattr(consumer, 'bpkb_nationality', 'Warga Negara Indonesia').strip()).strip() or 'Warga Negara Indonesia'),
                "tempat_lahir_debitur": getattr(consumer, 'bpkb_birth_place', '') or '',
                "tgl_lahir_debitur": consumer.bpkb_birth_date.strftime('%d-%m-%Y') if getattr(consumer, 'bpkb_birth_date', None) else '',
                "terbilang_tgl_lahir_debitur": terbilang_tanggal_lahir(consumer.bpkb_birth_date.strftime('%d-%m-%Y')) if getattr(consumer, 'bpkb_birth_date', None) else '-',
                "pekerjaan_debitur": getattr(consumer, 'bpkb_occupation', '') or '',
                "kota_debitur": getattr(consumer, 'bpkb_city', '') or '',
                "alamat_debitur": getattr(consumer, 'bpkb_address', '') or '',
                "rt_debitur": getattr(consumer, 'bpkb_rt', '') or '',
                "rw_debitur": getattr(consumer, 'bpkb_rw', '') or '',
                "kelurahan_debitur": getattr(consumer, 'bpkb_subdistrict', '') or '',
                "kecamatan_debitur": getattr(consumer, 'bpkb_district', '') or '',
                "provinsi_debitur": getattr(consumer, 'bpkb_province', '') or '',
                "kode_pos_debitur": getattr(consumer, 'bpkb_postal_code', '') or '',
                #"ktp_debitur": getattr(consumer, 'bpkb_ktp', '') or '',
            })

        is_provider_bpkb = ('atas nama bpkb' in fiduciaProvider) or (fiduciaProvider == 'bpkb')
        is_one_name = (jenis_doc.strip() == "- bpkb - 1") or (jenis_doc.endswith(" - bpkb - 1"))
    
        if jenis_doc.startswith(" - bpkb"):
            placeholders.update({
                "nama_pada_bpkb": consumer.name_on_bpkb or "",
                "kewarganegaraan_bpkb": (get_nationality_full((consumer.bpkb_nationality or 'Warga Negara Indonesia').strip()).strip() or "Warga Negara Indonesia"),
                "tempat_lahir_bpkb": consumer.bpkb_birth_place or "",
                "tgl_lahir_bpkb": consumer.bpkb_birth_date.strftime("%d-%m-%Y") if consumer.bpkb_birth_date else "",
                "terbilang_tgl_lahir_bpkb": terbilang_tanggal_lahir(consumer.bpkb_birth_date.strftime("%d-%m-%Y")) if consumer.bpkb_birth_date else "-",
                "pekerjaan_bpkb": consumer.bpkb_occupation or "",
                "kabupaten_bpkb": consumer.bpkb_city or "",
                "alamat_bpkb": consumer.bpkb_address or "",
                "rt_bpkb": consumer.bpkb_rt or "",
                "rw_bpkb": consumer.bpkb_rw or "",
                "kelurahan_bpkb": consumer.bpkb_subdistrict or "",
                "kecamatan_bpkb": consumer.bpkb_district or "",
                "provinsi_bpkb": consumer.bpkb_province or "",
                "kode_pos_bpkb": consumer.bpkb_postal_code or "",
                "ktp_bpkb": consumer.bpkb_ktp or "",
            })

            if is_one_name or is_provider_bpkb:
                _mirror_bpkb_to_debitur(placeholders)
                
        elif jenis_doc == " - customer" and customer_type == CustomerType.INDIVIDUAL:
            if is_provider_bpkb:
                _mirror_bpkb_to_debitur(placeholders)
            if is_provider_bpkb:
                placeholders.update({
                    "debitur_kewarganegaraan": (get_nationality_full(consumer.bpkb_nationality or 'Warga Negara Indonesia').strip()) or "Warga Negara Indonesia",
                    "tempat_lahir_debitur": getattr(consumer, 'bpkb_birth_place', '') or '',
                    "tgl_lahir_debitur": consumer.bpkb_birth_date.strftime('%d-%m-%Y') if getattr(consumer, 'bpkb_birth_date', None) else '',
                    "terbilang_tgl_lahir_debitur": terbilang_tanggal_lahir(consumer.bpkb_birth_date.strftime('%d-%m-%Y')) if getattr(consumer, 'bpkb_birth_date', None) else '-',
                    "pekerjaan_debitur": getattr(consumer, 'bpkb_occupation', '') or '',
                    "kota_debitur": getattr(consumer, 'bpkb_city', '') or '',
                    "alamat_debitur": getattr(consumer, 'bpkb_address', '') or '',
                    "rt_debitur": getattr(consumer, 'bpkb_rt', '') or '',
                    "rw_debitur": getattr(consumer, 'bpkb_rw', '') or '',
                    "kelurahan_debitur": getattr(consumer, 'bpkb_subdistrict', '') or '',
                    "kecamatan_debitur": getattr(consumer, 'bpkb_district', '') or '',
                    "provinsi_debitur": getattr(consumer, 'bpkb_province', '') or '',
                    "kode_pos_debitur": getattr(consumer, 'bpkb_postal_code', '') or '',
                    #"ktp_debitur": getattr(consumer, 'bpkb_ktp', '') or '',
                })
            else:
                placeholders.update({
                    "debitur_kewarganegaraan": (get_nationality_full(consumer.nationality or 'Warga Negara Indonesia').strip()) or "Warga Negara Indonesia",
                    "tempat_lahir_debitur": consumer.birth_place,
                    "tgl_lahir_debitur": consumer.birth_date.strftime('%d-%m-%Y') if consumer.birth_date else '',
                    "terbilang_tgl_lahir_debitur": terbilang_tanggal_lahir(consumer.birth_date.strftime('%d-%m-%Y')) if consumer.birth_date else '-',
                    "pekerjaan_debitur": consumer.occupation or '',
                    "kota_debitur": consumer.city or '',
                    "alamat_debitur": consumer.address or '',
                    "rt_debitur": (consumer.rt_rw or '').replace(' ', '').split('/')[0] if getattr(consumer, 'rt_rw', None) else '',
                    "rw_debitur": (consumer.rt_rw or '').replace(' ', '').split('/')[1] if getattr(consumer, 'rt_rw', None) and '/' in (consumer.rt_rw or '') else '',
                    #"rw_debitur": (consumer.rw or ''),
                    "kelurahan_debitur": consumer.subdistrict or '',
                    "kecamatan_debitur": consumer.district or '',
                    "provinsi_debitur": consumer.province or '',
                    "kode_pos_debitur": consumer.postal_code or '',
                    #"ktp_debitur": consumer.id_number or '',
                })
        elif jenis_doc == " - customer" and customer_type == CustomerType.COMPANY:
            # Jika fiduciaProvider mengandung 'Atas Nama BPKB', gunakan data BPKB untuk placeholder penanggung jawab (pj)
            if 'atas nama bpkb' in fiduciaProvider.lower():
                placeholders.update({
                    "kewarganegaraan_pj": (get_nationality_full(consumer.director_nationality or 'Warga Negara Indonesia').strip()) or "Warga Negara Indonesia",
                    "tempat_lahir_pj": getattr(consumer, 'director_birth_place', '') or '',
                    "tgl_lahir_pj": consumer.director_birth_date.strftime('%d-%m-%Y') if getattr(consumer, 'director_birth_date', None) else '',
                    "terbilang_tl_pj": terbilang_tanggal_lahir(consumer.director_birth_date.strftime('%d-%m-%Y')) if getattr(consumer, 'director_birth_date', None) else '-',
                    "pekerjaan_pj": getattr(consumer, 'director_occupation', '') or '',
                    "kota_pj_badan": getattr(consumer, 'director_city', '') or '',
                    "alamat_pj_badan": getattr(consumer, 'director_address', '') or '',
                    'rt_pj': (consumer.director_rt_rw or '').replace(' ', '').split('/')[0] if getattr(consumer, 'director_rt_rw', None) else '',
                    'rw_pj': (consumer.director_rt_rw or '').replace(' ', '').split('/')[1] if getattr(consumer, 'director_rt_rw', None) and '/' in (consumer.director_rt_rw or '') else '',
                    'kelurahan_pj': getattr(consumer, 'director_subdistrict', '') or '',
                    'kecamatan_pj': getattr(consumer, 'director_district', '') or '',
                    'provinsi_pj_badan': getattr(consumer, 'director_province', '') or '',
                    "noid_pj": getattr(consumer, 'director_id_number', '') or ''
                })
            else:
                placeholders.update({
                    "kewarganegaraan_pj": (get_nationality_full(consumer.director_nationality or 'Warga Negara Indonesia').strip()) or "Warga Negara Indonesia",
                    "tempat_lahir_pj": consumer.director_birth_place or '',
                    "tgl_lahir_pj": consumer.director_birth_date.strftime('%d-%m-%Y') if consumer.director_birth_date else '',
                    "terbilang_tl_pj": terbilang_tanggal_lahir(consumer.director_birth_date.strftime('%d-%m-%Y')) if consumer.director_birth_date else '-',
                    "pekerjaan_pj": consumer.director_occupation or '',
                    "kota_pj_badan": consumer.director_city or '',
                    "alamat_pj_badan": consumer.director_address or '',
                    'rt_pj': (consumer.director_rt_rw or '').replace(' ', '').split('/')[0] if getattr(consumer, 'director_rt_rw', None) else '',
                    'rw_pj': (consumer.director_rt_rw or '').replace(' ', '').split('/')[1] if getattr(consumer, 'director_rt_rw', None) and '/' in (consumer.director_rt_rw or '') else '',
                    'kelurahan_pj': consumer.director_subdistrict or '',
                    'kecamatan_pj': consumer.director_district or '',
                    'provinsi_pj_badan': consumer.director_province or '',
                    "noid_pj": consumer.director_id_number or ''
                })

        # Additional placeholders per customer type
        # if customer_type == CustomerType.INDIVIDUAL and akta_type != AktaType.MU:
        #     placeholders.update({
        #         "user_pekerjaan": consumer.occupation or "",
        #     })
        if customer_type == CustomerType.COMPANY:
            placeholders.update({
                "jabatan_pj": consumer.director_position or '',
                "kota_debitur": consumer.city or '',
                "nama_pj": consumer.company_director_name or '',
                "kewarganegaraan_pj": (get_nationality_full(consumer.director_nationality or 'Warga Negara Indonesia').strip()) or "Warga Negara Indonesia",
                "tgl_lahir_pj": consumer.director_birth_date.strftime('%d-%m-%Y') if consumer.director_birth_date else '',
                "terbilang_tl_pj": terbilang_tanggal_lahir(consumer.director_birth_date.strftime('%d-%m-%Y')) if getattr(consumer, 'director_birth_date', None) else '-',
                "pekerjaan_pj": consumer.director_occupation or '',
                "kota_pj_badan": consumer.director_city or '',
                "alamat_pj_badan": consumer.director_address or '',
                "rt_pj": (consumer.director_rt_rw or '').replace(' ', '').split('/')[0] if getattr(consumer, 'director_rt_rw', None) else '',
                "rw_pj": (consumer.director_rt_rw or '').replace(' ', '').split('/')[1] if getattr(consumer, 'director_rt_rw', None) and '/' in (consumer.director_rt_rw or '') else '',
                "kelurahan_pj": consumer.director_subdistrict or '',
                "kecamatan_pj": consumer.director_district or '',
                "provinsi_pj_badan": consumer.director_province or '',
                "noid_pj": consumer.director_id_number or '',
                "tempat_lahir_pj": consumer.director_birth_place or '',
                "no_akta_badan": consumer.deed_number or '',
                "notaris_badan_city": consumer.notary_city or '',
                "notaris_pj_badan": consumer.director_notary_name,
                "tgl_akta_badan": consumer.deed_date.strftime('%d-%m-%Y') if consumer.deed_date else '',
                "terbilang_akta_badan": tanggal_terbilang(consumer.deed_date) if consumer.deed_date else '-',
                "qty_kendaraan": consumer.unit_quantity or 1,
                "terbilang_kendaraan": terbilang_unit(consumer.unit_quantity) if consumer.unit_quantity else '1',
                "nama_pada_bpkb": getattr(consumer, 'name_on_bpkb', '') or '',
                "kabupaten_bpkb": getattr(consumer, 'bpkb_city', '') or '',
            })
        return placeholders

    def _customer_placeholders_pf(self, jenis_doc, customer_type: CustomerType, consumer):
        placeholders = {}

        fiduciaProvider = (consumer.fiducia_provider or "").lower()

        # Helper sama seperti di _customer_placeholders
        def _mirror_bpkb_to_debitur(ph):
            ph.update({
                "debitur_kewarganegaraan": (get_nationality_full(((getattr(consumer, 'nationality', None) or 'Warga Negara Indonesia').strip()))or 'Warga Negara Indonesia'),
                "tempat_lahir_debitur": getattr(consumer, 'birth_place', '') or '',
                "tgl_lahir_debitur": consumer.birth_date.strftime('%d-%m-%Y') if getattr(consumer, 'birth_date', None) else '',
                "terbilang_tgl_lahir_debitur": terbilang_tanggal_lahir(consumer.birth_date.strftime('%d-%m-%Y')) if getattr(consumer, 'birth_date', None) else '-',
                "pekerjaan_debitur": getattr(consumer, 'occupation', '') or '',
                "kota_debitur": getattr(consumer, 'city', '') or '',
                "alamat_debitur": getattr(consumer, 'address', '') or '',
                "alamat_debitur_new": getattr(consumer, 'new_address', '') or getattr(consumer, 'address', '') or '',
                "rt_debitur": getattr(consumer, 'rt_rw', '') or '',
                "rt_debitur_new": ((consumer.new_rt_rw or '').replace(' ', '').split('/')[0] if getattr(consumer, 'new_rt_rw', None) else ((consumer.rt_rw or '').replace(' ', '').split('/')[0] if getattr(consumer, 'rt_rw', None) else placeholders.get("rt_rw", ""))),
                "rw_debitur": getattr(consumer, 'rw', '') or '',
                "rw_debitur_new": ((consumer.new_rt_rw or '').replace(' ', '').split('/')[1] if getattr(consumer, 'new_rt_rw', None) and '/' in (consumer.new_rt_rw or '') else ((consumer.rt_rw or '').replace(' ', '').split('/')[1] if getattr(consumer, 'rt_rw', None) and '/' in (consumer.rt_rw or '') else "-")),
                "kelurahan_debitur": getattr(consumer, 'subdistrict', '') or '',
                "kelurahan_debitur_new": getattr(consumer, 'new_subdistrict', '') or getattr(consumer, 'subdistrict', '') or '',
                "kecamatan_debitur": getattr(consumer, 'district', '') or '',
                "kecamatan_debitur_new": getattr(consumer, 'new_district', '') or getattr(consumer, 'district', '') or '',
                "provinsi_debitur": getattr(consumer, 'province', '') or '',
                "provinsi_debitur_new": getattr(consumer, 'new_province', '') or getattr(consumer, 'province', '') or '',
                "kode_pos_debitur": getattr(consumer, 'postal_code', '') or '',
                "kode_pos_debitur_new": getattr(consumer, 'new_postal_code', '') or getattr(consumer, 'postal_code', '') or '',
                "kota_debitur_new": getattr(consumer, 'new_city', '') or getattr(consumer, 'city', '') or '',
            })

        is_provider_bpkb = 'atas nama bpkb' in fiduciaProvider
        is_provider_customer = 'atas nama customer' in fiduciaProvider

        if customer_type == CustomerType.INDIVIDUAL:
            placeholders.update({
                "kewarganegaraan_bpkb": (get_nationality_full((consumer.bpkb_nationality or 'Warga Negara Indonesia').strip()) or "Warga Negara Indonesia"),
                "kewarganegaraan_bpkb_new": (get_nationality_full((consumer.new_bpkb_nationality or 'Warga Negara Indonesia').strip()) or "Warga Negara Indonesia"),
                "tempat_lahir_bpkb": consumer.bpkb_birth_place or "-",
                "tempat_lahir_bpkb_new": consumer.new_bpkb_birth_place or "-",
                "tgl_lahir_bpkb": consumer.bpkb_birth_date.strftime("%d-%m-%Y") if consumer.bpkb_birth_date else "-",
                "tgl_lahir_bpkb_new": consumer.new_bpkb_birth_date.strftime("%d-%m-%Y") if consumer.new_bpkb_birth_date else "-",
                "terbilang_tgl_lahir_bpkb": terbilang_tanggal_lahir(consumer.bpkb_birth_date.strftime("%d-%m-%Y")) if consumer.bpkb_birth_date else "-",
                "terbilang_tgl_lahir_bpkb_new": terbilang_tanggal_lahir(consumer.new_bpkb_birth_date.strftime("%d-%m-%Y")) if consumer.new_bpkb_birth_date else "-",
                "pekerjaan_bpkb": consumer.bpkb_occupation or "",
                "pekerjaan_bpkb_new": consumer.new_bpkb_occupation or "-",
                "kabupaten_bpkb": consumer.bpkb_city or "",
                "kabupaten_bpkb_new": consumer.new_bpkb_city or "-",
                "alamat_bpkb": consumer.bpkb_address or "",
                "alamat_bpkb_new": consumer.new_bpkb_address or "-",
                "rt_bpkb": consumer.bpkb_rt or "",
                "rt_bpkb_new": consumer.new_bpkb_rt or "-",
                "rw_bpkb": consumer.bpkb_rw or "",
                "rw_bpkb_new": consumer.new_bpkb_rw or "-",
                "kelurahan_bpkb": consumer.bpkb_subdistrict or "",
                "kelurahan_bpkb_new": consumer.new_bpkb_subdistrict or "-",
                "kecamatan_bpkb": consumer.bpkb_district or "",
                "kecamatan_bpkb_new": consumer.new_bpkb_district or "-",
                "provinsi_bpkb": consumer.bpkb_province or "",
                "provinsi_bpkb_new": consumer.new_bpkb_province or "-",
                "kode_pos_bpkb": consumer.bpkb_postal_code or "",
                "kode_pos_bpkb_new": consumer.new_bpkb_postal_code or "-",
                "ktp_bpkb": consumer.bpkb_ktp or "",
                "ktp_bpkb_new": consumer.new_bpkb_ktp or "-",
                "nama_pada_bpkb": getattr(consumer, "name_on_bpkb", None) or getattr(consumer, "new_name_on_bpkb", None) or "",
                "nama_pada_bpkb_new": getattr(consumer, "new_name_on_bpkb", None) or getattr(consumer, "name_on_bpkb", None) or "",
            })

            # Jika provider Atas Nama Customer → pakai data non BPKB (debitur) untuk field BPKB
            if is_provider_customer:
                placeholders.update({
                    "nama_pada_bpkb": consumer.debtor_name or "",
                    "nama_pada_bpkb_new": consumer.new_debtor_name or consumer.debtor_name or "-",
                    "kewarganegaraan_bpkb": ((get_nationality_full((consumer.nationality or 'Warga Negara Indonesia').strip()) or "Warga Negara Indonesia").strip()),
                    "kewarganegaraan_bpkb_new": (get_nationality_full((consumer.new_nationality or 'Warga Negara Indonesia').strip()) or "Warga Negara Indonesia"),
                    "tempat_lahir_bpkb_new": consumer.new_birth_place or consumer.birth_place or "-",
                    "tgl_lahir_bpkb_new": (consumer.new_birth_date.strftime("%d-%m-%Y")if getattr(consumer, 'new_birth_date', None)else (consumer.birth_date.strftime("%d-%m-%Y")if getattr(consumer, 'birth_date', None)else "-")),
                    "terbilang_tgl_lahir_bpkb_new": (terbilang_tanggal_lahir((consumer.new_birth_date.strftime("%d-%m-%Y")if getattr(consumer, 'new_birth_date', None) else (consumer.birth_date.strftime("%d-%m-%Y")if getattr(consumer, 'birth_date', None) else "-"))) if (getattr(consumer, 'new_birth_date', None) or getattr(consumer, 'birth_date', None)) else "-"),
                    "pekerjaan_bpkb_new": consumer.new_occupation or consumer.occupation or "-",
                    "kabupaten_bpkb_new": consumer.new_bpkb_city or consumer.bpkb_city or "-",
                    "alamat_bpkb_new": consumer.new_address or consumer.address or "-",
                    "rt_bpkb_new": (consumer.new_rt_rw.replace(" ", "").split("/")[0] if getattr(consumer, "new_rt_rw", None) and "/" in consumer.new_rt_rw else (consumer.rt_rw.replace(" ", "").split("/")[0] if getattr(consumer, "rt_rw", None) and "/" in consumer.rt_rw else placeholders.get("rt_bpkb", "-"))),
                    "rw_bpkb_new": (consumer.new_rt_rw.replace(" ", "").split("/")[1] if getattr(consumer, "new_rt_rw", None) and "/" in consumer.new_rt_rw else (consumer.rt_rw.replace(" ", "").split("/")[1] if getattr(consumer, "rt_rw", None) and "/" in consumer.rt_rw else placeholders.get("rw_bpkb", "-"))),
                    "kelurahan_bpkb_new": consumer.new_subdistrict or consumer.subdistrict or "-",
                    "kecamatan_bpkb_new": consumer.new_district or consumer.district or "-",
                    "provinsi_bpkb_new": consumer.new_province or consumer.province or "-",
                    "kode_pos_bpkb_new": consumer.new_postal_code or consumer.postal_code or "-",
                    "ktp_bpkb_new": consumer.new_bpkb_ktp or consumer.bpkb_ktp or "-",
                    "tempat_lahir_bpkb": consumer.birth_place or placeholders.get("tempat_lahir_bpkb", ""),
                    "tgl_lahir_bpkb": consumer.birth_date.strftime("%d-%m-%Y") if getattr(consumer, 'birth_date', None) else placeholders.get("tgl_lahir_bpkb", ""),
                    "terbilang_tgl_lahir_bpkb": (terbilang_tanggal_lahir(consumer.birth_date.strftime("%d-%m-%Y")) if getattr(consumer, 'birth_date', None) else placeholders.get("terbilang_tgl_lahir_bpkb", "-")),
                    "pekerjaan_bpkb": consumer.occupation or placeholders.get("pekerjaan_bpkb", ""),
                    "kabupaten_bpkb": consumer.city or placeholders.get("kabupaten_bpkb", ""),
                    "alamat_bpkb": consumer.address or placeholders.get("alamat_bpkb", ""),
                    "rt_bpkb": ((consumer.rt_rw or '').replace(' ', '').split('/')[0] if getattr(consumer, 'rt_rw', None) else placeholders.get("rt_bpkb", "")),
                    "rw_bpkb": (consumer.rw or ((consumer.rt_rw or '').replace(' ', '').split('/')[1] if getattr(consumer, 'rt_rw', None) and '/' in (consumer.rt_rw or '') else placeholders.get("rw_bpkb", ""))),
                    "kelurahan_bpkb": consumer.subdistrict or placeholders.get("kelurahan_bpkb", ""),
                    "kecamatan_bpkb": consumer.district or placeholders.get("kecamatan_bpkb", ""),
                    "provinsi_bpkb": consumer.province or placeholders.get("provinsi_bpkb", ""),
                    "kode_pos_bpkb": consumer.postal_code or placeholders.get("kode_pos_bpkb", ""),
                    "ktp_bpkb": consumer.bpkb_ktp or consumer.new_bpkb_ktp or "",
                })

            if is_provider_bpkb:
                _mirror_bpkb_to_debitur(placeholders)

        elif customer_type == CustomerType.COMPANY:
            placeholders.update({
                "kewarganegaraan_pj_badan": ((get_nationality_full(consumer.director_nationality or 'Warga Negara Indonesia') or 'Warga Negara Indonesia').strip()),
                "tempat_lahir_pj_badan": getattr(consumer, 'director_birth_place', '') or '',
                "tgl_lahir_pj_badan": consumer.director_birth_date.strftime('%d-%m-%Y') if getattr(consumer, 'director_birth_date', None) else '',
                "terbilang_tgl_lahir_pj_badan": terbilang_tanggal_lahir(consumer.director_birth_date.strftime('%d-%m-%Y')) if consumer.director_birth_date else '-',
                "pekerjaan_pj_badan": consumer.director_occupation or '',
                "kota_pj_badan": consumer.director_city or '',
                "kabupaten_pj_badan": getattr(consumer, 'director_city', '') or '',
                "alamat_pj_badan": consumer.director_address or '',
                'rt_pj_badan': (consumer.director_rt_rw or '').replace(' ', '').split('/')[0] if getattr(consumer, 'director_rt_rw', None) else '',
                'rw_pj_badan': (consumer.director_rt_rw or '').replace(' ', '').split('/')[1] if getattr(consumer, 'director_rt_rw', None) and '/' in (consumer.director_rt_rw or '') else '',
                'kelurahan_pj_badan': consumer.director_subdistrict or '',
                'kecamatan_pj_badan': consumer.director_district or '',
                'provinsi_pj_badan': consumer.director_province or '',
                "ktp_pj_badan": getattr(consumer, 'director_id_number', '') or '',
                "nama_pj_badan": getattr(consumer, 'company_director_name', '') or '',
                "jabatan_pj_badan": getattr(consumer, 'director_position', '') or '',
                "nama_bpkb": getattr(consumer, 'name_on_bpkb', '') or '',
                "kabupaten_bpkb": consumer.bpkb_city or "",
                "no_akta_badan": consumer.deed_number or '',
                "tanggal_akta_badan": consumer.deed_date.strftime('%d-%m-%Y') if getattr(consumer, 'deed_date', None) else '',
                "terbilang_tanggal_akta_badan": terbilang_tanggal_lahir( consumer.deed_date.strftime('%d-%m-%Y') ) if consumer.deed_date else '-',
                "notaris_pj_badan": consumer.director_notary_name,
                "notaris_badan_city": consumer.notary_city or '',
                "nama_pj": consumer.company_director_name or '',
                "jabatan_pj": consumer.director_position or '',
                "tgl_akta_badan": consumer.deed_date.strftime('%d-%m-%Y') if consumer.deed_date else '',
                "terbilang_akta_badan": tanggal_terbilang(consumer.deed_date) if consumer.deed_date else '-',
                "nama_pada_bpkb": getattr(consumer, "name_on_bpkb", None) or getattr(consumer, "new_name_on_bpkb", None) or "",
                "nama_pada_bpkb_new": getattr(consumer, "new_name_on_bpkb", None) or getattr(consumer, "name_on_bpkb", None) or "",
                "debitur_kewarganegaraan": (get_nationality_full(((getattr(consumer, 'nationality', None) or 'Warga Negara Indonesia').strip()))or 'Warga Negara Indonesia'),
                "tempat_lahir_debitur": getattr(consumer, 'birth_place', '') or '',
                "tgl_lahir_debitur": consumer.birth_date.strftime('%d-%m-%Y') if getattr(consumer, 'birth_date', None) else '-',
                "terbilang_tgl_lahir_debitur": terbilang_tanggal_lahir(consumer.birth_date.strftime('%d-%m-%Y')) if getattr(consumer, 'birth_date', None) else '-',
                "pekerjaan_debitur": getattr(consumer, 'occupation', '') or '',
                "kota_debitur": getattr(consumer, 'city', '') or '',
                "alamat_debitur": getattr(consumer, 'address', '') or '',
                "alamat_debitur_new": getattr(consumer, 'new_address', '') or getattr(consumer, 'address', '') or '',
                "rt_debitur": getattr(consumer, 'rt_rw', '') or '',
                "rt_debitur_new": ((consumer.new_rt_rw or '').replace(' ', '').split('/')[0] if getattr(consumer, 'new_rt_rw', None) else ((consumer.rt_rw or '').replace(' ', '').split('/')[0] if getattr(consumer, 'rt_rw', None) else placeholders.get("rt_rw", ""))),
                "rw_debitur": getattr(consumer, 'rw', '') or '',
                "rw_debitur_new": ((consumer.new_rt_rw or '').replace(' ', '').split('/')[1] if getattr(consumer, 'new_rt_rw', None) and '/' in (consumer.new_rt_rw or '') else ((consumer.rt_rw or '').replace(' ', '').split('/')[1] if getattr(consumer, 'rt_rw', None) and '/' in (consumer.rt_rw or '') else "-")),
                "kelurahan_debitur": getattr(consumer, 'subdistrict', '') or '',
                "kelurahan_debitur_new": getattr(consumer, 'new_subdistrict', '') or getattr(consumer, 'subdistrict', '') or '',
                "kecamatan_debitur": getattr(consumer, 'district', '') or '',
                "kecamatan_debitur_new": getattr(consumer, 'new_district', '') or getattr(consumer, 'district', '') or '',
                "provinsi_debitur": getattr(consumer, 'province', '') or '',
                "provinsi_debitur_new": getattr(consumer, 'new_province', '') or getattr(consumer, 'province', '') or '',
                "kode_pos_debitur": getattr(consumer, 'postal_code', '') or '',
                "kode_pos_debitur_new": getattr(consumer, 'new_postal_code', '') or getattr(consumer, 'postal_code', '') or '',
                "kota_debitur_new": getattr(consumer, 'new_city', '') or getattr(consumer, 'city', '') or '',
            })

            placeholders.update({
                "kewarganegaraan_bpkb": ( get_nationality_full((consumer.bpkb_nationality or 'Warga Negara Indonesia').strip()) or "Warga Negara Indonesia"),
                "kewarganegaraan_bpkb_new": ( get_nationality_full((consumer.new_bpkb_nationality or 'Warga Negara Indonesia').strip()) or "Warga Negara Indonesia"),
                "tempat_lahir_bpkb": consumer.bpkb_birth_place or "-",
                "tempat_lahir_bpkb_new": consumer.new_bpkb_birth_place or "-",
                "tgl_lahir_bpkb": consumer.bpkb_birth_date.strftime("%d-%m-%Y") if consumer.bpkb_birth_date else "-",
                "tgl_lahir_bpkb_new": consumer.new_bpkb_birth_date.strftime("%d-%m-%Y") if consumer.new_bpkb_birth_date else "-",
                "terbilang_tgl_lahir_bpkb": terbilang_tanggal_lahir(consumer.bpkb_birth_date.strftime("%d-%m-%Y")) if consumer.bpkb_birth_date else "-",
                "terbilang_tgl_lahir_bpkb_new": terbilang_tanggal_lahir(consumer.new_bpkb_birth_date.strftime("%d-%m-%Y")) if consumer.new_bpkb_birth_date else "-",
                "pekerjaan_bpkb": consumer.bpkb_occupation or "",
                "pekerjaan_bpkb_new": consumer.new_bpkb_occupation or "-",
                "kabupaten_bpkb_new": consumer.new_bpkb_city or "-",
                "alamat_bpkb": consumer.bpkb_address or "",
                "alamat_bpkb_new": consumer.new_bpkb_address or "-",
                "rt_bpkb": consumer.bpkb_rt or "",
                "rt_bpkb_new": consumer.new_bpkb_rt or "-",
                "rw_bpkb": consumer.bpkb_rw or "",
                "rw_bpkb_new": consumer.new_bpkb_rw or "-",
                "kelurahan_bpkb": consumer.bpkb_subdistrict or "",
                "kelurahan_bpkb_new": consumer.new_bpkb_subdistrict or "-",
                "kecamatan_bpkb": consumer.bpkb_district or "",
                "kecamatan_bpkb_new": consumer.new_bpkb_district or "-",
                "provinsi_bpkb": consumer.bpkb_province or "",
                "provinsi_bpkb_new": consumer.new_bpkb_province or "-",
                "kode_pos_bpkb": consumer.bpkb_postal_code or "",
                "kode_pos_bpkb_new": consumer.new_bpkb_postal_code or "-",
                "ktp_bpkb": consumer.bpkb_ktp or "",
                "ktp_bpkb_new": consumer.new_bpkb_ktp or "-",
            })

            placeholders.update({
                "qty_kendaraan": consumer.unit_quantity or 1,
                "terbilang_kendaraan": terbilang_unit(consumer.unit_quantity) if consumer.unit_quantity else 'Satu',
            })

        return placeholders

    def _validate_non_empty(self, data: dict):
        invalid_keys = [key for key, value in data.items() if value in [None, '']]
        if invalid_keys:
            return JsonResponse({
                'success': False,
                'message': 'Gagal menghasilkan dokumen. Mohon lengkapi data yang diperlukan.',
                'invalid_fields': invalid_keys   # tampilkan field invalid di sini
            }, status=400)
        return None

    def _build_response(self, doc, filename_prefix: str, name_suffix: str):
        from io import BytesIO
        stream = BytesIO()
        doc.save(stream)
        stream.seek(0)
        response = FileResponse(
            stream,
            as_attachment=True,
            filename=filename_prefix + name_suffix,
            content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename_prefix}{name_suffix}"'
        return response

    def _build_placeholders(self, admin, notary_instance, consumer, recipient_info, nomor_akta, document_date, document_time, akta_type, vehicles, jenis_doc, customer_type, legal_doc):
        """
        Membuat dictionary placeholders dokumen akta.
        Return: (placeholders, error)
        """
        admin_birth = self._admin_birth_info(admin)
        witness0, witness1, witnesses_birth = self._witnesses_info(notary_instance)
        time_info = self._current_time_info(document_date, document_time)

        format_hutang_pokok = format_rupiah(consumer.principal_loan)
        format_penjaminan = format_rupiah(consumer.guarantee_value)
        format_otr = format_rupiah(consumer.otr_value)
        notary_city_name = consumer.notary_city.splitlines()[0].replace('\\r\\n', '') if consumer.notary_city else ""

        placeholders = {
            # Recipient / SK
            "tgl_sk": recipient_info["recipient_date"],
            "terbilang_tgl_sk": recipient_info["recipient_terbilang"],
            "sk": recipient_info["recipient_sk"],
            "zona_notaris": notary_instance.zone if notary_instance and notary_instance.zone else "",
            "terbilang_zona_notaris": get_time_zone_full(notary_instance.zone) if notary_instance and notary_instance.zone else "-",

            # Admin temporary location and branch province
            # "user_sementara": getattr(admin, "residence", ""),
            "pn_cabang": recipient_info["recipient_pn"],

            # Witness names
            "saksi0_nama_tanpa": witness0.full_name if witness0 else "",
            "saksi1_nama_tanpa": witness1.full_name if witness1 else "",

            # Terbilang numbers and date
            "terbilang_tgl_perjanjian": terbilang_tanggal_lahir(consumer.agreement_date.strftime("%d-%m-%Y")) if consumer.agreement_date else "-",
            "terbilang_hutang_pokok": terbilang_angka(consumer.principal_loan),
            "terbilang_penjaminan": terbilang_angka(consumer.guarantee_value),
            "terbilang_otr": terbilang_angka(consumer.otr_value),

            # Today
            "tgl_hari_ini": time_info["tgl_hari_ini_str"],
            "hari_ini": time_info["hari_ini"],
            "terbilang_hari_ini": time_info["terbilang_hari_ini"],

            # Notary
            "notary_name": notary_instance.full_name if notary_instance else "",
            "notary_city": notary_instance.city_name if notary_instance else notary_city_name,
            "notaris_gelar_panjang": notary_instance.full_title if notary_instance else "",
            "notaris_gelar_pendek": notary_instance.short_title if notary_instance else "",
            "user_sementara": notary_instance.city_name if notary_instance else "",

            # Admin / penerima kuasa
            "user_name": admin.full_name,
            "penerima_kuasa_gelar_panjang": admin.full_title,
            "penerima_kuasa_gelar_pendek": admin.short_title,
            "user_tempat_lahir": admin_birth.user_tempat_lahir,
            "user_tanggal_lahir": tanggal_indo(admin_birth.user_tanggal_lahir),
            "user_terbilang_ttl": admin_birth.user_terbilang_ttl,
            "user_province": admin.residence,
            "user_pekerjaan": admin.job,
            "user_alamat": admin.address,
            "user_rt": admin.rt,
            "user_rw": admin.rw,
            "user_kelurahan": admin.subdistrict,
            "user_kecamatan": admin.district,
            "user_city": admin.province.region_name if admin.province else None,

            # Consumer
            "tgl_perjanjian": consumer.agreement_date.strftime("%d-%m-%Y") if getattr(consumer, 'agreement_date', None) else "",
            "alamat_cabang": recipient_info["recipient_full_address"],
            "nama_debitur": consumer.debtor_name or "",
            "ktp_debitur": consumer.id_number or "-",
            "no_kontrak": consumer.agreement_number or "",
            "hutang_pokok": format_hutang_pokok,
            "nilai_penjaminan": format_penjaminan,
            "nilai_otr": format_otr,

            # Witness 0
            "saksi0_nama": witness0.full_name if witness0 else "",
            "saksi0_tempat_lahir": witnesses_birth["saksi0_tempat_lahir"],
            "saksi0_tgl_lahir": witnesses_birth["saksi0_tgl_lahir"],
            "saksi0_terbilang_ttl": witnesses_birth["saksi0_terbilang_ttl"],
            "saksi0_tempat_tinggal": witness0.residence if witness0 else "",
            "saksi0_rt": witness0.rt if witness0 else "",
            "saksi0_rw": witness0.rw if witness0 else "",
            "saksi0_kelurahan": witness0.subdistrict if witness0 else "",
            "saksi0_kecamatan": witness0.district if witness0 else "",
            "saksi0_province": get_region_name_or_placeholder(getattr(witness0, "province_id", None)),
            "saksi0_ktp": witness0.national_id if witness0 else "",

            # Witness 1
            "saksi1_nama": witness1.full_name if witness1 else "",
            "saksi1_tempat_lahir": witnesses_birth["saksi1_tempat_lahir"],
            "saksi1_tgl_lahir": witnesses_birth["saksi1_tgl_lahir"],
            "saksi1_terbilang_ttl": witnesses_birth["saksi1_terbilang_ttl"],
            "saksi1_tempat_tinggal": witness1.residence if witness1 else "",
            "saksi1_rt": witness1.rt if witness1 else "",
            "saksi1_rw": witness1.rw if witness1 else "",
            "saksi1_kelurahan": witness1.subdistrict if witness1 else "",
            "saksi1_kecamatan": witness1.district if witness1 else "",
            "saksi1_province": get_region_name_or_placeholder(getattr(witness1, "province_id", None)),
            "saksi1_ktp": witness1.national_id if witness1 else "",

            # Time
            "pukul_akta": time_info["pukul_akta"],
            "terbilang_pukul": time_info["terbilang_pukul"],

            # Others
            "no_akta": nomor_akta,
            "user_ktp": admin.national_id if admin.national_id else "",
        }

        # Vehicle placeholders
        vehicle_placeholders, error = self._vehicle_placeholders(akta_type, consumer, vehicles)
        if error:
            return None, error
        placeholders.update(vehicle_placeholders)

        # Customer-specific placeholders
        placeholders.update(self._customer_placeholders(jenis_doc, customer_type, consumer, akta_type))

        # Mizuho: tanggal kuasa, pasangan, nama BPKB, no. polisi (template reguler personal - customer)
        if akta_type == AktaType.REGULER:
            placeholders.update(mizuho_deed_placeholders(consumer))

        # Validate
        error = self._validate_non_empty(placeholders)
        if error:
            return None, error

        return placeholders, None

    def _build_placeholders_pf(self, admin, notary_instance, consumer, recipient_info, nomor_akta, document_date, document_time, jenis_doc, customer_type):

        consumer_reguler = None
        legal_doc_reguler = None

        try:
            agreement_number_key = (
                getattr(consumer, "new_agreement_number", None)
                or getattr(consumer, "agreement_number", None)
                or ""
            )

            if agreement_number_key:
                RegConsumer = get_consumer_model(AktaType.REGULER)
                RegDoc     = get_document_model(AktaType.REGULER)

                consumer_reguler = RegConsumer.objects.filter(agreement_number=agreement_number_key).first()
                if consumer_reguler:
                    legal_doc_reguler = RegDoc.objects.filter(consumer=consumer_reguler).first()

        except Exception as e:
            print(f"[PF] Gagal ambil data reguler untuk agreement_number={agreement_number_key}: {e}")

        admin_birth = self._admin_birth_info(admin)
        witness0, witness1, witnesses_birth = self._witnesses_info(notary_instance)
        time_info = self._current_time_info(document_date, document_time)

        def fmt_tgl(dt):
            return dt.strftime("%d-%m-%Y") if dt else ""

        def fmt_terbilang_tgl(dt):
            return terbilang_tanggal_lahir(dt.strftime("%d-%m-%Y")) if dt else ""

        no_akta_from_reg = getattr(legal_doc_reguler, "document_number", None) if legal_doc_reguler else None
        no_akta_pf = (
            getattr(consumer, "new_deed_number", None)
            or getattr(consumer, "deed_number", None)
            or ""
        )
        no_akta_final = no_akta_from_reg or no_akta_pf

        tgl_akta_from_reg = getattr(legal_doc_reguler, "document_date", None) if legal_doc_reguler else None
        tgl_akta_pf = getattr(consumer, "new_deed_date", None) or getattr(consumer, "deed_date", None)
        tgl_akta_final = tgl_akta_from_reg or tgl_akta_pf

        agreement_date_use = getattr(consumer, "agreement_date", None) or (getattr(consumer_reguler, "agreement_date", None) if consumer_reguler else None)

        placeholders = {
            "no_akta_new": nomor_akta,
            "hari_ini": time_info["hari_ini"],
            "tgl_hari_ini": time_info["tgl_hari_ini_str"],
            "terbilang_hari_ini": time_info["terbilang_hari_ini"],
            "pukul_akta_new": time_info["pukul_akta"],
            "zona_notaris": notary_instance.zone if notary_instance and notary_instance.zone else "",
            "terbilang_pukul": time_info["terbilang_pukul"],
            "terbilang_zona_notaris": get_time_zone_full(notary_instance.zone) if notary_instance and notary_instance.zone else "-",
            "notary_name_new": notary_instance.full_name if notary_instance else "",
            "notaris_gelar_panjang_new": notary_instance.full_title if notary_instance else "",
            "notaris_gelar_panjang": notary_instance.full_title if notary_instance else "",
            "notary_city_new": notary_instance.city_name if notary_instance else "",
            "notary_city": notary_instance.city_name if notary_instance else "",
            "user_name_new": admin.full_name if admin else "",
            "penerima_kuasa_gelar_panjang_new": admin.full_title if admin else "",
            "user_tempat_lahir_new": admin_birth.user_tempat_lahir,
            "user_tanggal_lahir_new": admin_birth.user_tanggal_lahir,
            "user_terbilang_ttl_new": admin_birth.user_terbilang_ttl,
            "user_pekerjaan_new": admin.job,
            "user_city_new": admin.residence if admin else "",
            "user_alamat_new": admin.address if admin else "",
            "user_rt_new": admin.rt if admin else "",
            "user_rw_new": admin.rw if admin else "",
            "user_kelurahan_new": admin.subdistrict if admin else "",
            "user_kecamatan_new": admin.district if admin else "",
            "user_province_new": admin.province.region_name if admin and getattr(admin, "province", None) else "",
            "user_ktp_new": admin.national_id if admin and admin.national_id else "",
            "user_sementara_new": notary_instance.city_name if notary_instance else "",
            "alamat_pemberi_fidusia": recipient_info["recipient_full_address"],
            "tgl_sk_new": recipient_info["recipient_date"],
            "alamat_penerima_fidusia": recipient_info["recipient_full_address"],
            "terbilang_tgl_sk_new": recipient_info["recipient_terbilang"],
            "sk_new": recipient_info["recipient_sk"],
            "tgl_perjanjian": fmt_tgl(agreement_date_use),
            "terbilang_tgl_perjanjian": fmt_terbilang_tgl(agreement_date_use),
            "no_akta": no_akta_final,
            "tanggal_akta": fmt_tgl(tgl_akta_final),
            "terbilang_tanggal_akta": fmt_terbilang_tgl(tgl_akta_final),
            "no_kontrak": (getattr(consumer, "new_agreement_number", None)or getattr(consumer, "agreement_number", None)or ""),
            "no_surat_perubahan": getattr(consumer, "fiduciary_change_letter_number", "") or "",
            "tanggal_surat_perubahan": fmt_tgl(getattr(consumer, "date_of_change_of_fiduciary_letter", None)),
            "terbilang_tanggal_surat_perubahan": fmt_terbilang_tgl(getattr(consumer, "date_of_change_of_fiduciary_letter", None)),
            "merk": getattr(consumer, "brand", None) or getattr(consumer, "new_brand", None) or "",
            "type": getattr(consumer, "type", None) or getattr(consumer, "new_type", None) or "",
            "no_rangka": getattr(consumer, "chassis_number", None) or getattr(consumer, "new_chassis_number", None) or "",
            "no_mesin": getattr(consumer, "engine_number", None) or getattr(consumer, "new_engine_number", None) or "",
            "warna": getattr(consumer, "color", None) or getattr(consumer, "new_color", None) or "",
            "tahun": str(getattr(consumer, "production_year", "") or ""),
            "debitur_name": consumer.debtor_name or "",
            "debitur_ktp": consumer.id_number or "-",
            "nilai_otr": format_rupiah(getattr(consumer, "otr_value", None)),
            "terbilang_otr": terbilang_angka(consumer.otr_value),
            "merk_new": getattr(consumer, "new_brand", None) or getattr(consumer, "brand", None) or "",
            "type_new": getattr(consumer, "new_type", None) or getattr(consumer, "type", None) or "",
            "no_rangka_new": getattr(consumer, "new_chassis_number", None) or getattr(consumer, "chassis_number", None) or "",
            "no_mesin_new": getattr(consumer, "new_engine_number", None) or getattr(consumer, "engine_number", None) or "",
            "warna_new": getattr(consumer, "new_color", None) or getattr(consumer, "color", None) or "",
            "tahun_new": str(getattr(consumer, "production_year", "") or ""),
            "nilai_otr_new": format_rupiah(getattr(consumer, "new_otr_values", None) or getattr(consumer, "otr_values", None)),
            "terbilang_otr_new": terbilang_angka(getattr(consumer, "new_otr_values", None) or getattr(consumer, "otr_values", None)) or "-",
            "saksi0_nama_new": witness0.full_name if witness0 else "",
            "saksi0_tempat_lahir_new": witnesses_birth["saksi0_tempat_lahir"],
            "saksi0_tgl_lahir_new": tanggal_indo(witnesses_birth["saksi0_tgl_lahir"]),
            "saksi0_terbilang_ttl_new": witnesses_birth["saksi0_terbilang_ttl"],
            "saksi0_pekerjaan": witness0.occupation if witness0 and witness0.occupation else "",
            "saksi0_tempat_tinggal_new": witness0.residence if witness0 else "",
            "saksi0_rt_new": witness0.rt if witness0 else "",
            "saksi0_rw_new": witness0.rw if witness0 else "",
            "saksi0_kelurahan_new": witness0.subdistrict if witness0 else "",
            "saksi0_kecamatan_new": witness0.district if witness0 else "",
            "saksi0_province_new": get_region_name_or_placeholder(getattr(witness0, "province_id", None)),
            "saksi0_ktp_new": witness0.national_id if witness0 else "",
            "saksi1_nama_new": witness1.full_name if witness1 else "",
            "saksi1_tempat_lahir_new": witnesses_birth["saksi1_tempat_lahir"],
            "saksi1_tgl_lahir_new": tanggal_indo(witnesses_birth["saksi1_tgl_lahir"]),
            "saksi1_terbilang_ttl_new": witnesses_birth["saksi1_terbilang_ttl"],
            "saksi1_pekerjaan": witness1.occupation if witness1 and witness1.occupation else "",
            "saksi1_tempat_tinggal_new": witness1.residence if witness1 else "",
            "saksi1_rt_new": witness1.rt if witness1 else "",
            "saksi1_rw_new": witness1.rw if witness1 else "",
            "saksi1_kelurahan_new": witness1.subdistrict if witness1 else "",
            "saksi1_kecamatan_new": witness1.district if witness1 else "",
            "saksi1_province_new": get_region_name_or_placeholder(getattr(witness1, "province_id", None)),
            "saksi1_ktp_new": witness1.national_id if witness1 else "",
            "penerima_kuasa_gelar_pendek_new": admin.short_title if admin else "",
            "saksi0_nama_tanpa_new": witness0.full_name if witness0 else "",
            "saksi1_nama_tanpa_new": witness1.full_name if witness1 else "",
            "notaris_gelar_pendek_new": notary_instance.short_title if notary_instance else "",
            "notary_name": notary_instance.full_name if notary_instance else "",
            "notaris_gelar_pendek": notary_instance.short_title if notary_instance else "",
            "nama_debitur": getattr(consumer, "debtor_name", None) or getattr(consumer, "debtor_name", None) or "",
            "ktp_debitur": getattr(consumer, "id_number", None) or getattr(consumer, "id_number", None) or "",
            "nama_debitur_new": getattr(consumer, "new_debtor_name", None) or getattr(consumer, "debtor_name", None) or "",
            "ktp_debitur_new": getattr(consumer, "new_id_number", None) or getattr(consumer, "id_number", None) or "",
        }

        # Customer-specific placeholders
        placeholders.update(self._customer_placeholders_pf(jenis_doc, customer_type, consumer))
        # Validate
        error = self._validate_non_empty(placeholders)
        if error:
            return None, error

        return placeholders, None

    # ========================= Endpoint ========================= #
    def get(self, request, akta_type: AktaType, id: int, doc_type: DocType):
        try:
            required_permission = self._permission_for_akta(akta_type)
            if not user_has_permission(request.user, required_permission):
                return JsonResponse({'success': False, 'message': 'Forbidden'}, status=403)

            consumer, legal_doc, vehicles = self._get_models_and_instances(akta_type, id)
            if not self._can_access_consumer(request.user, consumer):
                return JsonResponse({'success': False, 'message': 'Forbidden'}, status=403)
            nomor_akta = legal_doc.document_number
            document_time = legal_doc.document_time
            document_date = legal_doc.document_date

            recipient_info, error = self._get_recipient_info(document_date, legal_doc, consumer)
            if error:
                return error

            # === GANTI: admin harus dari power_user ===
            admin = getattr(legal_doc, "power_user", None)
            if admin is None:
                return JsonResponse({'success': False,'message': 'Penerima kuasa belum diisi pada dokumen ini.'},status=400)

            notary_instance, error = self._resolve_notary(legal_doc)
            if error:
                return error
            legal_doc.notary = notary_instance

            is_pf_subject = False
            if akta_type == AktaType.PF:
                cc = self._norm(getattr(consumer, 'choose_change', None))
                if cc in ('perubahan1', 'perubahan2'):
                    is_pf_subject = True

            customer_type, jenis_doc, template_path, error = self._determine_types_and_template(
                consumer, akta_type, doc_type
            )
            if error:
                return error

            doc = DocxTemplate(template_path)
            if not doc:
                return JsonResponse({'success': False, 'message': f'Template file tidak ditemukan: {template_path}'}, status=404)

            if akta_type == AktaType.PF:
                placeholders, error = self._build_placeholders_pf(
                    admin, notary_instance, consumer, recipient_info,
                    nomor_akta, document_date, document_time, jenis_doc, customer_type
                )
                if error:
                    return error
            else:
                placeholders, error = self._build_placeholders(
                    admin, notary_instance, consumer, recipient_info, nomor_akta,
                    document_date, document_time, akta_type, vehicles, jenis_doc, customer_type, legal_doc
                )
                if error:
                    return error

            # (opsional) Rapikan: jangan double-format tanggal lahir admin
            placeholders["user_tanggal_lahir"] = self._admin_birth_info(admin).user_tanggal_lahir

            doc.render(placeholders)
            name_prefix = '_SALINAN AKTA FIDUCIA.docx' if doc_type == DocType.SALINAN else '_AKTA FIDUCIA.docx'
            name_suffix = f"{consumer.agreement_number}"
            return self._build_response(doc, name_suffix, name_prefix)

        except Http404:
            return JsonResponse({
                'success': False,
                'message': 'Data tidak ditemukan.'
            }, status=404)
        except (ValueError, TypeError) as e:
            return JsonResponse({
                'success': False,
                'message': f'Kesalahan dalam pengolahan data: {str(e)}'
            }, status=400)
        except Exception as e:
            print(f"[ERROR] Unexpected error: {str(e)}")
            return JsonResponse({
                'success': False,
                'message': f'Kesalahan tidak terduga: {str(e)}'
            }, status=500)

@login_required(login_url='')
@permission_required('read-akta')
@require_GET
def check_akta_download_status(request, scope=None):
    agreement_number = request.GET.get('agreement_number')
    if not agreement_number:
        return JsonResponse({
            'success': False,
            'message': 'agreement_number is required'
        }, status=400)

    akta_type_q = request.GET.get('akta_type')

    if not akta_type_q:
        s = (scope or '').lower()
        if s in AktaType.values:
            akta_type_q = s
        else:
            akta_type_q = AktaType.REGULER

    akta_type_q = str(akta_type_q).lower()

    LogModel = get_log_model(akta_type_q)

    db_alias = request.GET.get('db')
    qs = LogModel.objects.using(db_alias) if db_alias else LogModel.objects

    exists = qs.filter(agreement_number=agreement_number).exists()

    return JsonResponse({
        'success': True,
        'already_downloaded': exists,
        'akta_type': akta_type_q,
        'scope': scope or 'reguler'
    })

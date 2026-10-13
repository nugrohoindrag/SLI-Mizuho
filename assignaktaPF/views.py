from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from AdminRecipients.models import Recipient
from import_cabang.models import Branch, NotaryBranchArea
from change_fiduciary.models import change_of_fiduciary, ActivityLogPf
from .serializers import ConsumerSerializerPF
from assignaktaPF.models import LegalDocumentPF
from AdminNotaries.models import Notary
from AdminProxies.models import AdminProxy
from assignakta.models import Witness
from django.shortcuts import get_object_or_404, redirect, render
from AdminMenu.auth_helpers import class_permission_required, get_user_permissions, get_user_role_names, permission_required
from common.const.role_map import has_role, ROLE_KEYS
from AdminUser.branch_access import get_primary_user_branch_code, get_user_branch_codes, user_is_branch_restricted
from common.util.id_formatters import safe_split_birth_info, tanggal_terbilang, terbilang_tanggal_lahir, format_rupiah, \
    get_region_name_or_placeholder, terbilang_angka, get_nationality_full, tanggal_indo
from rest_framework.permissions import IsAuthenticated
from django.http import JsonResponse, HttpResponse
from rest_framework.authentication import SessionAuthentication
from django.forms.models import model_to_dict
from datetime import datetime, time, timedelta, date
import openpyxl, io, os, glob, re, html
from django.utils.timezone import localdate, make_aware, get_current_timezone, now
from django.db.models.deletion import ProtectedError
from docxtpl import DocxTemplate
from django.conf import settings
from num2words import num2words
from django.views.decorators.csrf import csrf_exempt
from django.db import IntegrityError, connection, transaction
from django.db.models import Q, Subquery, Prefetch, OuterRef, F, Count
from rest_framework.decorators import api_view, permission_classes
from django.db.models.functions import TruncDate
from collections import Counter
import pandas as pd
from django.views.decorators.http import require_GET
from django.core.exceptions import FieldDoesNotExist, FieldError
from common.const.id_calendar import BULAN_ID, HARI_ID
from io import BytesIO
from openpyxl import load_workbook
from openpyxl.styles import Font, Border, Alignment
from openpyxl.utils import get_column_letter
from django.contrib.auth.mixins import LoginRequiredMixin

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

from django.contrib.auth.decorators import login_required
from django.views import View

@login_required(login_url="")
@permission_required("read-akta-pf")
def get_notaris_list_pf(request):
    notaris = Notary.objects.filter(is_active=1).values('id', 'firm_name')
    return JsonResponse(list(notaris), safe=False)

def _normalize_name(name: str) -> str:
    """Ambil inti nama: sebelum koma, buang . dan ,, rapikan spasi, uppercase."""
    if not name:
        return ''
    head = name.split(',')[0]
    head = re.sub(r'[.,]', '', head)
    return ' '.join(head.split()).upper()

def _eligible_consumers_qs():
    # st_process & is_st_used sesuai logic Anda
    return change_of_fiduciary.objects.filter(st_process='0', is_st_used=1)

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

    if _consumer_has_field(change_of_fiduciary, 'branch'):
        # Consumer punya FK 'branch' → gunakan branch_id__in
        qs = _eligible_consumers_qs().filter(branch_id__in=Subquery(branch_ids_sq))
    elif _consumer_has_field(change_of_fiduciary, 'branch_code'):
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

class AssignGenerateAPIViewPF(APIView):
    permission_classes = [IsAuthenticated]

    # =========================
    # PREVIEW: GET
    # =========================
    @class_permission_required('read-akta-pf')
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

        docs_qs = LegalDocumentPF.objects.select_related('consumer', 'notary').filter(
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
    @class_permission_required('create-akta-pf')
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
            return Response({'error': 'Tidak ada Consumer (st_process=0 & is_st_used=1) yang cocok.'}, status=400)

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

        success, failed = [], []
        per_poa_counter = {}

        for idx, cons in enumerate(consumers):
            try:
                with transaction.atomic():
                    nomor = nomor_range[idx]

                    # 10 data/menit; batch berikutnya +2 menit
                    batch_index   = idx // 10
                    minute_offset = batch_index * 1
                    doc_dt        = base_dt + timedelta(minutes=minute_offset)

                    # === ALOKASI ADIL PER ADMINPROXY (10/10, 10/11, dst) ===
                    current_poa = None
                    power_receiver_name_val = None
                    if poa_list and assigned_poa_for_idx:
                        poa_index = assigned_poa_for_idx[idx]
                        current_poa = poa_list[poa_index]
                        power_receiver_name_val = current_poa.full_name
                        per_poa_counter[current_poa.id] = per_poa_counter.get(current_poa.id, 0) + 1

                    legal_doc = LegalDocumentPF.objects.create(
                        consumer=cons,
                        notary=notaris,
                        document_number=nomor,
                        document_date=doc_date,
                        document_time=doc_dt.time(),
                        recipient=recipient,
                        power_receiver_name=power_receiver_name_val,
                        power_user=current_poa if current_poa else None,
                    )

                    cons.st_process = '1'
                    cons.save(update_fields=['st_process'])

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
    
# class LegalDocumentTodayAPIViewPF(APIView):
#     permission_classes = [IsAuthenticated]

#     @class_permission_required('read-akta-pf')
#     def get(self, request, notary_id):
#         date_str = request.query_params.get('mock_date')

#         if date_str:
#             try:
#                 filter_date = datetime.strptime(date_str, "%Y-%m-%d").date()
#             except ValueError:
#                 return Response({'error': 'Format tanggal salah. Gunakan YYYY-MM-DD.'}, status=400)
#         else:
#             latest_doc = LegalDocumentPF.objects.filter(
#                 notary_id=notary_id,
#                 created_at__isnull=False
#             ).order_by('-created_at').first()

#             if not latest_doc:
#                 return Response({'message': 'Tidak ada dokumen untuk notaris ini.'}, status=404)

#             filter_date = latest_doc.created_at.date()

#         start_datetime = datetime.combine(filter_date, time.min)
#         end_datetime = datetime.combine(filter_date, time.max)

#         docs = LegalDocumentPF.objects.select_related('consumer', 'notary').filter(
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

class LegalDocumentUpsertAPIViewPF(APIView):
    permission_classes = [IsAuthenticated]

    @class_permission_required('create-akta-pf')
    def post(self, request):
        legal_doc_id = request.data.get('id')  # pastikan dikirim dari JS
        document_number = request.data.get('document_number')
        document_time = request.data.get('document_time')  # format "HH:MM"

        if not legal_doc_id:
            return Response({'error': 'ID dokumen tidak ditemukan.'}, status=400)

        try:
            doc = LegalDocumentPF.objects.get(id=legal_doc_id)

            # Update nomor dan waktu
            doc.document_number = document_number

            if document_time:
                hour, minute = map(int, document_time.split(':'))
                doc.document_time = time(hour=hour, minute=minute)

            doc.save()

            return Response({'message': 'LegalDocument berhasil diperbarui'})
        except LegalDocumentPF.DoesNotExist:
            return Response({'error': 'LegalDocument tidak ditemukan'}, status=404)
        except Exception as e:
            return Response({'error': str(e)}, status=500)

class AssignListCreateAPIViewPF(LoginRequiredMixin, View):
    login_url = '/login/'

    @class_permission_required('read-akta-pf')
    def get(self, request):
        page_size = min(int(request.GET.get('initial', 100)), 500)

        latest_doc = (
            LegalDocumentPF.objects
            .filter(consumer_id=OuterRef('pk'))
            .order_by('-document_date', '-id')
        )

        consumers = (
            change_of_fiduciary.objects
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

        return render(request, 'operator/page-operator-assignakta-pf.html', {
            'consumers': consumers,
            'branches': branches,
            'user_permissions': perms_lower,
            'user_roles': roles_lower,
            'is_notaris': 'notaris' in roles_lower,
            'is_warkah': 'warkah' in roles_lower,
            'data_list': consumers.count(),
        })

    @class_permission_required('create-akta-pf')
    def post(self, request):
        data = request.POST.copy()

        for k in ('deed_number', 'deed_date', 'deed_documents'):
            data.pop(k, None)

        serializer = ConsumerSerializerPF(data=data, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return redirect('operator-assign-akta-pf')

        return render(request, 'operator/page-operator-assignakta-pf.html', {
            'errors': serializer.errors
        })

class AssignDataJSONPF(APIView):
    permission_classes = [IsAuthenticated]

    @class_permission_required('read-akta-pf')
    def get(self, request):
        def _to_int(v, d): 
            try: return int(v)
            except: return d

        draw   = _to_int(request.GET.get('draw', 1), 1)
        start  = _to_int(request.GET.get('start', 0), 0)
        length = _to_int(request.GET.get('length', 50), 50)
        length = min(max(length, 1), 500)

        notary_name = (request.GET.get('notary_name') or '').strip()
        mock_date   = request.GET.get('mock_date')

        search_val  = (request.GET.get('search') or '').strip()

        # start_date = request.GET.get('start_date')
        # end_date = request.GET.get('end_date')

        # if not start_date and not end_date:
        #     today_str = date.today().strftime('%Y-%m-%d')
        #     start_date = today_str
        #     end_date = today_str

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
            LegalDocumentPF.objects
            .select_related('consumer', 'notary')
            .filter(consumer__st_process='1')
        )
        
        is_notary = 'notaris' in user_roles or 'notary' in user_roles
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

        # if start_date:
        #     qs_base = qs_base.filter(document_date__gte=start_date)
        # if end_date:
        #     qs_base = qs_base.filter(document_date__lte=end_date)

        if search_val:
            qs_base = qs_base.filter(
                Q(notary__firm_name__icontains=search_val) |
                Q(consumer__agreement_number__icontains=search_val) |
                Q(consumer__debtor_name__icontains=search_val) |
                Q(document_number__icontains=search_val) |
                Q(voucher_code__icontains=search_val) |
                Q(certificate_number__icontains=search_val)
            )

        records_total     = LegalDocumentPF.objects.filter(consumer__st_process='1').count()
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
    
class AssignDetailAPIViewPF(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, pk):
        return get_object_or_404(change_of_fiduciary, pk=pk)

    @class_permission_required('read-akta-pf')
    def get(self, request, pk):
        consumer = self.get_object(pk)
        if not _consumer_belongs_to_user_branches(consumer, request.user):
            return Response({"detail": "Forbidden branch scope."}, status=status.HTTP_403_FORBIDDEN)
        serializer = ConsumerSerializerPF(consumer)
        return Response(serializer.data)

    @class_permission_required('update-akta-pf')
    def put(self, request, pk):
        consumer = self.get_object(pk)
        if not _consumer_belongs_to_user_branches(consumer, request.user):
            return Response({"detail": "Forbidden branch scope."}, status=status.HTTP_403_FORBIDDEN)
        serializer = ConsumerSerializerPF(consumer, data=request.data, context={'request': request}, partial=True)
        if serializer.is_valid():
            updated_consumer = serializer.save()
            return Response(ConsumerSerializerPF(updated_consumer).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @class_permission_required('delete-akta-pf')
    def delete(self, request, pk):
        try:
            consumer = self.get_object(pk)
            if not _consumer_belongs_to_user_branches(consumer, request.user):
                return Response({"detail": "Forbidden branch scope."}, status=status.HTTP_403_FORBIDDEN)
            consumer.delete()

            ActivityLogPf.objects.create(
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
        
class AssignPrintViewPF(APIView):
    permission_classes = [IsAuthenticated]

    nama_hari = {
        0: 'Senin', 1: 'Selasa', 2: 'Rabu',
        3: 'Kamis', 4: 'Jumat', 5: 'Sabtu', 6: 'Minggu'
    }

    bulan_id = [
        "", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
        "Juli", "Agustus", "September", "Oktober", "November", "Desember"
    ]
    
    @class_permission_required('read-akta-pf')
    def get(self, request, pk):
        consumer = get_object_or_404(change_of_fiduciary, pk=pk)
        if not _consumer_belongs_to_user_branches(consumer, request.user):
            return HttpResponse("Forbidden", status=403)
        legal_doc = get_object_or_404(LegalDocumentPF, change_of_fiduciary=consumer)
        document_time = legal_doc.document_time
        admin = AdminProxy.objects.first()

        if isinstance(legal_doc.notary, Notary):
            notary_instance = legal_doc.notary

        elif isinstance(legal_doc.notary, str):
            try:
                notary_instance = Notary.objects.get(firm_name__iexact=legal_doc.notary.strip())
            except Notary.DoesNotExist:
                return JsonResponse({
                    "success": False,
                    "message": f"Notaris dengan nama '{legal_doc.notary}' tidak ditemukan di tabel Notary."
                }, status=404)

        else:
            return JsonResponse({
                "success": False,
                "message": "Data notaris tidak valid atau kosong."
            }, status=400)

        legal_doc.notary = notary_instance

        akta_type = 'Konvensional' if consumer.agreement_type.upper() == 'KONVE' else 'SYARIAH'
        customer_type = 'Personal' if consumer.customer_type.upper() == 'PERSONAL' else 'PT'

        jenis_doc = request.GET.get('doc', 'bpkb')
        is_salinan = request.GET.get('salinan', 'false').lower() == 'true'

        template_file = f"New BPKB - Draft Akta {akta_type} - {customer_type} - {jenis_doc}"
        if is_salinan:
            template_file += " - Salinan"
        template_file += ".docx"

        template_path = os.path.join(settings.BASE_DIR, 'media', 'templatesakta', template_file)

        if not os.path.exists(template_path):
            return HttpResponse(f"Template file not found: {html.escape(template_path)}", status=404, content_type="text/plain; charset=utf-8")

        doc = DocxTemplate(template_path)
        
        document_date = legal_doc.document_date
        birth_place, birth_date_str = self.safe_split_birth_info(getattr(admin, 'birth_info', ''))
        birth_date = datetime.strptime(birth_date_str, '%d-%m-%Y') if birth_date_str else None

        user_tempat_lahir = birth_place.strip()
        user_tanggal_lahir = birth_date.strftime('%d %B %Y') if birth_date else ''
        user_terbilang_ttl = f"{num2words(birth_date.day, lang='id')} {birth_date.strftime('%B')} {num2words(birth_date.year, lang='id')}" if birth_date else ''
    
        print("DEBUG: legal_doc.notary =", repr(legal_doc.notary))
        print("DEBUG: notary_instance =", repr(notary_instance))
        print("DEBUG: type legal_doc.notary =", type(legal_doc.notary))
        print("DEBUG: type notary_instance =", type(notary_instance))

        witnesses = Witness.objects.filter(notary_id=notary_instance.id)
        witness0 = witnesses[0] if len(witnesses) > 0 else None
        witness1 = witnesses[1] if len(witnesses) > 1 else None
        
        tgl_hari_ini = document_date.strftime('%d-%m-%Y') if document_date else ''
        hari_ini = self.nama_hari[document_date.weekday()] if document_date else ''
        terbilang_hari_ini = self.tanggal_terbilang(document_date) if document_date else ''
        pukul_akta = document_time.strftime('%H:%M') if document_time else ''
        
        saksi0_tempat_lahir, saksi0_tgl_lahir = '', ''
        saksi0_terbilang_ttl = ''
        if witness0:
            saksi0_tempat_lahir, saksi0_tgl_lahir = self.safe_split_birth_info(witness0.birth_info)
            saksi0_terbilang_ttl = self.terbilang_tanggal_lahir(saksi0_tgl_lahir)
            
        saksi1_tempat_lahir, saksi1_tgl_lahir = '', ''
        saksi1_terbilang_ttl = ''
        if witness1:
            saksi1_tempat_lahir, saksi1_tgl_lahir = self.safe_split_birth_info(witness1.birth_info)
            saksi1_terbilang_ttl = self.terbilang_tanggal_lahir(saksi1_tgl_lahir)
        
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

        format_hutang_pokok = self.format_rupiah(consumer.principal_loan)
        format_penjaminan = self.format_rupiah(consumer.guarantee_value)
        format_otr = self.format_rupiah(consumer.otr_value)

        bpkb_nationality = get_nationality_full(consumer.bpkb_nationality)

        context = {
            
            'tgl_sk': consumer.decree_date.strftime('%d %B %Y') if consumer.decree_date else '',
            'terbilang_tgl_sk': self.terbilang_tanggal_lahir(consumer.decree_date.strftime('%d-%m-%Y')) if consumer.decree_date else '',
            'sk': consumer.deed_number or '',

            'user_sementara': getattr(admin, 'address', ''),
            'pn_cabang': consumer.province or '',

            'saksi0_nama_tanpa': witness0.full_name if witness0 else '',
            'saksi1_nama_tanpa': witness1.full_name if witness1 else '',

            'terbilang_tgl_lahir_bpkb': self.terbilang_tanggal_lahir(consumer.bpkb_birth_date.strftime('%d-%m-%Y')) if consumer.bpkb_birth_date else '',
            'terbilang_tgl_perjanjian': self.terbilang_tanggal_lahir(consumer.agreement_date.strftime('%d-%m-%Y')) if consumer.agreement_date else '',
            'terbilang_hutang_pokok': self.terbilang_angka(consumer.principal_loan),
            'terbilang_penjaminan': self.terbilang_angka(consumer.guarantee_value),
            'terbilang_otr': self.terbilang_angka(consumer.otr_value),
    
            # Tanggal dan Hari Ini
            'tgl_hari_ini': tgl_hari_ini,
            'hari_ini': hari_ini,
            'terbilang_hari_ini': terbilang_hari_ini,

            # Data Notaris
            'notary_name': notary_instance.full_name if notary_instance else '',
            'notaris_gelar_panjang': notary_instance.full_title if notary_instance else '',
            'notaris_gelar_pendek': notary_instance.short_title if notary_instance else '',

            # Data Admin sebagai penerima kuasa (AdminProxy → power_user)
            'user_name': admin.full_name if admin else '',
            'penerima_kuasa_gelar_panjang': admin.full_title if admin else '',
            'penerima_kuasa_gelar_pendek': admin.short_title if admin else '',
            'user_tempat_lahir': user_tempat_lahir,
            'user_tanggal_lahir': user_tanggal_lahir,
            'user_terbilang_ttl': user_terbilang_ttl,
            'user_province': admin.province.region_name if admin and admin.province else '',
            'user_alamat': admin.address if admin else '',
            'user_rt': admin.rt if admin else '',
            'user_rw': admin.rw if admin else '',
            'user_kelurahan': admin.subdistrict if admin else '',
            'user_kecamatan': admin.district if admin else '',
            'user_city': admin.residence if admin else '',

            # Data Konsumen
            'Deed_Number': consumer.deed_number,
            'nama_pada_bpkb': consumer.name_on_bpkb,
            'user_pekerjaan': consumer.occupation,
            'kewarganegaraan_bpkb': bpkb_nationality,
            'tempat_lahir_bpkb': consumer.bpkb_birth_place,
            'tgl_lahir_bpkb': consumer.bpkb_birth_date.strftime('%d %B %Y') if consumer.bpkb_birth_date else '',
            'pekerjaan_bpkb': consumer.bpkb_occupation,
            'kabupaten_bpkb': consumer.bpkb_city,
            'alamat_bpkb': consumer.bpkb_address,
            'rt_bpkb': consumer.bpkb_rt,
            'rw_bpkb': consumer.bpkb_rw,
            'kelurahan_bpkb': consumer.bpkb_subdistrict,
            'kecamatan_bpkb': consumer.bpkb_district,
            'provinsi_bpkb': consumer.bpkb_province,
            'kode_pos_bpkb': consumer.bpkb_postal_code,
            'ktp_bpkb': consumer.bpkb_ktp,
            'tgl_perjanjian': consumer.agreement_date.strftime('%d %B %Y') if consumer.agreement_date else '',
            'alamat_cabang': consumer.branch_name,
            'nama_debitur': consumer.debtor_name,
            'ktp_debitur': consumer.id_number,
            'no_kontrak': consumer.agreement_number,
            'hutang_pokok': format_hutang_pokok,
            'nilai_penjaminan': format_penjaminan,
            'merk': consumer.brand,
            'type': consumer.type,
            'no_rangka': consumer.chassis_number,
            'no_mesin': consumer.engine_number,
            'warna': consumer.color,
            'tahun': str(consumer.production_year),
            'nilai_otr': format_otr,
            'notary_city': consumer.notary_city,

            # Data Saksi 0
            'saksi0_nama': witness0.full_name if witness0 else '',
            'saksi0_tempat_lahir': saksi0_tempat_lahir,
            'saksi0_tgl_lahir': saksi0_tgl_lahir,
            'saksi0_terbilang_ttl': saksi0_terbilang_ttl,
            'saksi0_tempat_tinggal': witness0.residence if witness0 else '',
            'saksi0_rt': witness0.rt if witness0 else '',
            'saksi0_rw': witness0.rw if witness0 else '',
            'saksi0_kelurahan': witness0.subdistrict if witness0 else '',
            'saksi0_kecamatan': witness0.district if witness0 else '',
            'saksi0_province': self.get_region_name_or_placeholder(getattr(witness0, 'province_id', None)),
            'saksi0_ktp': witness0.national_id if witness0 else '',

            # Data Saksi 1
            'saksi1_nama': witness1.full_name if witness1 else '',
            'saksi1_tempat_lahir': saksi1_tempat_lahir,
            'saksi1_tgl_lahir': saksi1_tgl_lahir,
            'saksi1_terbilang_ttl': saksi1_terbilang_ttl,
            'saksi1_tempat_tinggal': witness1.residence if witness1 else '',
            'saksi1_rt': witness1.rt if witness1 else '',
            'saksi1_rw': witness1.rw if witness1 else '',
            'saksi1_kelurahan': witness1.subdistrict if witness1 else '',
            'saksi1_kecamatan': witness1.district if witness1 else '',
            'saksi1_province': self.get_region_name_or_placeholder(getattr(witness1, 'province_id', None)),
            'saksi1_ktp': witness1.national_id if witness1 else '',

            # Waktu Akta
            'pukul_akta': pukul_akta,
            'terbilang_pukul': terbilang_pukul,
        }

        # Cek jika ada field penting yang kosong
        invalid_keys = [key for key, value in context.items() if value in [None, '']]

        if invalid_keys:
            return JsonResponse({
                'success': False,
                'message': 'Gagal menghasilkan dokumen. Mohon lengkapi data yang diperlukan.'
            }, status=400)

        label_map = {
            'no_akta': 'Deed_Number',
            'tgl_hari_ini': 'tgl_hari_ini',
            'hari_ini': 'hari_ini',
            'terbilang_hari_ini': 'terbilang_hari_ini',
            'pukul_akta': 'pukul_akta',
            'terbilang_pukul': 'terbilang_pukul',
            
            'saksi0_nama_tanpa': 'saksi0_nama_tanpa',
            'saksi1_nama_tanpa': 'saksi1_nama_tanpa',

            'notary_name': 'notary_name',
            'notaris_gelar_panjang': 'notaris_gelar_panjang',
            'notaris_gelar_pendek': 'notaris_gelar_pendek',
            'notary_city': 'notary_city',

            'user_name': 'user_name',
            'penerima_kuasa_gelar_panjang': 'penerima_kuasa_gelar_panjang',
            'penerima_kuasa_gelar_pendek': 'penerima_kuasa_gelar_pendek',
            'user_tempat_lahir': 'user_tempat_lahir',
            'user_tanggal_lahir': 'user_tanggal_lahir',
            'user_terbilang_ttl': 'user_terbilang_ttl',
            'user_pekerjaan': 'user_pekerjaan',
            'user_province': 'user_province',
            'user_alamat': 'user_alamat',
            'user_rt': 'user_rt',
            'user_rw': 'user_rw',
            'user_kelurahan': 'user_kelurahan',
            'user_kecamatan': 'user_kecamatan',
            'user_city': 'user_city',
            'user_ktp': 'ktp_debitur',

            'user_sementara': 'user_sementara',

            'tgl_sk': 'tgl_sk',
            'terbilang_tgl_sk': 'terbilang_tgl_sk',
            'sk': 'sk', 

            'nama_pada_bpkb': 'nama_pada_bpkb',
            'kewarganegaraan_bpkb': 'kewarganegaraan_bpkb',
            'tempat_lahir_bpkb': 'tempat_lahir_bpkb',
            'tgl_lahir_bpkb': 'tgl_lahir_bpkb',
            'terbilang_tgl_lahir_bpkb': 'terbilang_tgl_lahir_bpkb',
            'pekerjaan_bpkb': 'pekerjaan_bpkb',
            'kabupaten_bpkb': 'kabupaten_bpkb',
            'alamat_bpkb': 'alamat_bpkb',
            'rt_bpkb': 'rt_bpkb',
            'rw_bpkb': 'rw_bpkb',
            'kelurahan_bpkb': 'kelurahan_bpkb',
            'kecamatan_bpkb': 'kecamatan_bpkb',
            'provinsi_bpkb': 'provinsi_bpkb',
            'kode_pos_bpkb': 'kode_pos_bpkb',
            'ktp_bpkb': 'ktp_bpkb',

            'tgl_perjanjian': 'tgl_perjanjian',
            'terbilang_tgl_perjanjian': 'terbilang_tgl_perjanjian',
            'alamat_cabang': 'alamat_cabang',

            'nama_debitur': 'nama_debitur',
            'ktp_debitur': 'ktp_debitur',
            'no_kontrak': 'no_kontrak',

            'hutang_pokok': 'hutang_pokok',
            'terbilang_hutang_pokok': 'terbilang_hutang_pokok',

            'nilai_penjaminan': 'nilai_penjaminan',
            'terbilang_penjaminan': 'terbilang_penjaminan',

            'merk': 'merk',
            'type': 'type',
            'no_rangka': 'no_rangka',
            'no_mesin': 'no_mesin',
            'warna': 'warna',
            'tahun': 'tahun',
            'nilai_otr': 'nilai_otr',
            'terbilang_otr': 'terbilang_otr',

            'pn_cabang': 'pn_cabang',

            'saksi0_nama': 'saksi0_nama',
            'saksi0_tempat_lahir': 'saksi0_tempat_lahir',
            'saksi0_tgl_lahir': 'saksi0_tgl_lahir',
            'saksi0_terbilang_ttl': 'saksi0_terbilang_ttl',
            'saksi0_tempat_tinggal': 'saksi0_tempat_tinggal',
            'saksi0_rt': 'saksi0_rt',
            'saksi0_rw': 'saksi0_rw',
            'saksi0_kelurahan': 'saksi0_kelurahan',
            'saksi0_kecamatan': 'saksi0_kecamatan',
            'saksi0_province': 'saksi0_province',
            'saksi0_ktp': 'saksi0_ktp',

            'saksi1_nama': 'saksi1_nama',
            'saksi1_tempat_lahir': 'saksi1_tempat_lahir',
            'saksi1_tgl_lahir': 'saksi1_tgl_lahir',
            'saksi1_terbilang_ttl': 'saksi1_terbilang_ttl',
            'saksi1_tempat_tinggal': 'saksi1_tempat_tinggal',
            'saksi1_rt': 'saksi1_rt',
            'saksi1_rw': 'saksi1_rw',
            'saksi1_kelurahan': 'saksi1_kelurahan',
            'saksi1_kecamatan': 'saksi1_kecamatan',
            'saksi1_province': 'saksi1_province',
            'saksi1_ktp': 'saksi1_ktp',
        }

        context = {label: context[eng] for label, eng in label_map.items() if eng in context}

        doc.render(context)

        from io import BytesIO
        stream = BytesIO()
        doc.save(stream)
        stream.seek(0)

        response = HttpResponse(
            stream.read(),
            content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )
        response['Content-Disposition'] = f'attachment; filename="DraftAkta_{consumer.id}.docx"'
        return response

class AssignExportExcelSatuanViewPF(APIView):
    permission_classes = [IsAuthenticated]

    nama_hari = {
        0: 'Senin', 1: 'Selasa', 2: 'Rabu',
        3: 'Kamis', 4: 'Jumat', 5: 'Sabtu', 6: 'Minggu'
    }

    bulan_id = [
        "", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
        "Juli", "Agustus", "September", "Oktober", "November", "Desember"
    ]

    def format_rupiah(self, value):
        try:
            value = int(float(value))
            return f"{value:,}".replace(",", ".")
        except (ValueError, TypeError):
            return str(value)

    @staticmethod
    def tanggal_terbilang(dt):
        if not dt:
            return ''
        return f"{num2words(dt.day, lang='id')} {AssignExportExcelSatuanViewPF.bulan_id[dt.month]} {num2words(dt.year, lang='id')}"

    @staticmethod
    def safe_split_birth_info(birth_info_raw):
        if isinstance(birth_info_raw, str) and ',' in birth_info_raw:
            tempat, tgl = birth_info_raw.split(',', 1)
            return tempat.strip(), tgl.strip()
        return '', ''
    
    @staticmethod
    def terbilang_angka(nilai):
        if nilai is None:
            return ''
        try:
            nilai = int(float(nilai))  # pastikan tidak ada koma
            hasil = num2words(nilai, lang='id')
            return hasil.capitalize() + ' rupiah'
        except Exception:
            return str(nilai)

    @staticmethod
    def terbilang_tanggal_lahir(tgl_str):
        try:
            dt = datetime.strptime(tgl_str, '%d-%m-%Y')
            return f"{num2words(dt.day, lang='id')} {dt.strftime('%B')} {num2words(dt.year, lang='id')}"
        except Exception:
            return ''
    
    @staticmethod
    def get_region_name_or_placeholder(province_id):
        if not province_id:
            return ''
        with connection.cursor() as cursor:
            cursor.execute("SELECT region_name FROM regions WHERE id = %s", [province_id])
            row = cursor.fetchone()
            return row[0] if row else f"[province_id: {province_id}]"
    
    @class_permission_required('read-akta-pf')
    def get(self, request, pk):
        consumer = get_object_or_404(change_of_fiduciary, pk=pk)
        if not _consumer_belongs_to_user_branches(consumer, request.user):
            return HttpResponse("Forbidden", status=403)
        legal_doc = get_object_or_404(LegalDocumentPF, change_of_fiduciary=consumer)
        document_time = legal_doc.document_time
        admin = AdminProxy.objects.first()

        if isinstance(legal_doc.notary, Notary):
            notary_instance = legal_doc.notary

        elif isinstance(legal_doc.notary, str):
            try:
                notary_instance = Notary.objects.get(firm_name__iexact=legal_doc.notary.strip())
            except Notary.DoesNotExist:
                return JsonResponse({
                    "success": False,
                    "message": f"Notaris dengan nama '{legal_doc.notary}' tidak ditemukan di tabel Notary."
                }, status=404)

        else:
            return JsonResponse({
                "success": False,
                "message": "Data notaris tidak valid atau kosong."
            }, status=400)

        legal_doc.notary = notary_instance

        akta_type = 'Konvensional' if consumer.agreement_type.upper() == 'KONVE' else 'SYARIAH'
        customer_type = 'Personal' if consumer.customer_type.upper() == 'PERSONAL' else 'PT'

        jenis_doc = request.GET.get('doc', 'bpkb')
        is_salinan = request.GET.get('salinan', 'false').lower() == 'true'

        template_file = f"New BPKB - Draft Akta {akta_type} - {customer_type} - {jenis_doc} - Salinan"
        if is_salinan:
            template_file += " - Salinan"
        template_file += ".docx"

        template_path = os.path.join(settings.BASE_DIR, 'media', 'templatesakta-salinan', template_file)

        if not os.path.exists(template_path):
            return HttpResponse(f"Template file not found: {html.escape(template_path)}", status=404, content_type="text/plain; charset=utf-8")

        doc = DocxTemplate(template_path)
        
        document_date = legal_doc.document_date
        birth_place, birth_date_str = self.safe_split_birth_info(getattr(admin, 'birth_info', ''))
        birth_date = datetime.strptime(birth_date_str, '%d-%m-%Y') if birth_date_str else None

        user_tempat_lahir = birth_place.strip()
        user_tanggal_lahir = birth_date.strftime('%d %B %Y') if birth_date else ''
        user_terbilang_ttl = f"{num2words(birth_date.day, lang='id')} {birth_date.strftime('%B')} {num2words(birth_date.year, lang='id')}" if birth_date else ''
    
        print("DEBUG: legal_doc.notary =", repr(legal_doc.notary))
        print("DEBUG: notary_instance =", repr(notary_instance))
        print("DEBUG: type legal_doc.notary =", type(legal_doc.notary))
        print("DEBUG: type notary_instance =", type(notary_instance))

        witnesses = Witness.objects.filter(notary_id=notary_instance.id)
        witness0 = witnesses[0] if len(witnesses) > 0 else None
        witness1 = witnesses[1] if len(witnesses) > 1 else None
        
        tgl_hari_ini = document_date.strftime('%d-%m-%Y') if document_date else ''
        hari_ini = self.nama_hari[document_date.weekday()] if document_date else ''
        terbilang_hari_ini = self.tanggal_terbilang(document_date) if document_date else ''
        pukul_akta = document_time.strftime('%H:%M') if document_time else ''
        
        saksi0_tempat_lahir, saksi0_tgl_lahir = '', ''
        saksi0_terbilang_ttl = ''
        if witness0:
            saksi0_tempat_lahir, saksi0_tgl_lahir = self.safe_split_birth_info(witness0.birth_info)
            saksi0_terbilang_ttl = self.terbilang_tanggal_lahir(saksi0_tgl_lahir)
            
        saksi1_tempat_lahir, saksi1_tgl_lahir = '', ''
        saksi1_terbilang_ttl = ''
        if witness1:
            saksi1_tempat_lahir, saksi1_tgl_lahir = self.safe_split_birth_info(witness1.birth_info)
            saksi1_terbilang_ttl = self.terbilang_tanggal_lahir(saksi1_tgl_lahir)
        
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

        format_hutang_pokok = self.format_rupiah(consumer.principal_loan)
        format_penjaminan = self.format_rupiah(consumer.guarantee_value)
        format_otr = self.format_rupiah(consumer.otr_value)

        bpkb_nationality = get_nationality_full(consumer.bpkb_nationality)

        context = {
            
            'tgl_sk': consumer.decree_date.strftime('%d %B %Y') if consumer.decree_date else '',
            'terbilang_tgl_sk': self.terbilang_tanggal_lahir(consumer.decree_date.strftime('%d-%m-%Y')) if consumer.decree_date else '',
            'sk': consumer.deed_number or '',

            'user_sementara': getattr(admin, 'address', ''),
            'pn_cabang': consumer.province or '',

            'saksi0_nama_tanpa': witness0.full_name if witness0 else '',
            'saksi1_nama_tanpa': witness1.full_name if witness1 else '',

            'terbilang_tgl_lahir_bpkb': self.terbilang_tanggal_lahir(consumer.bpkb_birth_date.strftime('%d-%m-%Y')) if consumer.bpkb_birth_date else '',
            'terbilang_tgl_perjanjian': self.terbilang_tanggal_lahir(consumer.agreement_date.strftime('%d-%m-%Y')) if consumer.agreement_date else '',
            'terbilang_hutang_pokok': self.terbilang_angka(consumer.principal_loan),
            'terbilang_penjaminan': self.terbilang_angka(consumer.guarantee_value),
            'terbilang_otr': self.terbilang_angka(consumer.otr_value),
    
            # Tanggal dan Hari Ini
            'tgl_hari_ini': tgl_hari_ini,
            'hari_ini': hari_ini,
            'terbilang_hari_ini': terbilang_hari_ini,

            # Data Notaris
            'notary_name': notary_instance.full_name if notary_instance else '',
            'notaris_gelar_panjang': notary_instance.full_title if notary_instance else '',
            'notaris_gelar_pendek': notary_instance.short_title if notary_instance else '',

            # Data Admin sebagai penerima kuasa (AdminProxy → power_user)
            'user_name': admin.full_name if admin else '',
            'penerima_kuasa_gelar_panjang': admin.full_title if admin else '',
            'penerima_kuasa_gelar_pendek': admin.short_title if admin else '',
            'user_tempat_lahir': user_tempat_lahir,
            'user_tanggal_lahir': user_tanggal_lahir,
            'user_terbilang_ttl': user_terbilang_ttl,
            'user_province': admin.province.region_name if admin and admin.province else '',
            'user_alamat': admin.address if admin else '',
            'user_rt': admin.rt if admin else '',
            'user_rw': admin.rw if admin else '',
            'user_kelurahan': admin.subdistrict if admin else '',
            'user_kecamatan': admin.district if admin else '',
            'user_city': admin.residence if admin else '',

            # Data Konsumen
            'Deed_Number': consumer.deed_number,
            'nama_pada_bpkb': consumer.name_on_bpkb,
            'user_pekerjaan': consumer.occupation,
            'kewarganegaraan_bpkb': bpkb_nationality,
            'tempat_lahir_bpkb': consumer.bpkb_birth_place,
            'tgl_lahir_bpkb': consumer.bpkb_birth_date.strftime('%d %B %Y') if consumer.bpkb_birth_date else '',
            'pekerjaan_bpkb': consumer.bpkb_occupation,
            'kabupaten_bpkb': consumer.bpkb_city,
            'alamat_bpkb': consumer.bpkb_address,
            'rt_bpkb': consumer.bpkb_rt,
            'rw_bpkb': consumer.bpkb_rw,
            'kelurahan_bpkb': consumer.bpkb_subdistrict,
            'kecamatan_bpkb': consumer.bpkb_district,
            'provinsi_bpkb': consumer.bpkb_province,
            'kode_pos_bpkb': consumer.bpkb_postal_code,
            'ktp_bpkb': consumer.bpkb_ktp,
            'tgl_perjanjian': consumer.agreement_date.strftime('%d %B %Y') if consumer.agreement_date else '',
            'alamat_cabang': consumer.branch_name,
            'nama_debitur': consumer.debtor_name,
            'ktp_debitur': consumer.id_number,
            'no_kontrak': consumer.agreement_number,
            'hutang_pokok': format_hutang_pokok,
            'nilai_penjaminan': format_penjaminan,
            'merk': consumer.brand,
            'type': consumer.type,
            'no_rangka': consumer.chassis_number,
            'no_mesin': consumer.engine_number,
            'warna': consumer.color,
            'tahun': str(consumer.production_year),
            'nilai_otr': format_otr,
            'notary_city': consumer.notary_city,

            # Data Saksi 0
            'saksi0_nama': witness0.full_name if witness0 else '',
            'saksi0_tempat_lahir': saksi0_tempat_lahir,
            'saksi0_tgl_lahir': saksi0_tgl_lahir,
            'saksi0_terbilang_ttl': saksi0_terbilang_ttl,
            'saksi0_tempat_tinggal': witness0.residence if witness0 else '',
            'saksi0_rt': witness0.rt if witness0 else '',
            'saksi0_rw': witness0.rw if witness0 else '',
            'saksi0_kelurahan': witness0.subdistrict if witness0 else '',
            'saksi0_kecamatan': witness0.district if witness0 else '',
            'saksi0_province': self.get_region_name_or_placeholder(getattr(witness0, 'province_id', None)),
            'saksi0_ktp': witness0.national_id if witness0 else '',

            # Data Saksi 1
            'saksi1_nama': witness1.full_name if witness1 else '',
            'saksi1_tempat_lahir': saksi1_tempat_lahir,
            'saksi1_tgl_lahir': saksi1_tgl_lahir,
            'saksi1_terbilang_ttl': saksi1_terbilang_ttl,
            'saksi1_tempat_tinggal': witness1.residence if witness1 else '',
            'saksi1_rt': witness1.rt if witness1 else '',
            'saksi1_rw': witness1.rw if witness1 else '',
            'saksi1_kelurahan': witness1.subdistrict if witness1 else '',
            'saksi1_kecamatan': witness1.district if witness1 else '',
            'saksi1_province': self.get_region_name_or_placeholder(getattr(witness1, 'province_id', None)),
            'saksi1_ktp': witness1.national_id if witness1 else '',

            # Waktu Akta
            'pukul_akta': pukul_akta,
            'terbilang_pukul': terbilang_pukul,
        }

        # Cek jika ada field penting yang kosong
        invalid_keys = [key for key, value in context.items() if value in [None, '']]

        if invalid_keys:
            return JsonResponse({
                'success': False,
                'message': 'Gagal menghasilkan dokumen. Mohon lengkapi data yang diperlukan.'
            }, status=400)

        label_map = {
            'no_akta': 'Deed_Number',
            'tgl_hari_ini': 'tgl_hari_ini',
            'hari_ini': 'hari_ini',
            'terbilang_hari_ini': 'terbilang_hari_ini',
            'pukul_akta': 'pukul_akta',
            'terbilang_pukul': 'terbilang_pukul',
            
            'saksi0_nama_tanpa': 'saksi0_nama_tanpa',
            'saksi1_nama_tanpa': 'saksi1_nama_tanpa',

            'notary_name': 'notary_name',
            'notaris_gelar_panjang': 'notaris_gelar_panjang',
            'notaris_gelar_pendek': 'notaris_gelar_pendek',
            'notary_city': 'notary_city',

            'user_name': 'user_name',
            'penerima_kuasa_gelar_panjang': 'penerima_kuasa_gelar_panjang',
            'penerima_kuasa_gelar_pendek': 'penerima_kuasa_gelar_pendek',
            'user_tempat_lahir': 'user_tempat_lahir',
            'user_tanggal_lahir': 'user_tanggal_lahir',
            'user_terbilang_ttl': 'user_terbilang_ttl',
            'user_pekerjaan': 'user_pekerjaan',
            'user_province': 'user_province',
            'user_alamat': 'user_alamat',
            'user_rt': 'user_rt',
            'user_rw': 'user_rw',
            'user_kelurahan': 'user_kelurahan',
            'user_kecamatan': 'user_kecamatan',
            'user_city': 'user_city',
            'user_ktp': 'ktp_debitur',

            'user_sementara': 'user_sementara',

            'tgl_sk': 'tgl_sk',
            'terbilang_tgl_sk': 'terbilang_tgl_sk',
            'sk': 'sk', 

            'nama_pada_bpkb': 'nama_pada_bpkb',
            'kewarganegaraan_bpkb': 'kewarganegaraan_bpkb',
            'tempat_lahir_bpkb': 'tempat_lahir_bpkb',
            'tgl_lahir_bpkb': 'tgl_lahir_bpkb',
            'terbilang_tgl_lahir_bpkb': 'terbilang_tgl_lahir_bpkb',
            'pekerjaan_bpkb': 'pekerjaan_bpkb',
            'kabupaten_bpkb': 'kabupaten_bpkb',
            'alamat_bpkb': 'alamat_bpkb',
            'rt_bpkb': 'rt_bpkb',
            'rw_bpkb': 'rw_bpkb',
            'kelurahan_bpkb': 'kelurahan_bpkb',
            'kecamatan_bpkb': 'kecamatan_bpkb',
            'provinsi_bpkb': 'provinsi_bpkb',
            'kode_pos_bpkb': 'kode_pos_bpkb',
            'ktp_bpkb': 'ktp_bpkb',

            'tgl_perjanjian': 'tgl_perjanjian',
            'terbilang_tgl_perjanjian': 'terbilang_tgl_perjanjian',
            'alamat_cabang': 'alamat_cabang',

            'nama_debitur': 'nama_debitur',
            'ktp_debitur': 'ktp_debitur',
            'no_kontrak': 'no_kontrak',

            'hutang_pokok': 'hutang_pokok',
            'terbilang_hutang_pokok': 'terbilang_hutang_pokok',

            'nilai_penjaminan': 'nilai_penjaminan',
            'terbilang_penjaminan': 'terbilang_penjaminan',

            'merk': 'merk',
            'type': 'type',
            'no_rangka': 'no_rangka',
            'no_mesin': 'no_mesin',
            'warna': 'warna',
            'tahun': 'tahun',
            'nilai_otr': 'nilai_otr',
            'terbilang_otr': 'terbilang_otr',

            'pn_cabang': 'pn_cabang',

            'saksi0_nama': 'saksi0_nama',
            'saksi0_tempat_lahir': 'saksi0_tempat_lahir',
            'saksi0_tgl_lahir': 'saksi0_tgl_lahir',
            'saksi0_terbilang_ttl': 'saksi0_terbilang_ttl',
            'saksi0_tempat_tinggal': 'saksi0_tempat_tinggal',
            'saksi0_rt': 'saksi0_rt',
            'saksi0_rw': 'saksi0_rw',
            'saksi0_kelurahan': 'saksi0_kelurahan',
            'saksi0_kecamatan': 'saksi0_kecamatan',
            'saksi0_province': 'saksi0_province',
            'saksi0_ktp': 'saksi0_ktp',

            'saksi1_nama': 'saksi1_nama',
            'saksi1_tempat_lahir': 'saksi1_tempat_lahir',
            'saksi1_tgl_lahir': 'saksi1_tgl_lahir',
            'saksi1_terbilang_ttl': 'saksi1_terbilang_ttl',
            'saksi1_tempat_tinggal': 'saksi1_tempat_tinggal',
            'saksi1_rt': 'saksi1_rt',
            'saksi1_rw': 'saksi1_rw',
            'saksi1_kelurahan': 'saksi1_kelurahan',
            'saksi1_kecamatan': 'saksi1_kecamatan',
            'saksi1_province': 'saksi1_province',
            'saksi1_ktp': 'saksi1_ktp',
        }

        context = {label: context[eng] for label, eng in label_map.items() if eng in context}

        doc.render(context)

        from io import BytesIO
        stream = BytesIO()
        doc.save(stream)
        stream.seek(0)

        response = HttpResponse(
            stream.read(),
            content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )
        response['Content-Disposition'] = f'attachment; filename="DraftSalinanAkta_{consumer.id}.docx"'
        return response

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
@permission_required('read-akta-pf')
@require_GET
def export_excel_by_date_range_pf(request):
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
        LegalDocumentPF.objects
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
        tipe_raw            = g(cof, 'type')
        model               = g(cof, 'model')
        tipe_gabungan       = join_normal(tipe_raw, model)
        
        row = {
            'id'                    : getattr(d, 'consumer_id', None),
            'Notaris'               : d.notary.firm_name if getattr(d, 'notary', None) else "",
            'nama_cabang'           : g(cof, 'branch.branch_code') or g(cof, 'branch.code'),
            'jenis_nasabah'         : g(cof, 'customer_type'),
            'pemberi_fidusia'       : g(cof, 'fiducia_provider'),
            'nama_pada_bpkb'        : getattr(cof, "new_name_on_bpkb", None) or getattr(cof, "name_on_bpkb", None) or "",
            'nama_debitur'          : getattr(cof, "new_debtor_name", None) or getattr(cof, "debtor_name", None) or "",
            'tgl_lahir_bpkb'        : getattr(cof, "new_bpkb_birth_date", None) or getattr(cof, "bpkb_birth_date", None) or "",
            'tempat_lahir_bpkb'     : getattr(cof, "new_bpkb_birth_place", None) or getattr(cof, "bpkb_birth_place", None) or "",
            'pekerjaan_bpkb'        : getattr(cof, "new_bpkb_occupation", None) or getattr(cof, "bpkb_occupation", None) or "",
            'kewarganegaraan_bpkb'  : getattr(cof, "new_bpkb_nationality", None) or getattr(cof, "bpkb_nationality", None) or "",
            'alamat_bpkb'           : getattr(cof, "new_bpkb_address", None) or getattr(cof, "bpkb_address", None) or "",
            'rt_bpkb'               : getattr(cof, "new_bpkb_rt", None) or getattr(cof, "bpkb_rt", None) or "",
            'rw_bpkb'               : getattr(cof, "new_bpkb_rw", None) or getattr(cof, "bpkb_rw", None) or "",
            'kelurahan_bpkb'        : getattr(cof, "new_bpkb_subdistrict", None) or getattr(cof, "bpkb_subdistrict", None) or "",
            'kecamatan_bpkb'        : getattr(cof, "new_bpkb_district", None) or getattr(cof, "bpkb_district", None) or "",
            'kabupaten_bpkb'        : getattr(cof, "new_bpkb_city", None) or getattr(cof, "bpkb_city", None) or "",
            'provinsi_bpkb'         : getattr(cof, "new_bpkb_province", None) or getattr(cof, "bpkb_province", None) or "",
            'kode_pos_bpkb'         : getattr(cof, "new_bpkb_postal_code", None) or getattr(cof, "bpkb_postal_code", None) or "",
            'ktp_bpkb'              : getattr(cof, "new_bpkb_ktp", None) or getattr(cof, "bpkb_ktp", None) or "",
            'jenis_kelamin'         : getattr(cof, "new_gender", None) or getattr(cof, "gender", None) or "",
            'status_pernikahan'     : getattr(cof, "new_marital_status", None) or getattr(cof, "marital_status", None) or "",
            'tmp_lahir'             : getattr(cof, "new_birth_place", None) or getattr(cof, "birth_place", None) or "",
            'tgl_lahir'             : getattr(cof, "new_birth_date", None) or getattr(cof, "birth_date", None) or "",
            'pekerjaan'             : getattr(cof, "new_occupation", None) or getattr(cof, "occupation", None) or "",
            'kewarganegaraan'       : getattr(cof, "new_nationality", None) or getattr(cof, "nationality", None) or "",
            'jenis_identitas'       : getattr(cof, "new_id_type", None) or getattr(cof, "id_type", None) or "",
            'no_identitas'          : getattr(cof, "new_id_number", None) or getattr(cof, "id_number", None) or "",
            'alamat'                : getattr(cof, "new_address", None) or getattr(cof, "address", None) or "",
            'rt_rw'                 : getattr(cof, "new_rt_rw", None) or getattr(cof, "rt_rw", None) or "",
            'kelurahan'             : getattr(cof, "new_subdistrict", None) or getattr(cof, "subdistrict", None) or "",
            'kecamatan'             : getattr(cof, "new_district", None) or getattr(cof, "district", None) or "",
            'kota'                  : getattr(cof, "new_city", None) or getattr(cof, "city", None) or "",
            'provinsi'              : getattr(cof, "new_province", None) or getattr(cof, "province", None) or "",
            'kode_pos'              : getattr(cof, "new_postal_code", None) or getattr(cof, "postal_code", None) or "",
            'tgl_upload'            : g(cof, 'created_at'),

            'jenis_badan'           : g(cof, 'company_type'),
            'npwp_badan'            : g(cof, 'company_npwp'),
            'no_akta_badan'         : getattr(cof, "new_deed_number", None) or getattr(cof, "deed_number", None) or "",
            'tgl_akta_badan'        : getattr(cof, "new_deed_date", None) or getattr(cof, "deed_date", None) or "",
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
            'rt_rw_pj_badan'        : g(cof, 'director_rt_rw'),
            'kelurahan_pj_badan'    : g(cof, 'director_subdistrict'),
            'kecamatan_pj_badan'    : g(cof, 'director_district'),
            'kota_pj_badan'         : g(cof, 'director_city'),
            'provinsi_pj_badan'     : g(cof, 'director_province'),
            'jabatan_pj_badan'      : g(cof, 'director_position'),

            'jenis_perjanjian'      : g(cof, 'agreement_type'),
            'nama_perjanjian'       : g(cof, 'agreement_name'),
            'no_perjanjian'         : getattr(cof, "new_agreement_number", None) or getattr(cof, "agreement_number", None) or "",
            'tgl_perjanjian'        : g(cof, 'agreement_date'),

            'nilai_hutang_pokok'    : to_int_safe(getattr(cof, 'principal_loan', None)),
            'nilai_penjaminan'      : to_int_safe(getattr(cof, 'guarantee_value', None)),
            'tgl_awal_cicilan'      : g(cof, 'installment_start_date'),
            'tgl_akhir_cicilan'     : g(cof, 'installment_end_date'),
            'tenor'                 : g(cof, 'tenor'),

            'kondisi_kendaraan'     : g(cof, 'vehicle_condition'),
            'merk'                  : getattr(cof, "new_brand", None) or getattr(cof, "brand", None) or "",
            'tipe'                  : tipe_gabungan,
            'warna'                 : getattr(cof, "new_color", None) or getattr(cof, "color", None) or "",
            'tahun_perakitan'       : g(cof, 'assembly_year'),
            'tahun_pembuatan'       : g(cof, 'production_year'),
            'no_rangka'             : getattr(cof, "new_chassis_number", None) or getattr(cof, "chassis_number", None) or "",
            'no_mesin'              : getattr(cof, "new_engine_number", None) or getattr(cof, "engine_number", None) or "",
            'no_bpkb'               : g(cof, 'bpkb_number'),
            'nilai_objek_otr'       : to_int_safe(getattr(cof, 'otr_value', None)) or getattr(cof, "otr_value", None) or "",

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
        'biaya_pnbp','biaya_jasa','no_akta','tgl_akta', 'jam_akta', 'kd_voucher','no_sertifikat','tanggal_sertifikat','nama', 'tgl_upload'
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

    filename = f"Export_Akta_pf_{start_date}_-_{end_date}.xlsx"

    resp = HttpResponse(
        final_output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp

@login_required(login_url='')
@permission_required('read-akta-pf')
@require_GET
def export_template_filtered_pf(request):
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
        LegalDocumentPF.objects
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
        new_nama_debitur    = g(cof, 'new_debtor_name')
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
        new_nama_pemberi     = g(cof, 'new_name_on_bpkb' )
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
        
        nama_debitur_prior = (new_nama_debitur or nama_debitur or '')  # new_debtor_name → debtor_name
        nama_pemberi_bpkb_prior = (new_nama_pemberi or nama_pemberi or '') 
        
        if provider in ('atas nama customer'):
            # Ketentuan: nama pemberi diisi dengan nama debitur (prioritas new_debtor_name)
            row['nama pemberi'] = nama_debitur_prior

            # Ambil seluruh field alamat dari data debitur
            row['alamat pemberi']    = address_con or ""
            row['rt pemberi']        = rt_con or ""
            row['rw pemberi']        = rw_con or ""
            row['kelurahan pemberi'] = subdistrict_con or ""
            row['kecamatan pemberi'] = district_con or ""
            row['kota pemberi']      = city_con or ""
            row['provinsi pemberi']  = province_con or ""
            row['kode pos pemberi']  = postal_code_con or ""
            row['nik npwp pemberi']  = (nik_npwp_pemberi or "")   # jika 1 field dipakai untuk keduanya
            row['npwp pemberi']      = (nik_npwp_pemberi or "")

            # Kosongkan data debitur agar tidak ganda
            row['nama debitur'] = ""

        elif provider in ('atas nama bpkb'):
            # Pastikan semua field pemberi terisi
            wajib_pemberi = {
                'jenis kelamin pemberi': gender,
                # Pakai prioritas new_name_on_bpkb → name_on_bpkb
                'nama pemberi'         : nama_pemberi_bpkb_prior,
                'provinsi pemberi'     : provinsi_pemberi,
                'kota pemberi'         : kota_pemberi,
                'nik npwp pemberi'     : (nik_npwp_pemberi_con if 'nik_npwp_pemberi_con' in locals() else nik_npwp_pemberi),
                'npwp pemberi'         : (nik_npwp_pemberi_con if 'nik_npwp_pemberi_con' in locals() else nik_npwp_pemberi),
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
            nama_pemberi_norm = norm_str(nama_pemberi_bpkb_prior)
            nama_debitur_norm = norm_str(nama_debitur_prior)

            # === Logika pengisian nama debitur ===
            if nama_debitur_norm and nama_pemberi_norm:
                if nama_debitur_norm != nama_pemberi_norm:
                    # Jika beda → isi nama debitur apa adanya (prioritas new_debtor_name)
                    row['nama debitur'] = nama_debitur_prior or row.get('nama debitur', "")
                else:
                    # Jika sama → kosongkan nama debitur (cukup nama pemberi saja)
                    row['nama debitur'] = ""
            else:
                # Jika salah satu kosong → gunakan nama pemberi sebagai fallback
                row['nama debitur'] = nama_pemberi_bpkb_prior or row.get('nama debitur', "")

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

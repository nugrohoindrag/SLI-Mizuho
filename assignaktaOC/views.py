from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from urllib3 import request
from AdminRecipients.models import Recipient
from import_cabang_oc.models import ConsumerOC, Branch
from .serializers import ConsumerSerializer
from warkahDokumentOC.models import LegalDocumentOC
from AdminNotaries.models import Notary
from AdminProxies.models import AdminProxy
from assignakta.models import Witness
from django.shortcuts import get_object_or_404, redirect, render
from accesslogoc.models import activity_logs_oc
from AdminMenu.auth_helpers import class_permission_required, get_user_permissions, get_user_role_names, permission_required, user_has_permission
from common.const.role_map import has_role, ROLE_KEYS
from AdminUser.branch_access import get_primary_user_branch_code, get_user_branch_codes, user_is_branch_restricted
from rest_framework.permissions import IsAuthenticated
from django.http import JsonResponse, HttpResponse
from rest_framework.authentication import SessionAuthentication
from django.forms.models import model_to_dict
from datetime import datetime, time, timedelta, date
import openpyxl, io, os, glob, re
from django.utils.timezone import localdate, make_aware, get_current_timezone, now
from django.db.models.deletion import ProtectedError
from import_cabang.models import NotaryBranchArea
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
from io import BytesIO
from openpyxl import load_workbook
from openpyxl.styles import Font, Border, Alignment
from openpyxl.utils import get_column_letter
from common.const.id_calendar import BULAN_ID, HARI_ID
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
@permission_required("read-akta-oc")
def get_notaris_list_oc(request):
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
    return ConsumerOC.objects.filter(st_process='0', is_st_used=1)

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

    if _consumer_has_field(ConsumerOC, 'branch'):
        # Consumer punya FK 'branch' → gunakan branch_id__in
        qs = _eligible_consumers_qs().filter(branch_id__in=Subquery(branch_ids_sq))
    elif _consumer_has_field(ConsumerOC, 'branch_code'):
        # Consumer simpan string 'branch_code' → cocokkan ke branch_ids_sq (yang berisi kode)
        qs = _eligible_consumers_qs().filter(branch_code__in=Subquery(branch_ids_sq))
    else:
        # Skema lain—kasih pesan jelas
        raise FieldError("Consumer tidak memiliki field 'branch' maupun 'branch_code'.")

    # Postgres: aman pakai distinct('id'); jika bukan Postgres, ganti ke .distinct()
    return qs.distinct('id').order_by('id')

# ---- View ------------------------------------------------------------------

class AssignGenerateAPIViewOC(APIView):
    permission_classes = [IsAuthenticated]

    # =========================
    # PREVIEW: GET
    # =========================
    @class_permission_required('read-akta-oc')
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

        docs_qs = LegalDocumentOC.objects.select_related('consumer', 'notary').filter(
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
    @class_permission_required('create-akta-oc')
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

                    # Recalculate and persist PNBP before it is snapshotted to the
                    # legal document. The surrounding transaction keeps the old
                    # all-or-nothing behavior if document creation fails.
                    cons.st_process = '1'
                    cons.save(update_fields=['st_process'])

                    legal_doc = LegalDocumentOC.objects.create(
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
    
# class LegalDocumentTodayAPIViewOC(APIView):
#     permission_classes = [IsAuthenticated]

#     @class_permission_required('read-akta-oc')
#     def get(self, request, notary_id):
#         date_str = request.query_params.get('mock_date')

#         if date_str:
#             try:
#                 filter_date = datetime.strptime(date_str, "%Y-%m-%d").date()
#             except ValueError:
#                 return Response({'error': 'Format tanggal salah. Gunakan YYYY-MM-DD.'}, status=400)
#         else:
#             latest_doc = LegalDocumentOC.objects.filter(
#                 notary_id=notary_id,
#                 created_at__isnull=False
#             ).order_by('-created_at').first()

#             if not latest_doc:
#                 return Response({'message': 'Tidak ada dokumen untuk notaris ini.'}, status=404)

#             filter_date = latest_doc.created_at.date()

#         start_datetime = datetime.combine(filter_date, time.min)
#         end_datetime = datetime.combine(filter_date, time.max)

#         docs = LegalDocumentOC.objects.select_related('consumer', 'notary').filter(
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

class LegalDocumentUpsertAPIViewOC(APIView):
    permission_classes = [IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not user_has_permission(request.user, "create-akta-oc"):
            self.permission_denied(request, message="Forbidden")

    @class_permission_required('create-akta-oc')
    def post(self, request):
        legal_doc_id = request.data.get('id')  # pastikan dikirim dari JS
        document_number = request.data.get('document_number')
        document_time = request.data.get('document_time')  # format "HH:MM"

        if not legal_doc_id:
            return Response({'error': 'ID dokumen tidak ditemukan.'}, status=400)

        try:
            doc = LegalDocumentOC.objects.get(id=legal_doc_id)
            if _user_is_staff_cabang(request.user):
                user_branches = _user_branch_codes(request.user)
                if not user_branches:
                    return Response({'detail': 'Branch wajib diisi untuk user non-superadmin.'}, status=403)
                consumer = getattr(doc, "consumer", None)
                consumer_branch = None
                if getattr(consumer, "branch", None):
                    consumer_branch = getattr(consumer.branch, "branch_code", None) or getattr(
                        consumer.branch, "pk", None
                    )
                else:
                    consumer_branch = getattr(consumer, "branch_id", None) or getattr(
                        consumer, "branch_code", None
                    )
                if not consumer_branch or str(consumer_branch) != str(user_branch):
                    return Response({'detail': 'Forbidden branch scope.'}, status=403)

            # Update nomor dan waktu
            doc.document_number = document_number

            if document_time:
                hour, minute = map(int, document_time.split(':'))
                doc.document_time = time(hour=hour, minute=minute)

            doc.save()

            return Response({'message': 'LegalDocument berhasil diperbarui'})
        except LegalDocumentOC.DoesNotExist:
            return Response({'error': 'LegalDocument tidak ditemukan'}, status=404)
        except Exception as e:
            return Response({'error': str(e)}, status=500)

class AssignListCreateAPIViewOC(LoginRequiredMixin, View):
    login_url = '/login/'

    @class_permission_required('read-akta-oc')
    def get(self, request):
        page_size = min(int(request.GET.get('initial', 100)), 500)

        latest_doc = (
            LegalDocumentOC.objects
            .filter(consumer_id=OuterRef('pk'))
            .order_by('-document_date', '-id')
        )

        consumers = (
            ConsumerOC.objects
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
            consumeroc__uploader=request.user
        ).only('id','name').distinct()

        user_permissions = list(get_user_permissions(request.user))
        user_roles = list(get_user_role_names(request.user))

        roles_lower = [r.lower() for r in user_roles]
        perms_lower = [p.lower() for p in user_permissions]

        return render(request, 'operator/page-operator-assignakta-oc.html', {
            'consumers': consumers,
            'branches': branches,
            'user_permissions': perms_lower,
            'user_roles': roles_lower,
            'is_notaris': 'notaris' in roles_lower,
            'is_warkah': 'warkah' in roles_lower,
            'data_list': consumers.count(),
        })

    @class_permission_required('create-akta-oc')
    def post(self, request):
        data = request.POST.copy()

        for k in ('deed_number', 'deed_date', 'deed_documents'):
            data.pop(k, None)

        serializer = ConsumerSerializer(data=data, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return redirect('operator-assign-akta-oc')

        return render(request, 'operator/page-operator-assignakta-oc.html', {
            'errors': serializer.errors
        })

class AssignDataJSONOC(APIView):
    permission_classes = [IsAuthenticated]

    @class_permission_required('read-akta-oc')
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
            LegalDocumentOC.objects
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

        records_total     = LegalDocumentOC.objects.filter(consumer__st_process='1').count()
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

class AssignDetailAPIViewOC(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, pk):
        return get_object_or_404(ConsumerOC, pk=pk)

    @class_permission_required('read-akta-oc')
    def get(self, request, pk):
        consumer = self.get_object(pk)
        if not _consumer_belongs_to_user_branches(consumer, request.user):
            return Response({"detail": "Forbidden branch scope."}, status=status.HTTP_403_FORBIDDEN)
        serializer = ConsumerSerializer(consumer)
        return Response(serializer.data)

    @class_permission_required('update-akta-oc')
    def put(self, request, pk):
        consumer = self.get_object(pk)
        if not _consumer_belongs_to_user_branches(consumer, request.user):
            return Response({"detail": "Forbidden branch scope."}, status=status.HTTP_403_FORBIDDEN)
        serializer = ConsumerSerializer(consumer, data=request.data, context={'request': request}, partial=True)
        if serializer.is_valid():
            updated_consumer = serializer.save()
            return Response(ConsumerSerializer(updated_consumer).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @class_permission_required('delete-akta-oc')
    def delete(self, request, pk):
        try:
            consumer = self.get_object(pk)
            if not _consumer_belongs_to_user_branches(consumer, request.user):
                return Response({"detail": "Forbidden branch scope."}, status=status.HTTP_403_FORBIDDEN)
            consumer.delete()

            activity_logs_oc.objects.create(
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
@permission_required('read-akta-oc')
@require_GET
def export_excel_by_date_range_oc(request):
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
        LegalDocumentOC.objects
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
            'tgl_akhir_cicilan'     : g(cof, 'installment_end_date'),
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
        'biaya_pnbp','biaya_jasa','no_akta','tgl_akta','jam_akta','kd_voucher','no_sertifikat','tanggal_sertifikat','nama', 'tgl_upload'
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

    filename = f"Export_Akta_oc_{start_date}_-_{end_date}.xlsx"

    resp = HttpResponse(
        final_output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp

@login_required(login_url='')
@permission_required('read-akta-oc')
@require_GET
def export_template_filtered_oc(request):
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
        LegalDocumentOC.objects
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

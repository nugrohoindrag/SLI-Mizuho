from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from rest_framework.response import Response
from rest_framework import viewsets, permissions, status as drf_status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, permission_classes, parser_classes, action
from openpyxl import Workbook
from rest_framework.parsers import MultiPartParser, FormParser
from django.core.exceptions import FieldDoesNotExist
from django.utils import timezone
from django.http import HttpResponse, HttpResponseForbidden
from rest_framework.permissions import IsAuthenticated
import openpyxl, re, os, posixpath
from django.utils.text import slugify
from django.conf import settings
from django.core.files.storage import default_storage
from django.utils.crypto import get_random_string
from import_cabang_mu.models import ConsumerMU, Branch
from .models import ConsumerDocumentMU
from .serializers import ConsumerDocumentSerializer
from AdminMenu.auth_helpers import permission_required, class_permission_required, get_user_permissions, get_user_role_names
from common.const.role_map import has_role, ROLE_KEYS
from common.validators import validate_excel_file_or_error
from AdminUser.branch_access import (
    branch_scope_q,
    get_primary_user_branch_code,
    get_user_branch_codes,
    user_is_branch_restricted,
)
from collections import OrderedDict, defaultdict
from decimal import Decimal, InvalidOperation
from datetime import datetime, date, timedelta, time
from django.db import models, transaction
from django.db.models import Q, OuterRef, Subquery, Count, Exists, Case, When, F, CharField, Value, Window
from django.db.models.functions import RowNumber
from openpyxl.styles import numbers
from rest_framework.pagination import CursorPagination
from io import BytesIO
from django.urls import reverse
# ===== HTML page view (non-API) =====

STATUS_MAP = {
    'uncomplete': 0,
    'complete':   1,
    'process':    2,
    'revisi':     3,
}

# ====== Helpers (KHUSUS MU) ======
def can_view_all(user):
    role_names = []
    if getattr(user, 'roles', None) is not None:
        role_names = list(user.roles.values_list('name', flat=True))
    return (
        has_role(role_names, ROLE_KEYS["SUPERADMIN"])
        or has_role(role_names, ROLE_KEYS["HO"])
        or has_role(role_names, ROLE_KEYS["OPERATOR"])
        or has_role(role_names, ROLE_KEYS["WARKAH"])
    )

def _user_is_staff_cabang(user):
    """Check if user has staff_cabang role"""
    return user_is_branch_restricted(user)

def get_user_branch_code(user):
    return get_primary_user_branch_code(user)

def _consumer_mu_branch_filter_kwargs():
    """
    Kembalikan kwargs filter untuk ConsumerMU berdasarkan struktur model:
    - Jika ada FK 'branch' → gunakan branch__branch_code
    - Jika ada kolom 'branch_code' → gunakan branch_code
    """
    try:
        ConsumerMU._meta.get_field('branch')      # FK
        return 'branch__branch_code'
    except FieldDoesNotExist:
        try:
            ConsumerMU._meta.get_field('branch_code')
            return 'branch_code'
        except FieldDoesNotExist:
            return None  # tidak ada info cabang di ConsumerMU


# ====== Page View (staff cabang dokumen MU) ======
@login_required(login_url='')
@permission_required('read-dokumen-mu')
def view_staff_cabang_document_mu(request):
    branches = Branch.objects.only('id', 'branch_name', 'code').order_by('branch_name')

    if _user_is_staff_cabang(request.user):
        key = _consumer_branch_filter_kwargs()
        user_branches = get_user_branch_codes(request.user)
        if not user_branches or not key:
            base = ConsumerMU.objects.none()
        else:
            base = ConsumerMU.objects.filter(branch_scope_q(key, user_branches))
    else:
        base = ConsumerMU.objects.all()

    # hitung consumer_name berdasar fiducia_provider
    qs = (
        base.annotate(
            consumer_name=Case(
                # Kasus "Atas Nama BPKB" atau "BPKB"
                When(fiducia_provider__iexact='Atas Nama BPKB', then=F('name_on_bpkb')),
                When(fiducia_provider__iexact='BPKB', then=F('name_on_bpkb')),

                # Kasus "Atas Nama Customer" atau "CUSTOMER"
                When(fiducia_provider__iexact='Atas Nama Customer', then=F('debtor_name')),
                When(fiducia_provider__iexact='CUSTOMER', then=F('debtor_name')),

                # Default fallback → debtor_name
                default=F('debtor_name'),
                output_field=CharField(),
            )
        )
        .only(
            'id',
            'agreement_number',
            'debtor_name',
            'upload_date',
            'branch_name',
            'fiducia_provider',
            'name_on_bpkb',
        )
        .order_by('-id')
    )

    try:
        page_size = min(int(request.GET.get('page_size', 25)), 200)
    except (TypeError, ValueError):
        page_size = 25

    last_id = request.GET.get('last_id')
    if last_id:
        qs = qs.filter(id__lt=last_id)

    items = list(qs[:page_size])
    next_last_id = items[-1].id if items else None

    user_permissions = list(get_user_permissions(request.user))
    user_roles = ['Warkah', 'superadministrator'] if request.user.is_superuser else list(get_user_role_names(request.user))

    return render(
        request,
        'staff_cabang/page-staff_cabang-dokument-mu.html',
        {
            'branches': branches,
            'consumer_list': items,
            'next_last_id': next_last_id,
            'page_size': page_size,
            'user_permissions': user_permissions,
            'user_roles': user_roles,
        }
    )
    
def _consumer_branch_filter_kwargs():
    """
    Kembalikan kwargs filter untuk Consumer berdasarkan struktur model:
    - Jika ada FK 'branch' → gunakan branch__branch_code
    - Jika ada kolom 'branch_code' → gunakan branch_code
    """
    try:
        ConsumerMU._meta.get_field('branch')      # FK
        return 'branch__branch_code'
    except FieldDoesNotExist:
        try:
            ConsumerMU._meta.get_field('branch_code')
            return 'branch_code'
        except FieldDoesNotExist:
            return None

# ====== Utility simpan file ======
def _save_upload_and_get_url(uploaded_file, agreement_number):
    """
    Simpan file ke storage default dalam folder 'uploads' dan kembalikan URL web (/media/...).
    """
    ext = os.path.splitext(uploaded_file.name or '')[1].lower()  # contoh: .pdf/.jpg
    rand = get_random_string(6)
    rel_path = posixpath.join('uploads', f"{agreement_number}_{rand}{ext}")  # uploads/2504..._abc123.jpg

    # Simpan ke MEDIA_ROOT
    saved_path = default_storage.save(rel_path, uploaded_file)

    # Bangun URL web: /media/uploads/2504..._abc123.jpg (forward slash)
    media_url = settings.MEDIA_URL.rstrip('/')  # '/media'
    return f"{media_url}/{saved_path}".replace('\\', '/')

# ====== REST API ViewSet (MU) ======
ALLOWED_EXT  = {'.pdf', '.jpg', '.jpeg', '.png'}
ALLOWED_MIME = {'application/pdf', 'image/jpeg', 'image/png'}

def _get_ext(filename: str) -> str:
    _, ext = os.path.splitext(filename or '')
    return (ext or '.bin').lower()

def _is_allowed_upload(upload) -> bool:
    ext_ok = _get_ext(getattr(upload, 'name', '')) in ALLOWED_EXT
    # beberapa browser bisa kosong mimetype saat drag-drop; cukup cek ekstensi
    mime = getattr(upload, 'content_type', '') or ''
    return ext_ok and mime in ALLOWED_MIME

def _max_bytes_for_consumer(consumer) -> int:
    """
    Paksa batas ukuran file:
      - 'C' (Corporate)  -> 10 MB
      - 'I' (Individual) -> 5 MB
      - selain itu        -> 5 MB (dipaksa seperti 'I')
    """
    ctype = str(getattr(consumer, 'customer_type', '') or '').strip().upper()

    TEN_MB  = 10 * 1024 * 1024
    FIVE_MB = 5 * 1024 * 1024

    if ctype == 'C':
        return TEN_MB
    # default & 'I' → 5MB
    return FIVE_MB

def _build_public_url(path: str, request=None) -> str:
    if not path:
        return ''
    return reverse('media-mu', kwargs={'path': path})

def _storage_path(agreement_number: str, filename: str) -> str:
    ts   = timezone.now().strftime('%Y%m%d%H%M%S')
    base = slugify((filename or '').rsplit('.', 1)[0]) or 'upload'
    ext  = _get_ext(filename)
    folder = slugify(agreement_number or 'unknown') or 'unknown'
    return f"consumer_documents_mu/{folder}/{ts}_{base}{ext}"

def _delete_old_file_if_any(instance: ConsumerDocumentMU):
    """Opsional: hapus file lama jika ada dan bukan external URL."""
    url = (instance.file_url or '').strip()
    if not url:
        return
    # Jika file_url dari default_storage.url(path), coba derive path relatif
    # Pola umum: url(...) -> path disimpan di instance.file_path (kalau ada).
    # Kalau model tidak punya field path, SKIP supaya aman.
    if hasattr(instance, 'file_path') and instance.file_path:
        try:
            if default_storage.exists(instance.file_path):
                default_storage.delete(instance.file_path)
        except Exception:
            pass  # diamkan bila gagal, jangan mengganggu flow

class ConsumerDocumentViewSetMU(viewsets.ModelViewSet):
    authentication_classes = [SessionAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    serializer_class = ConsumerDocumentSerializer
    queryset = ConsumerDocumentMU.objects.all()
    
    class DocCursorPagination(CursorPagination):
        page_size = 50
        page_size_query_param = 'page_size'
        max_page_size = 200
        ordering = '-id'

    pagination_class = DocCursorPagination

    # ===================== SCOPE & FILTER =====================
    def _agreement_numbers_for_user_branch(self):
        """Ambil agreement_number yang boleh dilihat user, berdasar branch_code user."""
        if not _user_is_staff_cabang(self.request.user):
            return ConsumerMU.objects.all().values_list('agreement_number', flat=True)

        user_branches = get_user_branch_codes(self.request.user)
        if not user_branches:
            return ConsumerMU.objects.none().values_list('agreement_number', flat=True)

        return ConsumerMU.objects.filter(branch__branch_code__in=user_branches).values_list('agreement_number', flat=True)

    def get_queryset(self):
        user = self.request.user
        user_branches = get_user_branch_codes(user)
        
        # Queryset dasar (tanpa .defer() atau .only() agar lebih stabil)
        qs = super().get_queryset()

        # Terapkan filter hak akses cabang
        if _user_is_staff_cabang(user) and user_branches:
            exists_q = ConsumerMU.objects.filter(
                agreement_number=OuterRef('agreement_number'),
                branch__branch_code__in=user_branches
            )
            return qs.filter(Exists(exists_q))
        if _user_is_staff_cabang(user):
            return qs.none()

        return qs

    def _build_consumer_map(self, agreement_numbers):
        consumer_qs = (
            ConsumerMU.objects.filter(agreement_number__in=agreement_numbers).select_related('branch').only('agreement_number', 'debtor_name', 'customer_type', 'status', 'document_status','name_on_bpkb', 'fiducia_provider', 'agreement_date', 'branch__branch_code', 'branch__branch_name'))
        return {c.agreement_number: c for c in consumer_qs}

    def get_serializer_context(self):
        context = super().get_serializer_context()
        agreement_numbers = getattr(self.request, "_page_agreements", None)
        if agreement_numbers is None:
            agreement_numbers = self.get_queryset().values_list('agreement_number', flat=True)
        context['consumer_map'] = self._build_consumer_map(agreement_numbers)
        return context

    # ===================== LIST & LOOKUP =====================
    @class_permission_required("read-dokumen-mu")
    def list(self, request, *args, **kwargs):
        draw = request.query_params.get('draw')
        if draw is None:
            return super().list(request, *args, **kwargs)

        start  = int(request.query_params.get('start', 0))
        length = int(request.query_params.get('length', 10))
        search_value = (request.query_params.get('search[value]') or '').strip()

        base_qs = self.get_queryset()

        from datetime import datetime, date
        DATE_FIELD = 'created_at'

        def _parse_date(s: str):
            if not s: return None
            s = s.strip().replace(' ', '')
            for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
                try:
                    return datetime.strptime(s, fmt).date()
                except ValueError:
                    pass
            return None

        from_d = _parse_date(request.query_params.get('fromDate') or '')
        to_d   = _parse_date(request.query_params.get('toDate') or '')
        include_old = (request.query_params.get('include_old') or '').lower() in ('1', 'true', 'yes')

        if not from_d and not to_d:
            if search_value or include_old:
                from_d = None
                to_d = None
            else:
                today = date.today()
                from_d = to_d = today
        elif from_d and not to_d:
            to_d = from_d
        elif to_d and not from_d:
            from_d = to_d

        if from_d and to_d:
            diff_days = (to_d - from_d).days + 1

            if diff_days > 31:
                return Response(
                    {
                        "error": "Filter tanggal maksimal 31 hari (inklusif).",
                    },
                    status=400
                )

        qs = base_qs

        if from_d or to_d:
            consumer_date_q = ConsumerMU.objects.filter(agreement_number=OuterRef('agreement_number'))
            if from_d:
                consumer_date_q = consumer_date_q.filter(**{f'{DATE_FIELD}__date__gte': from_d})
            if to_d:
                consumer_date_q = consumer_date_q.filter(**{f'{DATE_FIELD}__date__lte': to_d})
            qs = qs.filter(Exists(consumer_date_q))

        if search_value:
            qs = qs.filter(
                Q(agreement_number__icontains=search_value) |
                Q(consumer_name__icontains=search_value)
            ).filter(
                Exists(
                    ConsumerMU.objects.filter(
                        agreement_number=OuterRef('agreement_number'),
                        is_st_used=True
                    )
                )
            )

        records_total = (base_qs.values('agreement_number').annotate(n=Count('id')).count())
        records_filtered = (qs.values('agreement_number').annotate(n=Count('id')).count())

        order_col_idx = request.query_params.get('order[0][column]')
        order_dir = request.query_params.get('order[0][dir]', 'asc')
        order_field_from_dt = request.query_params.get(f'columns[{int(order_col_idx)}][data]') if order_col_idx else None

        ALLOWED_ORDER_FIELDS = {
            'agreement_number': 'agreement_number',
            'updated_at': 'updated_at',
            'file_url': 'file_url',
        }
        order_field = ALLOWED_ORDER_FIELDS.get(order_field_from_dt, 'updated_at')
        order_by = F(order_field).desc(nulls_last=True) if order_dir == 'desc' else F(order_field).asc(nulls_last=True)

        qs_one = qs.annotate(
            rn=Window(
                expression=RowNumber(),
                partition_by=[F('agreement_number')],
                order_by=[order_by]
            )
        ).filter(rn=1)

        ordered = qs_one.order_by(order_by, F('agreement_number').asc(nulls_last=True), F('id').desc())

        page_qs = ordered[start:start+length]

        self.request._page_agreements = list(page_qs.values_list('agreement_number', flat=True))

        serializer = self.get_serializer(page_qs, many=True)
        return Response({
            'draw': int(draw),
            'recordsTotal': records_total,
            'recordsFiltered': records_filtered,
            'data': serializer.data,
        })

    @action(detail=False, methods=['get'], url_path='customer-type')
    @class_permission_required("read-dokumen-mu")
    def customer_type_lookup(self, request):
        """
        GET /api/consumer-documents/customer-type/?agreement_number=XXXX
        """
        an = (request.query_params.get('agreement_number') or '').strip()
        if not an:
            return Response({'detail': 'agreement_number wajib diisi.'}, status=drf_status.HTTP_400_BAD_REQUEST)

        allowed = set(self._agreement_numbers_for_user_branch())
        if an not in allowed and _user_is_staff_cabang(self.request.user):
            return Response({'detail': 'Anda tidak berhak melihat nomor perjanjian ini.'},status=drf_status.HTTP_403_FORBIDDEN)

        consumer = ConsumerMU.objects.filter(agreement_number=an).select_related('branch').first()
        if not consumer:
            return Response({'detail': 'Nomor perjanjian tidak ditemukan.'}, status=drf_status.HTTP_404_NOT_FOUND)

        return Response({
            'agreement_number': an,
            'customer_type': (getattr(consumer, 'customer_type', None) or '').upper()
        })

    # ===================== CREATE / BULK =====================
    @action(detail=False, methods=['post'], url_path='batch-create')
    @class_permission_required("create-dokumen-mu")
    def batch_create(self, request):
        data_list = request.data if isinstance(request.data, list) else [request.data]
        serializer = self.get_serializer(data=data_list, many=True)
        serializer.is_valid(raise_exception=True)
        response_serializer = self.perform_bulk_create(serializer)
        return Response(response_serializer.data, status=drf_status.HTTP_201_CREATED)

    def perform_bulk_create(self, serializer):
        serializer.save()
        agreement_numbers = [obj.agreement_number for obj in serializer.instance]
        consumer_map = self._build_consumer_map(agreement_numbers)
        return self.get_serializer(serializer.instance, many=True, context={'consumer_map': consumer_map})

    @class_permission_required("create-dokumen-mu")
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()

        consumer = ConsumerMU.objects.filter(agreement_number=instance.agreement_number).select_related('branch').first()
        consumer_map = {instance.agreement_number: consumer} if consumer else {}
        return Response(self.get_serializer(instance, context={'consumer_map': consumer_map}).data,status=drf_status.HTTP_201_CREATED)

    # ===================== PATCH (UPLOAD FILE) =====================
    @class_permission_required("update-dokumen-mu")
    @transaction.atomic
    def partial_update(self, request, *args, **kwargs):
        try:
            instance: ConsumerDocumentMU = self.get_object()

            def _bulk_sync_agreement(agreement_number, status_val=None, doc_status_val=None):
                """Sinkronkan status ke SEMUA baris dengan agreement_number sama."""
                update_consumer = {}
                if status_val is not None:
                    update_consumer['status'] = status_val
                if doc_status_val is not None:
                    update_consumer['document_status'] = doc_status_val
                if update_consumer:
                    ConsumerMU.objects.filter(
                        agreement_number=agreement_number
                    ).update(**update_consumer)

                update_doc = {}
                if status_val is not None and hasattr(ConsumerDocumentMU, 'status'):
                    update_doc['status'] = status_val
                if doc_status_val is not None and hasattr(ConsumerDocumentMU, 'document_status'):
                    update_doc['document_status'] = doc_status_val
                if update_doc:
                    ConsumerDocumentMU.objects.filter(
                        agreement_number=agreement_number
                    ).update(**update_doc)

            if _user_is_staff_cabang(request.user):
                user_branches = get_user_branch_codes(request.user)
                consumer_for_scope = ConsumerMU.objects.filter(
                    agreement_number=instance.agreement_number
                ).select_related('branch').first()
                doc_branch = getattr(getattr(consumer_for_scope, 'branch', None), 'branch_code', None) \
                            or getattr(consumer_for_scope, 'branch_code', None)

                if not user_branches or not doc_branch or doc_branch not in user_branches:
                    return Response({'detail': 'Anda tidak berhak mengubah data cabang lain.'},
                                    status=drf_status.HTTP_403_FORBIDDEN)

            blocked_keys = {'branch_code', 'branch_name', 'file'}
            data = {k: request.data.get(k) for k in request.data.keys() if k not in blocked_keys}

            uploaded = request.FILES.get('file')
            if uploaded:
                if not _is_allowed_upload(uploaded):
                    return Response({'detail': 'Format file harus PDF/JPG/PNG.'},
                                    status=drf_status.HTTP_400_BAD_REQUEST)

                consumer = ConsumerMU.objects.filter(
                    agreement_number=instance.agreement_number
                ).first()
                if not consumer:
                    return Response({'detail': 'Consumer untuk agreement ini tidak ditemukan.'},
                                    status=drf_status.HTTP_404_NOT_FOUND)

                max_bytes = _max_bytes_for_consumer(consumer)
                if uploaded.size > max_bytes:
                    mb = max_bytes // (1024 * 1024)
                    return Response({'detail': f'Ukuran file maksimal {mb} MB.'},
                                    status=drf_status.HTTP_400_BAD_REQUEST)

                if getattr(consumer, 'status', None) != 'process' or getattr(consumer, 'document_status', None) != STATUS_MAP['process']:
                    _bulk_sync_agreement(
                        agreement_number=instance.agreement_number,
                        status_val='process',
                        doc_status_val=STATUS_MAP['process']
                    )

                path = _storage_path(instance.agreement_number, uploaded.name)
                saved_path = default_storage.save(path, uploaded)
                public_url = _build_public_url(saved_path)

                update_fields = []

                instance.file_url = public_url
                update_fields.append('file_url')

                if hasattr(instance, 'file_path'):
                    instance.file_path = saved_path
                    update_fields.append('file_path')

                if hasattr(instance, 'created_at') and not getattr(instance, 'created_at', None):
                    from django.utils import timezone
                    instance.created_at = timezone.now()
                    update_fields.append('created_at')

                if hasattr(instance, 'modified_by'):
                    instance.modified_by = request.user
                    update_fields.append('modified_by')

                if hasattr(instance, 'document_status'):
                    instance.document_status = STATUS_MAP['process']
                    update_fields.append('document_status')

                if hasattr(instance, 'status'):
                    instance.status = 'process'
                    update_fields.append('status')

                instance.save(update_fields=update_fields)

            serializer = self.get_serializer(instance, data=data, partial=True)
            serializer.is_valid(raise_exception=True)
            instance = serializer.save()

            changed_status_val = None
            changed_doc_status_val = None
            if 'status' in serializer.validated_data:
                changed_status_val = serializer.validated_data.get('status')
            if 'document_status' in serializer.validated_data:
                changed_doc_status_val = serializer.validated_data.get('document_status')

            if changed_status_val is not None or changed_doc_status_val is not None:
                _bulk_sync_agreement(
                    agreement_number=instance.agreement_number,
                    status_val=changed_status_val,
                    doc_status_val=changed_doc_status_val
                )

            consumer_ctx = ConsumerMU.objects.filter(
                agreement_number=instance.agreement_number
            ).select_related('branch').first()
            context = self.get_serializer_context()
            context.update({'consumer_map': {instance.agreement_number: consumer_ctx} if consumer_ctx else {}})

            payload = self.get_serializer(instance, context=context).data
            if 'file_url' not in payload:
                payload['file_url'] = getattr(instance, 'file_url', '')

            return Response(payload)

        except Exception as e:
            return Response({'detail': f'{e.__class__.__name__}: {e}'},
                            status=drf_status.HTTP_400_BAD_REQUEST)

# ====== Download Template (MU) ======
@login_required
@permission_required("read-dokumen-mu")
def download_template_mu(request):
    status_raw = request.GET.get('status') or 'process'
    raw_from = (
        request.GET.get('from')
        or request.GET.get('fromDate')
        or request.GET.get('start')
        or request.GET.get('startDate')
    )
    raw_to = (
        request.GET.get('to')
        or request.GET.get('toDate')
        or request.GET.get('end')
        or request.GET.get('endDate')
    )

    ACCEPT_FMTS = [
        "%Y-%m-%d", "%Y/%m/%d",
        "%d/%m/%Y", "%d-%m-%Y",
        "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S",
    ]

    def parse_to_date(s):
        if not s:
            return None
        s = s.strip()
        for fmt in ACCEPT_FMTS:
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        # fallback ambil 10 char pertama
        for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(s[:10], fmt).date()
            except Exception:
                pass
        return None

    start_date = parse_to_date(raw_from)
    end_date   = parse_to_date(raw_to)

    if not (start_date and end_date):
        return HttpResponse(
            "Bad request: from/to date required.",
            status=400,
            content_type="text/plain",
        )

    if start_date > end_date:
        start_date, end_date = end_date, start_date

    range_days_inclusive = (end_date - start_date).days + 1
    if range_days_inclusive > 7:
        return HttpResponse(
            "Bad request: date range must be ≤ 7 days.",
            status=400,
            content_type="text/plain",
        )

    def _norm(s):
        return '' if s is None else str(s).strip().lower()

    norm_status = _norm(status_raw) or 'process'

    start_dt = datetime.combine(start_date, time.min)
    end_dt   = datetime.combine(end_date, time.max)

    consumers_qs = ConsumerMU.objects.filter(
        status__iexact=norm_status,
        created_at__range=(start_dt, end_dt),
    )
    if _user_is_staff_cabang(request.user):
        key = _consumer_mu_branch_filter_kwargs()
        user_branches = get_user_branch_codes(request.user)
        if not user_branches or not key:
            return HttpResponseForbidden("Branch tidak ditemukan untuk user ini.")
        consumers_qs = consumers_qs.filter(branch_scope_q(key, user_branches))

    consumers = list(
        consumers_qs.values('agreement_number', 'status').order_by('agreement_number')
    )

    if not consumers:
        return HttpResponse(
            "No data for given status/date range.",
            status=204,
            content_type="text/plain",
        )

    ag_nums = [c['agreement_number'] for c in consumers if c['agreement_number']]

    docs_qs = (
        ConsumerDocumentMU.objects
        .filter(agreement_number__in=ag_nums)
        .order_by('agreement_number', '-id')
        .values('agreement_number', 'notes', 'modified_by__username')
    )

    latest_doc_map = {}
    for d in docs_qs:
        ag = d['agreement_number']
        if ag not in latest_doc_map:
            latest_doc_map[ag] = {
                'notes': d['notes'] or '',
                'uploader': d['modified_by__username'] or '',
            }

    wb = Workbook(write_only=True)
    ws = wb.create_sheet(title="Template")

    ws.append(["Notes: Isi Status Dengan Revisi/Complete"])
    ws.append([])

    headers = ['No Perjanjian', 'Status', 'Upload_by', 'Notes']
    ws.append(headers)

    for c in consumers:
        ag = c['agreement_number']
        status = c['status'] or ''
        doc_info = latest_doc_map.get(ag, {})
        uploader = doc_info.get('uploader', '')
        notes    = doc_info.get('notes', '')

        ws.append([
            ag or '',
            status,
            uploader,
            notes,
        ])

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    status_suffix = (norm_status or 'process').replace(' ', '_')
    if start_date == end_date:
        suffix = f"{status_suffix}_{start_date.strftime('%Y%m%d')}"
    else:
        suffix = f"{status_suffix}_{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}"

    response = HttpResponse(
        output.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename=template_{suffix}.xlsx'
    return response

_SCI_RE = re.compile(r'^[+-]?\d+(\.\d+)?[eE][+-]?\d+$')

def _normalize_agreement_cell(val):
    """
    Normalisasi 'No Perjanjian' dari Excel menjadi string.
    - Trim spasi, handle int/float/scientific.
    - Bila float → tandai suspicious (Excel bisa hilangkan digit >15).
    """
    if val is None:
        return '', False
    suspicious = False

    if isinstance(val, str):
        return val.replace('\u200b', '').strip(), suspicious

    if isinstance(val, int):
        return str(val).strip(), suspicious

    if isinstance(val, float):
        suspicious = True
        s_raw = str(val)
        if _SCI_RE.match(s_raw):
            try:
                s_dec = format(Decimal(s_raw), 'f')
            except InvalidOperation:
                s_dec = s_raw
        else:
            s_dec = '{:.0f}'.format(val)  # 123456.0 -> '123456'
        return s_dec.strip(), suspicious

    return str(val).strip(), suspicious

@api_view(['POST'])
@parser_classes([MultiPartParser, FormParser])
@permission_classes([IsAuthenticated])
@permission_required("update-dokumen-mu")
def upload_template_excel_mu(request):
    excel_file = request.FILES.get('excel_file')
    if not excel_file:
        return Response({'detail': 'No file uploaded.'}, status=drf_status.HTTP_400_BAD_REQUEST)

    # Validate Excel file
    validation_error = validate_excel_file_or_error(excel_file)
    if validation_error:
        return validation_error

    try:
        import openpyxl
        wb = openpyxl.load_workbook(excel_file, data_only=True, read_only=True)
        ws = wb.active

        rows = list(ws.iter_rows(values_only=True))

        # cari header dinamis
        header_row_idx = None
        for idx, row in enumerate(rows):
            if row and row[0] and str(row[0]).strip() == 'No Perjanjian':
                header_row_idx = idx
                break
        if header_row_idx is None:
            return Response({'detail': 'Header not found in file.'}, status=drf_status.HTTP_400_BAD_REQUEST)

        header = rows[header_row_idx]
        data_rows = rows[header_row_idx + 1:]

        expected_header = ['No Perjanjian', 'Status', 'Upload_by', 'Notes']
        if [(h.strip() if isinstance(h, str) else (h or '')) for h in header[:4]] != expected_header:
            return Response({'detail': 'Invalid template format.'}, status=drf_status.HTTP_400_BAD_REQUEST)

        # DEDUPE per agreement_number (last wins)
        rows_by_agreement = OrderedDict()  # {agreement: (status, upload_by, notes)}
        suspicious_examples = []
        total_data_rows = 0

        for row in data_rows:
            if not row or all(v is None for v in row):
                continue
            total_data_rows += 1

            raw_agreement, status_cell, upload_by_cell, notes_cell = (row + (None, None, None, None))[:4]
            agreement_number, suspicious = _normalize_agreement_cell(raw_agreement)
            if suspicious and agreement_number:
                if len(suspicious_examples) < 5:
                    suspicious_examples.append(agreement_number)
            if not agreement_number:
                continue

            rows_by_agreement[agreement_number] = (status_cell, upload_by_cell, notes_cell)

        if not rows_by_agreement:
            return Response({'detail': 'Tidak ada baris data yang valid di file.'}, status=drf_status.HTTP_400_BAD_REQUEST)

        # ====== CEK AGREEMENT DI DB ======
        wanted = list(rows_by_agreement.keys())

        # semua yang ada di DB (aktif / tidak aktif)
        all_existing_ag_set = set(
            ConsumerMU.objects.filter(agreement_number__in=wanted).values_list('agreement_number', flat=True)
        )
        # hanya yang AKTIF (is_st_used = 1)
        active_ag_set = set(
            ConsumerMU.objects.filter(is_st_used=1, agreement_number__in=wanted).values_list('agreement_number', flat=True)
        )

        not_found_list = [ag for ag in wanted if ag not in all_existing_ag_set]
        not_active_list = [ag for ag in wanted if (ag in all_existing_ag_set and ag not in active_ag_set)]

        # ====== Kelompok per status (bulk update): HANYA untuk yang AKTIF ======
        allowed_statuses = set(STATUS_MAP.keys())
        by_status = defaultdict(list)
        notes_map = {}

        skipped_invalid_status = []
        for ag, (status_cell, _upload_by, notes_cell) in rows_by_agreement.items():
            if ag not in active_ag_set:
                continue  # skip yang tidak ditemukan / tidak aktif
            status_str = (str(status_cell).strip().lower() if status_cell is not None else '')
            if not status_str:
                continue
            if status_str not in allowed_statuses:
                skipped_invalid_status.append(f'{ag}({status_str})')
                continue
            by_status[status_str].append(ag)
            notes_map[ag] = notes_cell

        # Deteksi ketersediaan field di model ConsumerMU
        consumer_fields = {f.name for f in ConsumerMU._meta.get_fields()}
        has_text_status = 'status' in consumer_fields
        has_doc_status = 'document_status' in consumer_fields

        # ====== BULK UPDATE ConsumerMU (HANYA yang AKTIF) ======
        updated_count = 0
        for st, ag_list in by_status.items():
            if not ag_list:
                continue
            update_kwargs = {}
            if has_text_status:
                update_kwargs['status'] = st
            if has_doc_status:
                update_kwargs['document_status'] = STATUS_MAP.get(st, STATUS_MAP['uncomplete'])
            n = ConsumerMU.objects.filter(is_st_used=1, agreement_number__in=ag_list).update(**update_kwargs)
            updated_count += int(n or 0)

        # ====== Update dokumen terbaru per agreement (HANYA untuk yang AKTIF) ======
        doc_fields = {f.name for f in ConsumerDocumentMU._meta.get_fields()}
        doc_has_consumer_status = 'consumer_status' in doc_fields
        doc_has_modified_by = 'modified_by' in doc_fields
        doc_has_created_at = 'created_at' in doc_fields
        doc_has_updated_at = 'updated_at' in doc_fields
        doc_has_document_status = 'document_status' in doc_fields

        for ag in active_ag_set:
            if ag not in rows_by_agreement:
                continue  # tidak ada input untuk ag ini
            st_cell = rows_by_agreement.get(ag, (None, None, None))[0]
            st_norm = (str(st_cell).strip().lower() if st_cell is not None else '')
            st_norm = st_norm if st_norm in allowed_statuses else 'uncomplete'
            st_code = STATUS_MAP.get(st_norm, STATUS_MAP['uncomplete'])

            latest_doc = (ConsumerDocumentMU.objects
                          .filter(agreement_number=ag)
                          .order_by('-updated_at', '-id')
                          .first())

            if latest_doc:
                fields = []
                if ag in notes_map:
                    latest_doc.notes = notes_map[ag]
                    fields.append('notes')
                if doc_has_consumer_status:
                    latest_doc.consumer_status = st_norm
                    fields.append('consumer_status')
                if doc_has_document_status:
                    latest_doc.document_status = st_code
                    fields.append('document_status')
                if doc_has_modified_by:
                    latest_doc.modified_by = request.user
                    fields.append('modified_by')
                if doc_has_updated_at:
                    latest_doc.updated_at = timezone.now()
                    fields.append('updated_at')

                if fields:
                    latest_doc.save(update_fields=fields)
            else:
                # buat dokumen pertama hanya untuk consumer AKTIF
                consumer = ConsumerMU.objects.filter(is_st_used=1, agreement_number=ag).first()
                if consumer:
                    kwargs = dict(
                        agreement_number=ag,
                        consumer_name=getattr(consumer, 'debtor_name', '') or getattr(consumer, 'consumer_name', ''),
                        agreement_date=getattr(consumer, 'agreement_date', None),
                        document_type='Default Type',
                        description='Auto-generated document',
                        notes=notes_map.get(ag),
                        file_url='',
                    )
                    if doc_has_consumer_status:
                        kwargs['consumer_status'] = st_norm
                    if doc_has_document_status:
                        kwargs['document_status'] = st_code
                    if doc_has_modified_by:
                        kwargs['modified_by'] = request.user
                    if doc_has_created_at:
                        kwargs['created_at'] = timezone.now()
                    if doc_has_updated_at:
                        kwargs['updated_at'] = timezone.now()

                    ConsumerDocumentMU.objects.create(**kwargs)

        # ====== Build message detail ======
        parts = [
            f"Rows in file (non-empty): {total_data_rows}",
            f"Distinct agreements in file: {len(rows_by_agreement)}",
            f"Found in DB (any): {len(all_existing_ag_set)}",
            f"Active (is_st_used=1): {len(active_ag_set)}",
            f"Updated consumers: {updated_count}",
        ]
        if not_found_list:
            parts.append(f"Not found: {', '.join(not_found_list)}")
        if not_active_list:
            parts.append(f"Skipped not active (is_st_used!=1): {', '.join(not_active_list)}")
        if skipped_invalid_status:
            parts.append(f"Skipped invalid status: {', '.join(skipped_invalid_status)}")
        if suspicious_examples:
            parts.append("⚠️ Detected numeric-looking agreement(s) — set column 'No Perjanjian' as TEXT in Excel. Examples: "
                         + ', '.join(suspicious_examples))

        return Response({'detail': ' | '.join(parts)}, status=drf_status.HTTP_200_OK)

    except Exception as e:
        return Response({'detail': f'Error processing file: {str(e)}'}, status=drf_status.HTTP_400_BAD_REQUEST)
    
def _parse_date_flexible(date_string):
    """
    Mencoba mem-parsing string tanggal dengan beberapa format umum.
    Mengembalikan objek date jika berhasil, atau None jika gagal.
    """
    if not date_string:
        return None
    
    # Coba beberapa format tanggal yang umum
    for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y'):
        try:
            # Pastikan input adalah string sebelum di-parse
            return datetime.strptime(str(date_string), fmt).date()
        except (ValueError, TypeError):
            continue # Coba format berikutnya jika gagal
            
    # Kembalikan None jika semua format gagal
    return None

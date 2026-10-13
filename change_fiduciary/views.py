from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from import_cabang.models import Consumer, Branch
from .serializers import ChangeOfFiduciarySerializer
from django.shortcuts import get_object_or_404, render
from AdminMenu.auth_helpers import class_permission_required, get_user_permissions, get_user_role_names, function_permission_required
from rest_framework.permissions import IsAuthenticated
from django.http import JsonResponse, HttpResponse
from rest_framework.authentication import SessionAuthentication
from django.utils.timezone import localdate, make_aware, now, localtime, get_current_timezone
from django.forms.models import model_to_dict
from django.contrib.auth.decorators import login_required
from datetime import datetime, time, timedelta
from django.core.exceptions import FieldError
import openpyxl, io, json, logging
from django.db.models import Q, CharField, Max
from django.utils import timezone
from django.db.models.functions import Cast
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models.deletion import ProtectedError
from .models import change_of_fiduciary, ActivityLogPf
from assignaktaPF.models import LegalDocumentPF
from rest_framework.pagination import PageNumberPagination
from rest_framework.generics import ListAPIView
from decimal import Decimal
from django.utils.dateparse import parse_date
from django.db import connection, transaction, IntegrityError, models
from rest_framework.decorators import api_view, permission_classes
from AdminUser.models import User
from AdminUser.branch_access import (
    get_primary_user_branch_code,
    get_user_branch_codes,
    user_is_branch_restricted,
)

logger = logging.getLogger(__name__)

# =======================
# Branch Access Helpers (PF)
# =======================

def _pf_is_all_branch_user(user):
    role_names = {str(r or '').strip().lower() for r in get_user_role_names(user)}
    return any(
        r in {
            'ho',
            'head-office',
            'head office',
            'head_office',
            'superadministrator',
            'operator',
        }
        for r in role_names
    )

def _pf_user_branch_code(user):
    return get_primary_user_branch_code(user)

def _pf_user_branch_codes(user):
    return get_user_branch_codes(user)

def _pf_object_branch_code(obj):
    branch = getattr(obj, 'branch', None)
    if branch is not None:
        return getattr(branch, 'branch_code', None) or getattr(obj, 'branch_id', None)
    return getattr(obj, 'branch_code', None) or getattr(obj, 'branch_id', None)

def _pf_can_access_object(user, obj):
    if _pf_is_all_branch_user(user):
        return True
    user_branches = _pf_user_branch_codes(user)
    obj_branch = _pf_object_branch_code(obj)
    if not user_branches or not obj_branch:
        return False
    return str(obj_branch).strip() in {str(code).strip() for code in user_branches}

# =======================
# Log & Utilities
# =======================

class PFLogListView(APIView):
    permission_classes = [IsAuthenticated]

    @class_permission_required('read-pf-log')
    def get(self, request):
        return render(request, 'operator/page-operator-accesslog-pf.html')
    
def _get_field(model, name):
    try:
        return model._meta.get_field(name)
    except Exception:
        return None

def _cast_for_model_field(model, name, val):
    if val in (None, "", "None"):
        return None
    f = _get_field(model, name)
    if isinstance(f, models.DateField):
        return _as_date(val)
    if isinstance(f, (models.IntegerField, models.BigIntegerField)):
        try:
            # buang pemisah ribuan umum
            return int(str(val).replace('.', '').replace(',', ''))
        except Exception:
            return None
    if isinstance(f, (models.DecimalField, models.FloatField)):
        return _as_decimal(val)
    return val

# Kumpulan field konkret milik model PF untuk guard
MODEL_FIELDS_PF = {
    f.name for f in change_of_fiduciary._meta.get_fields()
    if getattr(f, "concrete", False) and not getattr(f, "many_to_many", False) and not getattr(f, "is_relation", False)
}

DATE_FMT = "%Y-%m-%d"

def fmt_dt_wib(dt):
    if not dt:
        return ""

    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)

    dt = timezone.localtime(dt)
    return dt.strftime("%Y-%m-%d %H:%M:%S")

class PFLogDataJSON(APIView):
    permission_classes = [IsAuthenticated]

    # mapping kolom DataTables -> field ORM utk sorting
    ORDERABLE_COLUMNS = [
        None,                # 0 (nomor urut)
        'causer_name',       # 1 (dibentuk manual -> fallback ke created_at)
        'agreement_number',
        'change_type',       # 2
        'choose_change',     # 3
        'choose_change_name',# 4
        'customer_type',     # 5
        None,                # 6 (1/2 nama) – sort fallback
        'branch_name',       # 7 (atau props.branch_name)
        'created_at',        # 8
    ]

    @class_permission_required('read-pf-log')
    def get(self, request):
        try:
            draw   = int(request.GET.get('draw', '1'))
            start  = int(request.GET.get('start', '0'))
            length = int(request.GET.get('length', '25'))

            start_date  = request.GET.get('start_date') or None
            end_date    = request.GET.get('end_date') or None
            branch_name = request.GET.get('branch_name') or None

            # <<<— ambil search bawaan DataTables
            search_value = (request.GET.get('search[value]') or '').strip()

            qs_all = change_of_fiduciary.objects.all().order_by('-created_at')
            total_count = qs_all.count()
            qs = qs_all

            model_fields = {f.name for f in change_of_fiduciary._meta.get_fields()}
            field_map = {f.name: f for f in change_of_fiduciary._meta.get_fields()}
            has = model_fields.__contains__

            def safe_lookup(field_name: str):
                field = field_map.get(field_name)
                if not field:
                    return None
                # hindari lookup icontains di field relasi (ForeignKey/M2M/OneToOne)
                if getattr(field, 'is_relation', False):
                    return None
                return f"{field_name}__icontains"

            # dipakai di search & saat serialize (causer_name)
            id_field = 'causer_id' if has('causer_id') else ('uploader_id' if has('uploader_id') else None)
            text_uploader_field = 'uploader' if has('uploader') else None

            # filter tanggal
            if start_date:
                try:
                    sd = datetime.strptime(start_date, DATE_FMT).date()
                    qs = qs.filter(created_at__date__gte=sd)
                except ValueError:
                    pass
            if end_date:
                try:
                    ed = datetime.strptime(end_date, DATE_FMT).date()
                    qs = qs.filter(created_at__date__lte=ed)
                except ValueError:
                    pass

            # filter cabang (hindari branch__icontains kalau branch itu FK)
            if branch_name:
                q = Q()
                if has('branch_name'):
                    q |= Q(branch_name__icontains=branch_name)
                if has('branch_id'):
                    q |= Q(branch_id__icontains=branch_name)
                if has('branch_code'):
                    q |= Q(branch_code__icontains=branch_name)
                qs = qs.filter(q)

            # === gunakan search bawaan DataTables (global search ke banyak kolom) ===
            if search_value:
                search_lc = search_value.strip().lower()
                q = Q()

                # nomor perjanjian
                for f in ('agreement_number', 'new_agreement_number'):
                    if has(f):
                        lookup = safe_lookup(f)
                        if lookup:
                            q |= Q(**{lookup: search_value})

                # jenis perubahan, tipe customer, debitur, BPKB
                for f in (
                    'change_type',
                    'choose_change',
                    'choose_change_name',
                    'customer_type',
                    'debtor_name',
                    'new_debtor_name',
                    'name_on_bpkb',
                    'new_name_on_bpkb',
                ):
                    if has(f):
                        lookup = safe_lookup(f)
                        if lookup:
                            q |= Q(**{lookup: search_value})

                # cabang di field varchar biasa
                for f in ('branch_name', 'branch_id', 'branch_code'):
                    if has(f):
                        lookup = safe_lookup(f)
                        if lookup:
                            q |= Q(**{lookup: search_value})

                # causer_name → cari di relasi user (causer/uploader) kalau ada
                if id_field:
                    rel_name = id_field[:-3] if id_field.endswith('_id') else None
                    if rel_name and rel_name in field_map:
                        rel_field = field_map[rel_name]
                        if getattr(rel_field, 'is_relation', False):
                            q |= (
                                Q(**{f"{rel_name}__username__icontains": search_value}) |
                                Q(**{f"{rel_name}__first_name__icontains": search_value}) |
                                Q(**{f"{rel_name}__last_name__icontains": search_value})
                            )

                # mapping subject_type dari choose_change
                if has('choose_change'):
                    if 'perubahan nama' in search_lc:
                        q |= Q(choose_change='perubahan1')
                    if 'kesalahan penulisan' in search_lc:
                        q |= Q(choose_change='perubahan2')

                # mapping subject_id dari choose_change_name
                if has('choose_change_name'):
                    if 'dari 1 menjadi 2' in search_lc or '1 menjadi 2 nama' in search_lc:
                        q |= Q(choose_change_name='perubahan1nama')
                    if 'dari 2 menjadi 1' in search_lc or '2 menjadi 1 nama' in search_lc:
                        q |= Q(choose_change_name='perubahan2nama')

                if q:
                    qs = qs.filter(q)

            # ====== Sorting dari DataTables ======
            order_col = request.GET.get('order[0][column]')
            order_dir = request.GET.get('order[0][dir]', 'desc')
            if order_col is not None:
                try:
                    order_col = int(order_col)
                    orm_field = self.ORDERABLE_COLUMNS[order_col]
                    if orm_field:
                        if order_dir == 'desc':
                            orm_field = f"-{orm_field}"
                        qs = qs.order_by(orm_field, '-id')  # tie-breaker
                except (ValueError, IndexError):
                    pass

            # ====== Hitung filtered_count sebelum slicing ======
            filtered_count = qs.count()
            logger.info('[PF] total=%s filtered=%s', total_count, filtered_count)

            # ====== Prefetch user untuk causer_name (opsional) ======
            user_map = {}
            if id_field:
                # ambil hanya id pada slice agar hemat
                ids = set(qs.values_list(id_field, flat=True))
                ids.discard(None)
                if ids:
                    user_map = {u.id: (u.get_username() or u.username) for u in User.objects.filter(id__in=ids)}

            # ====== Pagination (slice) ======
            end = start + length
            qs = qs[start:end]

            # ====== Serialize ======
            def _name(u):
                if not u: return ""
                if isinstance(u, str): return u.strip()
                fn = getattr(u, "get_full_name", None)
                if callable(fn):
                    n = (fn() or "").strip()
                    if n: return n
                gu = getattr(u, "get_username", None)
                if callable(gu):
                    n = (gu() or "").strip()
                    if n: return n
                return (getattr(u, "username", "") or "").strip()

            data = []
            for log in qs:
                # causer_name
                causer_name = ""
                if id_field:
                    uid = getattr(log, id_field, None)
                    causer_name = (user_map.get(uid, "") or "")
                    if not causer_name and text_uploader_field:
                        causer_name = _name(getattr(log, text_uploader_field, None))
                elif text_uploader_field:
                    causer_name = _name(getattr(log, text_uploader_field, None))

                agreement_number = (getattr(log, 'new_agreement_number', "") or getattr(log, 'agreement_number', '') or '').strip()
                description = getattr(log, 'change_type', "") or ""

                causer_type  = getattr(log, 'customer_type', "") or ""
                if causer_type == 'I':
                    causer_type_id = 'Personal'
                elif causer_type == 'C':
                    causer_type_id = 'Company'
                else:
                    causer_type_id = getattr(log, 'customer_type', '') or ''

                choose_change = (getattr(log, 'choose_change', '') or '').strip().lower()
                choose_change_name = (getattr(log, 'choose_change_name', '') or '').strip().lower()

                if choose_change == 'perubahan1':
                    subject_type = 'Perubahan Nama'
                elif choose_change == 'perubahan2':
                    subject_type = 'Kesalahan Penulisan Nama dan atau Alamat'
                else:
                    subject_type = getattr(log, 'choose_change', '') or ''

                if choose_change_name == 'perubahan1nama':
                    subject_id = 'Perubahan Nama dari 1 Menjadi 2 Nama'
                elif choose_change_name == 'perubahan2nama':
                    subject_id = 'Perubahan Nama dari 2 Menjadi 1 Nama'
                else:
                    subject_id = getattr(log, 'choose_change_name', None)

                debtor_name = (getattr(log, 'new_debtor_name', '') or getattr(log, 'debtor_name', '') or '').strip()
                name_bpkb   = (getattr(log, 'new_name_on_bpkb', '') or getattr(log, 'name_on_bpkb', '') or '').strip()

                provider = getattr(log, 'fiducia_provider', None).strip().lower()

                if debtor_name and name_bpkb:
                    if provider == "atas nama customer":
                        causer_id_value = "1 Nama"
                    else:
                        causer_id_value = ( "1 Nama" if debtor_name.casefold() == name_bpkb.casefold() else "2 Nama")
                else:
                    causer_id_value = ""

                props = {
                    "subject_name": getattr(log, 'choose_change_name', "") or "",
                    "agreement_number": agreement_number,
                    "customer_type": getattr(log, 'customer_type', "") or "",
                    "debtor_name": debtor_name,
                    "name_on_bpkb": name_bpkb,
                    "debitur": debtor_name,
                    "debtorName": debtor_name,
                }

                if has('branch_code'):
                    bc = getattr(log, 'branch_code', "") or ""
                    props["branch_code"] = bc
                    props["branchCode"]  = bc

                if has('branch'):
                    b = getattr(log, 'branch', None)
                    safe_code = getattr(b, 'branch_code', None) or getattr(log, 'branch_id', None) or ""
                    safe_name = getattr(b, 'name', None) or getattr(b, 'branch_name', None) or getattr(log, 'branch_name', None) or ""
                    props.setdefault("branch_code", safe_code or "")
                    props.setdefault("branchCode",  safe_code or "")
                    if safe_name:
                        props.setdefault("branch_name", safe_name or "")
                        props.setdefault("branchName",  safe_name or "")

                if has('branch_id'):
                    bid = getattr(log, 'branch_id', "") or ""
                    props.setdefault("branch_code", bid)
                    props.setdefault("branchCode",  bid)

                if has('branch_name'):
                    bn = getattr(log, 'branch_name', "") or ""
                    props["branch_name"] = bn
                    props["branchName"]  = bn

                data.append({
                    "causer_name": causer_name,
                    "agreement_number": agreement_number,
                    "description": description,
                    "subject_type": subject_type,
                    "subject_id": subject_id,
                    "causer_type": causer_type_id,
                    "causer_id": causer_id_value,
                    "properties": props,
                    "created_at": fmt_dt_wib(getattr(log, 'created_at', None)),
                })

            # ====== Response format DataTables (server-side) ======
            return Response({
                "draw": draw,
                "recordsTotal": total_count,
                "recordsFiltered": filtered_count,
                "data": data
            }, status=status.HTTP_200_OK)

        except Exception as e:
            logger.exception('[PF] ERROR')  # cetak stacktrace otomatis
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def get_next_activity_log_id():
    max_id = ActivityLogPf.objects.aggregate(Max('id'))['id__max']
    return (max_id or 0) + 1


def log_activity_pf(log_name, description, subject_id, subject_type, causer, properties=None):
    ActivityLogPf.objects.create(
        id=get_next_activity_log_id(),
        log_name=log_name,
        description=description,
        subject_id=subject_id,
        subject_type=subject_type,
        causer_id=causer.id if causer else None,
        causer_type=causer.__class__.__name__ if causer else 'Unknown',
        properties=properties or {},
        created_at=now(),
        updated_at=now(),
    )


# =======================
# Mapper Consumer -> ChangeOfFiduciary
# =======================

FIELD_MAP = [
    # Debitur
    ('debtor_name', 'debtor_name'),
    ('address', 'address'),
    ('id_number', 'id_number'),
    ('birth_place', 'birth_place'),
    ('birth_date', 'birth_date'),
    ('gender', 'gender'),
    ('marital_status', 'marital_status'),
    ('occupation', 'occupation'),
    ('nationality', 'nationality'),
    ('id_type', 'id_type'),
    ('rt_rw', 'rt_rw'),
    ('subdistrict', 'subdistrict'),
    ('district', 'district'),
    ('city', 'city'),
    ('province', 'province'),
    ('postal_code', 'postal_code'),

    # Objek
    ('brand', 'brand'),
    ('type', 'type'),
    ('chassis_number', 'chassis_number'),
    ('engine_number', 'engine_number'),
    ('color', 'color'),
    ('otr_value', 'otr_value'),

    # BPKB
    ('name_on_bpkb', 'name_on_bpkb'),
    ('bpkb_nationality', 'bpkb_nationality'),
    ('bpkb_birth_place', 'bpkb_birth_place'),
    ('bpkb_birth_date', 'bpkb_birth_date'),
    ('bpkb_occupation', 'bpkb_occupation'),
    ('bpkb_city', 'bpkb_city'),
    ('bpkb_address', 'bpkb_address'),
    ('bpkb_rt', 'bpkb_rt'),
    ('bpkb_rw', 'bpkb_rw'),
    ('bpkb_district', 'bpkb_district'),
    ('bpkb_subdistrict', 'bpkb_subdistrict'),
    ('bpkb_province', 'bpkb_province'),
    ('bpkb_postal_code', 'bpkb_postal_code'),
    ('bpkb_ktp', 'bpkb_ktp'),

    # Tambahan opsional
    ('agreement_type', 'agreement_type'),
    ('agreement_name', 'agreement_name'),
    ('agreement_date', 'agreement_date'),
    ('spb_date', 'spb_date'),
    ('principal_loan', 'principal_loan'),
    ('guarantee_value', 'guarantee_value'),
    ('installment_start_date', 'installment_start_date'),
    ('tenor', 'tenor'),
    ('vehicle_condition', 'vehicle_condition'),
    ('assembly_year', 'assembly_year'),
    ('production_year', 'production_year'),
    ('bpkb_number', 'bpkb_number'),
    ('pnbp_fee', 'pnbp_fee'),
    ('service_fee', 'service_fee'),
    ('npwp_provider', 'npwp_provider'),
    ('installment_end_date', 'installment_end_date'),
    ('bpkb_id_number', 'bpkb_id_number'),
    ('bpkb_npwp_number', 'bpkb_npwp_number'),
    ('phone_number', 'phone_number'),
    ('receipt_name', 'receipt_name'),
    ('spouse_name', 'spouse_name'),
    ('uploader_company', 'uploader_company'),
    ('upload_group', 'upload_group'),
    ('house_number', 'house_number'),
    ('rw', 'rw'),
    ('bpkb_name', 'bpkb_name'),
    #('document_status', 'document_status'),
    
    # Korporasi / Direksi
    ('company_type', 'company_type'),
    ('company_npwp', 'company_npwp'),
    ('deed_number', 'deed_number'),
    ('deed_date', 'deed_date'),
    ('company_director_name', 'company_director_name'),
    ('decree_date', 'decree_date'),
    ('director_gender', 'director_gender'),
    ('director_status', 'director_status'),
    ('director_birth_place', 'director_birth_place'),
    ('director_birth_date', 'director_birth_date'),
    ('director_nationality', 'director_nationality'),
    ('director_id_type', 'director_id_type'),
    ('director_id_number', 'director_id_number'),
    ('director_address', 'director_address'),
    ('director_rt_rw', 'director_rt_rw'),
    ('director_subdistrict', 'director_subdistrict'),
    ('director_district', 'director_district'),
    ('director_city', 'director_city'),
    ('director_province', 'director_province'),
    ('director_occupation', 'director_occupation'),
    ('director_position', 'director_position'),
    ('director_notary_name', 'director_notary_name'),
    ('notary_city', 'notary_city'),

    # (opsional) status/flag lain jika memang ada di Consumer
    ('fiducia_provider', 'fiducia_provider'),
    ('customer_type',    'customer_type'),
    ('invoice_st', 'invoice_st'),
    ('over_credit', 'over_credit'),
    ('sync', 'sync'),
]

def _none_if_blank(v):
    if v is None:
        return None
    if isinstance(v, str) and v.strip() == "":
        return None
    return v

def _as_decimal(v):
    if v in (None, ""):
        return None
    from decimal import Decimal, InvalidOperation
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _as_date(v):
    if not v:
        return None
    if hasattr(v, "isoformat") and not isinstance(v, str):
        # date/datetime
        return getattr(v, "date", lambda: v)()
    try:
        return datetime.fromisoformat(str(v)).date()
    except Exception:
        try:
            return datetime.strptime(str(v), "%Y-%m-%d").date()
        except Exception:
            return None

def build_payload_from_consumer(consumer, request_data, user):
    """
    1) Isi payload dari Consumer via FIELD_MAP (field dasar).
       -> Data dasar TIDAK akan dioverride oleh input new_*
    2) Set meta (agreement, branch, tanggal upload, uploader, dsb).
    3) Set nomor perjanjian baru (new_agreement_number).
    4) Masukkan SEMUA field new_* ke payload (hanya jika kolomnya ada di model PF, auto-cast).
       -> Tidak ada copy balik ke field lama tanpa prefix new_
    5) Set opsi tambahan (choose_change, dst) + alias dropdown organisasi ke choose_change_name.
    6) Bersihkan string kosong → None.
    """
    payload = {}

    # 1) map Consumer -> payload (field dasar) — HANYA read-only dari consumer
    for src, dst in FIELD_MAP:
        val = getattr(consumer, src, None)
        if dst in ('principal_loan', 'guarantee_value', 'otr_value', 'pnbp_fee', 'service_fee'):
            payload[dst] = _as_decimal(val)
        elif dst.endswith('_date') or dst in (
            'agreement_date', 'spb_date', 'installment_start_date',
            'installment_end_date', 'birth_date', 'bpkb_birth_date'
        ):
            payload[dst] = _as_date(val)
        else:
            payload[dst] = None if val in (None, 'None') else val

    # 2) meta wajib
    payload['agreement_number'] = (consumer.agreement_number or '').strip().upper()

    if getattr(consumer, 'branch', None):
        payload['branch'] = consumer.branch.pk
        payload['branch_name'] = (
            getattr(consumer, 'branch_name', '') or getattr(consumer.branch, 'branch_name', '')
        )

    payload['fiduciary_change_letter_number'] = _none_if_blank(request_data.get('fiduciary_change_letter_number'))
    payload['date_of_change_of_fiduciary_letter'] = _as_date(request_data.get('date_of_change_of_fiduciary_letter'))

    _ct = request_data.get('change_type')
    payload['change_type'] = _none_if_blank((_ct or '').strip() if _ct is not None else None)

    payload['upload_date'] = _as_date(now().date())
    payload['uploader'] = user.id if user and getattr(user, 'is_authenticated', False) else None

    # 3) agreement baru
    new_ag = _none_if_blank(request_data.get('new_agreement_number'))
    payload['new_agreement_number'] = (new_ag or '').strip().upper() or None

    # 4) MASUKKAN HANYA kolom new_* ke payload jika memang ada di model PF (tanpa override field lama)
    #    Termasuk normalisasi alias new_* bila perlu.
    NEW_ALIASES = {
        # contoh normalisasi nama berbeda di request vs model:
        # 'new_otr_terbilang': 'new_otr_value_words',
        # 'new_otr_values'   : 'new_otr_values',  # tetap sama jika model memiliki kolom ini
    }

    for key, val in request_data.items():
        if not key.startswith('new_'):
            continue
        target_key = NEW_ALIASES.get(key, key)
        if target_key in MODEL_FIELDS_PF:
            payload[target_key] = _cast_for_model_field(change_of_fiduciary, target_key, val)

    # 5) tipe debitur & opsi tambahan
    payload['debtor_type'] = _none_if_blank(
        request_data.get('debtor_type') or request_data.get('debtor_type_PT')
    )

    # (opsional) set customer_type / fiducia_provider jika dikirim & field ada (ini bukan field lama yang terpengaruh new_*)
    if 'customer_type' in request_data and 'customer_type' in MODEL_FIELDS_PF:
        payload['customer_type'] = _none_if_blank(request_data.get('customer_type'))
    if 'fiducia_provider' in request_data and 'fiducia_provider' in MODEL_FIELDS_PF:
        payload['fiducia_provider'] = _none_if_blank(request_data.get('fiducia_provider'))

    payload['choose_change'] = _none_if_blank(request_data.get('choose_change'))
    _name_ind = _none_if_blank(request_data.get('choose_change_name'))
    _name_org = _none_if_blank(request_data.get('choose_change_name_org'))
    payload['choose_change_name'] = _name_ind or _name_org
    payload['choose_mistake'] = _none_if_blank(request_data.get('choose_mistake'))
    payload['choose_mistake_2nama'] = _none_if_blank(request_data.get('choose_mistake_2nama'))

    # 6) bersihkan string kosong → None
    payload = {k: _none_if_blank(v) for k, v in payload.items()}
    return payload

def bulk_import_consumers_to_pf(user) -> int:
    """Import semua Consumer sesuai cabang user → ChangeOfFiduciary. Cegah duplikat agreement_number."""
    # Normalisasi branch user (dukung FK atau string nama)
    raw = getattr(user, 'branch', None)
    branch_obj = raw if isinstance(raw, Branch) else None
    branch_name = getattr(branch_obj, 'branch_name', None) if branch_obj else (str(raw) if raw else None)
    if not branch_obj and not branch_name:
        return 0

    # Filter Consumer berdasarkan cabang
    q = Q()
    if branch_obj:
        q |= Q(branch=branch_obj) | Q(branch__branch_name=branch_name)
    if branch_name:
        q |= Q(branch_name=branch_name) | Q(branch__branch_name=branch_name)

    consumers = Consumer.objects.filter(q).distinct()
    if not consumers.exists():
        return 0

    PF = change_of_fiduciary  # ganti ke kelas model kamu jika namanya ChangeOfFiduciary
    pf_fields = {f.name for f in PF._meta.get_fields()}
    def hasf(n): return n in pf_fields

    # deteksi tipe FK
    use_uid = hasf('uploader_id') and not hasf('uploader')
    use_bid = hasf('branch_id') and not hasf('branch')

    # hindari duplikat
    agre_nums = list(consumers.values_list('agreement_number', flat=True))
    existing = set(PF.objects.filter(agreement_number__in=agre_nums)
                   .values_list('agreement_number', flat=True))

    today = timezone.now().date()
    ts = timezone.now().strftime('%Y%m%d%H%M%S')

    rows = []
    for c in consumers:
        ag = (getattr(c, 'agreement_number', None) or '').strip()
        if not ag or ag in existing:
            continue

        # map field Consumer → PF pakai FIELD_MAP dan helper _as_decimal/_as_date
        mapped = {}
        for src, dst in FIELD_MAP:
            val = getattr(c, src, None)
            if dst in ('principal_loan', 'guarantee_value', 'otr_value', 'pnbp_fee', 'service_fee'):
                mapped[dst] = _as_decimal(val)
            elif dst.endswith('_date') or dst in ('agreement_date', 'spb_date', 'installment_start_date', 'installment_end_date', 'birth_date', 'bpkb_birth_date'):
                mapped[dst] = _as_date(val)
            else:
                mapped[dst] = None if val in (None, 'None') else val

        candidate = {
            **{k: v for k, v in mapped.items() if hasf(k)},
            'agreement_number': ag.upper() if hasf('agreement_number') else ag,
            'fiduciary_change_letter_number': f"AUTO-{ts}-{ag[-4:]:0>4}" if hasf('fiduciary_change_letter_number') else None,
            'date_of_change_of_fiduciary_letter': today if hasf('date_of_change_of_fiduciary_letter') else None,
            'upload_date': today if hasf('upload_date') else None,
        }

        if use_uid:
            candidate['uploader_id'] = user.id
        elif hasf('uploader'):
            candidate['uploader'] = user

        if branch_obj:
            if use_bid:
                candidate['branch_id'] = getattr(branch_obj, 'id', None)
            elif hasf('branch'):
                candidate['branch'] = branch_obj

        if branch_name and hasf('branch_name'):
            candidate['branch_name'] = branch_name

        # buang None untuk field yang tidak ada
        safe_kwargs = {k: v for k, v in candidate.items() if v is not None and hasf(k)}
        rows.append(PF(**safe_kwargs))

    if not rows:
        return 0

    with transaction.atomic():
        PF.objects.bulk_create(rows, batch_size=1000, ignore_conflicts=True)
    return len(rows)


# =======================
# List + Create
# =======================

class PFListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    # --- helper: deteksi HO / Head Office / Superadmin / Operator ---
    def is_ho(self, user):
        return _pf_is_all_branch_user(user)

    # --- helper: ambil branch code user login (utamakan *_id yang berisi PK) ---
    def get_user_branch_code(self, user):
        # Jika PK Branch = branch_code, maka user.branch_id langsung = kode cabang
        return _pf_user_branch_code(user)

    def get_user_branch_codes(self, user):
        return _pf_user_branch_codes(user)

    # --- helper: filter queryset sesuai struktur model change_of_fiduciary ---
    def filter_by_branch(self, qs, code: str):
        return self.filter_by_branches(qs, [code] if code else [])

    def filter_by_branches(self, qs, codes):
        """
        Urutan preferensi:
        1) Jika ada kolom FK 'branch' → gunakan branch_id=code
        2) Jika ada kolom CharField 'branch_code' → gunakan branch_code__iexact=code
        3) Jika ada relasi 'branch' → gunakan branch__branch_code__iexact=code
        Kalau tidak ada ketiganya → kembalikan qs.none()
        """
        model = qs.model
        # set nama field & attname (untuk *_id)
        field_names = {f.name for f in model._meta.get_fields()}
        attnames = {getattr(f, 'attname', None) for f in model._meta.fields}

        # 1) FK 'branch' → gunakan branch_id (lebih cepat & pasti)
        if not codes:
            return qs.none()

        if 'branch_id' in attnames:
            return qs.filter(branch_id__in=codes)

        # 2) CharField langsung 'branch_code'
        if 'branch_code' in field_names:
            return qs.filter(branch_code__in=codes)

        # 3) Relasi ke Branch bernama 'branch' dengan field branch_code
        if 'branch' in field_names:
            try:
                return qs.filter(branch__branch_code__in=codes)
            except FieldError:
                pass

        # fallback: tidak ketemu struktur yang didukung
        return qs.none()

    @class_permission_required('read-pf')
    def get(self, request):
        # --- preload consumer optional ---

        # print (f'consumer_id={consumer_id}')

        # if consumer_id is None:
        #     consumer_id = request.GET.get('consumer_id')

        # agreement_number = request.GET.get('agreement_number')
        # consumer = None
        # if consumer_id:
        #     consumer = get_object_or_404(Consumer, id=consumer_id)
        # elif agreement_number:
        #     consumer = get_object_or_404(Consumer, agreement_number__iexact=agreement_number)

        # --- scope cabang ---
        user_is_ho = self.is_ho(request.user)
        user_branch_codes = self.get_user_branch_codes(request.user)

        # base qs
        pf_qs = change_of_fiduciary.objects.all().select_related('branch')

        if not user_is_ho:
            if user_branch_codes:
                pf_qs = self.filter_by_branches(pf_qs, user_branch_codes)
            elif user_is_branch_restricted(request.user):
                pf_qs = pf_qs.none()

        # --- EXCLUDE data HARI INI (zona waktu lokal) ---
        today = localdate()
        start_today = make_aware(datetime.combine(today, time.min))
        next_day   = make_aware(datetime.combine(today + timedelta(days=1), time.min))
        pf_qs = pf_qs.exclude(created_at__gte=start_today, created_at__lt=next_day)

        # --- branches untuk dropdown ---
        if user_is_ho:
            branches = Branch.objects.all()
        else:
            if user_branch_codes:
                branches = Branch.objects.filter(branch_code__in=user_branch_codes)
            elif user_is_branch_restricted(request.user):
                branches = Branch.objects.none()
            else:
                branches = Branch.objects.all()


        customer_types = (
            Consumer.objects
            .exclude(customer_type__isnull=True)
            .exclude(customer_type__exact='')
            .values_list('customer_type', flat=True)
            .distinct()
            .order_by('customer_type')
        )

        user_permissions = list(get_user_permissions(request.user))
        user_roles = list(get_user_role_names(request.user))

        return render(request, 'staff_cabang/page-staff_cabang-perubahan-fidusia.html', {
            'consumers': pf_qs,
            'branches': branches,
            'user_permissions': user_permissions,
            'user_roles': user_roles,
            'data_list': pf_qs.count(),
            'customer_types': customer_types,
            #'consumer': consumer,
            'user_is_ho': user_is_ho,
        })

    @class_permission_required('create-pf')
    def post(self, request):
        mode = (request.data.get('mode') or '').lower()

        if mode == 'bulk_import':
            return Response({'detail': 'bulk_import belum diimplementasi.'}, status=status.HTTP_501_NOT_IMPLEMENTED)

        agreement_number = (request.data.get('agreement_number') or '').strip()
        if not agreement_number:
            return Response({'error': 'Nomor perjanjian wajib diisi.'}, status=status.HTTP_400_BAD_REQUEST)

        consumer = Consumer.objects.filter(agreement_number__iexact=agreement_number).first()
        if not consumer:
            return Response({'error': 'Nomor perjanjian tidak ditemukan di Consumer.'}, status=status.HTTP_404_NOT_FOUND)

        if change_of_fiduciary.objects.filter(agreement_number__iexact=agreement_number).exists():
            return Response({'error': 'Data perubahan fidusia untuk nomor ini sudah ada.'}, status=status.HTTP_400_BAD_REQUEST)

        # Bangun payload
        try:
            payload = build_payload_from_consumer(consumer, request.data, request.user)
        except Exception as e:
            return Response({'error': f'Gagal membangun payload: {e}'}, status=status.HTTP_400_BAD_REQUEST)

        # Normalisasi agreement_number
        payload['agreement_number'] = (consumer.agreement_number or '').strip().upper()

        # Map entity_type -> customer_type (fallback ke consumer.customer_type)
        entity_type = (request.data.get('entity_type') or '').strip()
        payload['customer_type'] = entity_type or getattr(consumer, 'customer_type', None)

        # --- Enforce branch sesuai role ---
        user_is_ho = self.is_ho(request.user)
        user_branch_codes = self.get_user_branch_codes(request.user)

        if user_is_ho:
            # HO boleh set branch sesuai request (jika ada), fallback ke milik consumer/user
            branch_code = (request.data.get('branch_code') or '').strip() or _pf_object_branch_code(consumer)
        else:
            requested_branch_code = (request.data.get('branch_code') or '').strip()
            if requested_branch_code:
                if requested_branch_code not in user_branch_codes:
                    return Response({'error': 'Forbidden branch scope.'}, status=status.HTTP_403_FORBIDDEN)
                branch_code = requested_branch_code
            elif len(user_branch_codes) == 1:
                branch_code = user_branch_codes[0]
            else:
                return Response({'error': 'Cabang wajib dipilih untuk akun multi cabang.'}, status=status.HTTP_400_BAD_REQUEST)

        if branch_code:
            branch = Branch.objects.filter(branch_code__iexact=branch_code).first()
            if not branch:
                return Response({'error': 'Branch tidak valid.'}, status=status.HTTP_400_BAD_REQUEST)

            payload['branch'] = branch.pk
            payload['branch_name'] = getattr(branch, 'branch_name', None) or getattr(branch, 'name', None)
        else:
            if not user_is_ho:
                return Response({'error': 'Akun Anda tidak terikat ke branch.'}, status=status.HTTP_403_FORBIDDEN)

        serializer = ChangeOfFiduciarySerializer(data=payload, context={'request': request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        instance = serializer.save()

        log_activity_pf(
            log_name='change_of_fiduciary',
            description='Create perubahan fidusia (single by agreement)',
            subject_id=instance.id,
            subject_type='change_of_fiduciary',
            causer=request.user,
            properties={
                'agreement_number': payload['agreement_number'],
                'new_agreement_number': payload.get('new_agreement_number'),
                'change_type': payload.get('change_type'),
                'customer_type': payload.get('customer_type'),
                'branch_name': payload.get('branch_name'),
                'branch_code': payload.get('branch_code'),
            }
        )

        return Response(ChangeOfFiduciarySerializer(instance).data, status=status.HTTP_201_CREATED)

class StandardResultsSetPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'limit'
    max_page_size = 500

class PFDataJSON(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ChangeOfFiduciarySerializer
    pagination_class = StandardResultsSetPagination

    # --- helper: deteksi HO / Superadmin / Operator ---
    def is_ho(self, user):
        return _pf_is_all_branch_user(user)

    # --- helper: ambil branch code user ---
    def get_user_branch_code(self, user):
        # jika PK Branch adalah branch_code, biasanya user.branch_id == kode cabang
        return _pf_user_branch_code(user)

    def get_user_branch_codes(self, user):
        return _pf_user_branch_codes(user)

    # --- helper: filter ke cabang tertentu (menyesuaikan struktur model) ---
    def filter_by_branch(self, qs, code: str):
        return self.filter_by_branches(qs, [code] if code else [])

    def filter_by_branches(self, qs, codes):
        model = qs.model
        field_names = {f.name for f in model._meta.get_fields()}
        attnames = {getattr(f, 'attname', None) for f in model._meta.fields}

        if not codes:
            return qs.none()

        # 1) Ada kolom FK 'branch' -> gunakan branch_id (lebih cepat)
        if 'branch_id' in attnames:
            return qs.filter(branch_id__in=codes)

        # 2) Ada kolom CharField 'branch_code' langsung di model
        if 'branch_code' in field_names:
            return qs.filter(branch_code__in=codes)

        # 3) Relasi ke Branch bernama 'branch' -> traverse ke branch_code
        if 'branch' in field_names:
            try:
                return qs.filter(branch__branch_code__in=codes)
            except FieldError:
                pass

        # fallback: struktur tidak didukung
        return qs.none()

    def get_queryset(self):
        qs = change_of_fiduciary.objects.select_related('uploader', 'branch').order_by('-created_at')

        # === VALIDASI SCOPE BRANCH ===
        # HO → lihat semua
        if self.is_ho(self.request.user):
            return qs

        # Non-HO:
        user_branch_codes = self.get_user_branch_codes(self.request.user)
        if user_branch_codes:
            # punya branch_code → filter cabang itu
            qs = self.filter_by_branches(qs, user_branch_codes)
        else:
            # tidak punya branch_code → jangan tampilkan apapun
            qs = qs.none()

        return qs

    @class_permission_required('read-pf')
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

# =======================
# Detail / Update / Delete
# =======================

class PFDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, pk):
        return get_object_or_404(change_of_fiduciary, pk=pk)

    @class_permission_required('read-pf')
    def get(self, request, pk):
        consumer = self.get_object(pk)
        if not _pf_can_access_object(request.user, consumer):
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
        serializer = ChangeOfFiduciarySerializer(consumer)
        return Response(serializer.data)

    @class_permission_required('update-pf')
    def put(self, request, pk):
        consumer = self.get_object(pk)
        if not _pf_can_access_object(request.user, consumer):
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
        serializer = ChangeOfFiduciarySerializer(consumer, data=request.data, context={'request': request}, partial=True)
        if serializer.is_valid():
            updated_consumer = serializer.save()
            log_activity_pf(
                log_name='change_of_fiduciary',
                description='Update perubahan fidusia',
                subject_id=updated_consumer.id,
                subject_type='change_of_fiduciary',
                causer=request.user,
                properties={'updated_fields': list(request.data.keys())}
            )
            return Response(ChangeOfFiduciarySerializer(updated_consumer).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @class_permission_required('delete-pf')
    def delete(self, request, pk):
        try:
            consumer = self.get_object(pk)
            if not _pf_can_access_object(request.user, consumer):
                return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
            LegalDocumentPF.objects.filter(change_of_fiduciary_id=consumer.id).delete()
            consumer.delete()
            log_activity_pf(
                log_name='change_of_fiduciary',
                description='Delete perubahan fidusia',
                subject_id=pk,
                subject_type='change_of_fiduciary',
                causer=request.user,
                properties={'message': 'Data fidusia dihapus.'}
            )
            return Response({"detail": "Data berhasil dihapus."}, status=status.HTTP_204_NO_CONTENT)
        except ProtectedError:
            return Response({"error": "Tidak bisa hapus karena masih ada data terkait."}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# =======================
# Proses dari Consumer -> ChangeOfFiduciary
# =======================
import traceback

class ProcessChangeOfFiduciaryAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @class_permission_required('create-pf')
    def post(self, request):
        # 1) Normalisasi input
        agreement_number = (request.data.get('agreement_number') or '').strip()
        if not agreement_number:
            return Response({'error': 'Nomor perjanjian wajib diisi.'},
                            status=status.HTTP_400_BAD_REQUEST)

        # 2) Ambil consumer (case-insensitive)
        consumer = get_object_or_404(Consumer, agreement_number__iexact=agreement_number)
        if not _pf_can_access_object(request.user, consumer):
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        # 3) Build payload (+ fallback isian minimal)
        payload = build_payload_from_consumer(consumer, request.data, request.user)

        # 3.5) EXCLUDE: buang field yang TIDAK boleh diset dari request/build
        EXCLUDE_FIELDS = {'st_process', 'is_st_used', 'status'} #, 'deed_number', 'deed_date'
        for f in EXCLUDE_FIELDS:
            payload.pop(f, None)

        # (opsional) jika ingin memaksa nilai server-side:
        # payload['status'] = 'draft'  # contoh, kalau mau default paksa

        # Pastikan field minimal untuk serializer ada
        missing = []
        for k in ['change_type']:
            if not payload.get(k):
                missing.append(k)
        if missing:
            return Response({'error': 'Field wajib hilang/invalid.', 'missing': missing, 'hint': 'Lengkapi isian form.'}, status=status.HTTP_400_BAD_REQUEST)

        # 4) Cek duplikat new_agreement_number (jika diisi)
        new_ag = (payload.get('new_agreement_number') or '').strip()
        if new_ag and change_of_fiduciary.objects.filter(new_agreement_number__iexact=new_ag).exists():
            return Response({'error': 'Nomor perjanjian baru sudah pernah digunakan.'}, status=status.HTTP_400_BAD_REQUEST)

        # 5) Validasi serializer (kembalikan error detail)
        serializer = ChangeOfFiduciarySerializer(data=payload, context={'request': request})
        if not serializer.is_valid():
            return Response({'error': 'Validasi gagal', 'details': serializer.errors, 'payload_preview': { k: payload.get(k) for k in ['agreement_number', 'new_agreement_number', 'change_type']}}, status=status.HTTP_400_BAD_REQUEST)

        # 6) Simpan atomik + logging
        try:
            with transaction.atomic():
                instance = serializer.save()

                # (opsional) harden lagi setelah save:
                # for f, v in {'status': 'draft', 'st_process': None, 'is_st_used': 0}.items():
                #     setattr(instance, f, v)
                # instance.save(update_fields=['status', 'st_process', 'is_st_used'])

                log_activity_pf(
                    log_name='change_of_fiduciary',
                    description='Proses perubahan fidusia dari Consumer',
                    subject_id=instance.id,
                    subject_type='change_of_fiduciary',
                    causer=request.user,
                    properties={
                        'source_agreement': (consumer.agreement_number or '').strip().upper(),
                        'new_agreement': instance.new_agreement_number,
                        'change_type': payload.get('change_type')
                    }
                )
            return Response({'message': 'Data berhasil diproses ke perubahan fidusia.', 'id': instance.id}, status=status.HTTP_201_CREATED)
        except IntegrityError as e:
            return Response({'error': 'Gagal menyimpan (integrity).', 'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({'error': 'Gagal menyimpan data', 'detail': str(e), 'traceback': traceback.format_exc().splitlines()}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

# =======================
# Prefill data by agreement_number (untuk form)
# =======================

def s(v):
    if v is None or v == 'None':
        return ''
    try:
        return str(v)
    except Exception:
        return ''

def d(v):
    # kembalikan 'YYYY-MM-DD' atau '' bila None/invalid
    try:
        return v.strftime('%Y-%m-%d') if v else ''
    except Exception:
        return ''

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_consumer_by_agreement(request, agreement_number):
    # normalisasi input
    ag = (agreement_number or '').strip()
    if not ag:
        return Response({'detail': 'Nomor perjanjian kosong.'}, status=status.HTTP_400_BAD_REQUEST)

    # cari case-insensitive
    consumer = Consumer.objects.filter(agreement_number__iexact=ag).first()
    if not consumer:
        # pastikan JSON 404 yang konsisten
        return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
    if not _pf_can_access_object(request.user, consumer):
        return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

    data = {
        'agreement_number': s(consumer.agreement_number),
        'customer_type': 'Personal' if s(consumer.customer_type) == 'I' else 'Company',

        # Debitur
        'debtor_name': s(consumer.debtor_name),
        'address': s(consumer.address),
        'id_number': s(consumer.id_number),
        'birth_place': s(consumer.birth_place),
        'birth_date': d(consumer.birth_date),
        'gender': s(consumer.gender),
        'marital_status': s(consumer.marital_status),
        'occupation': s(consumer.occupation),
        'nationality': s(consumer.nationality),
        'id_type': s(consumer.id_type),
        'rt_rw': s(consumer.rt_rw),
        'subdistrict': s(consumer.subdistrict),
        'district': s(consumer.district),
        'city': s(consumer.city),
        'province': s(consumer.province),
        'postal_code': s(consumer.postal_code),

        # Objek
        'brand': s(consumer.brand),
        'type': s(consumer.type),
        'chassis_number': s(consumer.chassis_number),
        'engine_number': s(consumer.engine_number),
        'color': s(consumer.color),
        'otr_value': s(consumer.otr_value),
        'otr_terbilang': '',

        # BPKB (default pakai field BPKB)
        'name_on_bpkb': s(consumer.name_on_bpkb),
        'bpkb_nationality': s(consumer.bpkb_nationality),
        'bpkb_birth_place': s(consumer.bpkb_birth_place),
        'bpkb_birth_date': d(consumer.bpkb_birth_date),
        'bpkb_birth_terbilang': '',
        'bpkb_occupation': s(consumer.bpkb_occupation),
        'bpkb_city': s(consumer.bpkb_city),
        'bpkb_address': s(consumer.bpkb_address),
        'bpkb_rt': s(consumer.bpkb_rt),
        'bpkb_rw': s(consumer.bpkb_rw),
        'bpkb_district': s(consumer.bpkb_district),
        'bpkb_subdistrict': s(consumer.bpkb_subdistrict),
        'bpkb_province': s(consumer.bpkb_province),
        'bpkb_postal_code': s(consumer.bpkb_postal_code),
        'bpkb_ktp': s(consumer.bpkb_ktp),

        # Tambahan identitas BPKB
        'bpkb_name': s(consumer.bpkb_name),
        'bpkb_id_number': s(consumer.bpkb_id_number),
        'bpkb_npwp_number': s(consumer.bpkb_npwp_number),
    }

    if consumer.fiducia_provider == 'Atas Nama BPKB':
        if consumer.name_on_bpkb != consumer.debtor_name:
            data.update({
                'debtor_type': '2 Nama'
            })
        else:
            data.update({
                'debtor_type': '1 Nama'
            })

    elif consumer.fiducia_provider == 'Atas Nama Customer':
        data.update({
            'debtor_type': '1 Nama',
            'name_on_bpkb':     s(consumer.debtor_name),
            'bpkb_nationality': s(consumer.nationality),
            'bpkb_birth_place': s(consumer.birth_place),
            'bpkb_birth_date':  d(consumer.birth_date),
            'bpkb_occupation':  s(consumer.occupation),
            'bpkb_birth_terbilang': '',
            'bpkb_rt': s(consumer.rt),
            'bpkb_rw': s(consumer.rw),

            'bpkb_city':        s(consumer.city),
            'bpkb_address':     s(consumer.address),
            'bpkb_district':    s(consumer.district),
            'bpkb_subdistrict': s(consumer.subdistrict),
            'bpkb_province':    s(consumer.province),
            'bpkb_postal_code': s(consumer.postal_code),

            'bpkb_ktp':         s(consumer.id_number),
            'bpkb_name':        s(consumer.debtor_name),
            'bpkb_id_number':   s(consumer.id_number),
            'bpkb_npwp_number': s(consumer.npwp_provider),
        })

    return Response(data, status=status.HTTP_200_OK)

from django.shortcuts import render
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from django.contrib.auth.decorators import login_required
from .models import Consumer, Branch
from import_document.models import FileProcessings
from .serializers import ConsumerSerializer
import pandas as pd
from datetime import datetime, date
from django.db import connection, transaction, models, DatabaseError, IntegrityError
from rest_framework.permissions import IsAuthenticated
from dateutil import parser
import os, json, logging, re
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.conf import settings
from django.core.files.storage import FileSystemStorage, default_storage
from accesslog.models import ActivityLog
from django import template
from rest_framework.exceptions import PermissionDenied
from django.utils import timezone
from AdminMenu.auth_helpers import (
    class_permission_required,
    user_has_permission,
    get_user_permissions,
    permission_required,
    get_user_role_names,
)
from common.const.role_map import has_role, ROLE_KEYS
from AdminUser.branch_access import (
    get_primary_user_branch_code,
    get_user_branch_codes,
    user_is_branch_restricted,
)
from common.validators import validate_excel_file_or_error
from import_document.models import ConsumerDocument
from rest_framework import serializers
from AktaSertifikat.models import ConsumerStatus

logger = logging.getLogger(__name__)
logger.warning(f"VIEW using DB alias: {connection.alias}")

register = template.Library()

@register.filter
def user_permissions(user):
    return get_user_permissions(user)

@permission_required('import_staff_cabang')
@login_required(login_url='')
def view_staff_cabang_import(request):
    branches = Branch.objects.filter(consumer__uploader=request.user).distinct()
    consumers = Consumer.objects.filter(uploader=request.user)

    # ✅ Ambil permission & role user dan masukkan ke context
    user_permissions = list(get_user_permissions(request.user))
    user_roles = list(get_user_role_names(request.user))

    return render(request, 'staff_cabang/page-staff_cabang-import.html', {
        'branches': branches,
        'consumer_list': consumers,
        'user_permissions': user_permissions,
        'user_roles': user_roles,
    })

class ConsumerViewSet(viewsets.ModelViewSet):
    serializer_class = ConsumerSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        logger.debug(f"User {self.request.user} accessing ConsumerViewSet")
        if not user_has_permission(self.request.user, 'view_staff_cabang'):
            logger.warning("User not authorized, returning empty queryset.")
            return Consumer.objects.none()
        return Consumer.objects.filter(uploader=self.request.user)

    def get_object(self):
        obj = super().get_object()
        action = self.action
        permission_map = {
            'retrieve': 'view_staff_cabang',
            'update': 'edit_staff_cabang',
            'partial_update': 'edit_staff_cabang',
            'destroy': 'delete_staff_cabang',
        }

        needed_perm = permission_map.get(action)
        if needed_perm and not user_has_permission(self.request.user, needed_perm):
            raise PermissionDenied(f"You do not have permission to {action} this object.")
        return obj

    @class_permission_required('create_staff_cabang')
    def create(self, request, *args, **kwargs):
        data = request.data.copy()
        data['uploader'] = request.user.id
        data['branch'] = get_primary_user_branch_code(request.user)
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @class_permission_required('edit_staff_cabang')
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @class_permission_required('edit_staff_cabang')
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @class_permission_required('delete_staff_cabang')
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    @class_permission_required('view_staff_cabang')
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

def normalize_header(header):
    header = str(header).strip().lower()
    header = re.sub(r'[^a-z0-9]+', '_', header)
    return header.strip('_')

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
@parser_classes([MultiPartParser])
def preview_excel(request):
    if not user_has_permission(request.user, 'import_staff_cabang'):
        return Response({'message': 'Forbidden'}, status=403)

    excel_file = request.FILES.get('excel_file')
    if not excel_file:
        return Response({'error': 'No file uploaded.'}, status=400)

    validation_error = validate_excel_file_or_error(excel_file)
    if validation_error:
        return validation_error

    logger.info(f"📁 File Excel diterima: {excel_file.name}")
    ext = os.path.splitext(excel_file.name)[1].lower()

    user = request.user
    role_names = list(get_user_role_names(user))
    is_privileged = (
        has_role(role_names, ROLE_KEYS["SUPERADMIN"])
        or has_role(role_names, ROLE_KEYS["HO"])
        or has_role(role_names, ROLE_KEYS["OPERATOR"])
        or has_role(role_names, ROLE_KEYS["RPA"])
        or getattr(user, "is_superuser", False)
    )
    is_ho = is_privileged
    is_rpa = has_role(role_names, ROLE_KEYS["RPA"])
    is_staff_cabang = user_is_branch_restricted(user)
    IS_FAST_MODE = is_rpa

    user_branch_codes = get_user_branch_codes(user)
    user_branch = Branch.objects.filter(branch_code=user_branch_codes[0]).first() if len(user_branch_codes) == 1 else None
    if not is_privileged and not user_branch_codes:
        return Response({'message': 'Branch wajib diisi untuk user non-HO.'}, status=403)

    def s(v): return '' if v is None else str(v).strip()

    def pad_3(v):
        v = s(v)
        if v in ('', '-'):
            return v
        if v.isdigit() and len(v) < 3:
            return v.zfill(3)
        return v

    def normalize_phone(v):
        v = s(v)
        return '0000000' if v == '0' else v

    def find_branch(excel_or_model_value):
        """
        Input bisa kode (numeric/string) atau nama cabang.
        Return Branch instance atau None.
        """
        val = s(excel_or_model_value)
        if not val:
            return None

        if val.isdigit():
            norm_code = pad_3(val)
            b = Branch.objects.filter(branch_code__iexact=norm_code).first()
            if b:
                return b

        b = Branch.objects.filter(branch_name__iexact=val).first()
        if b:
            return b

        b = Branch.objects.filter(branch_name__icontains=val).first()
        return b
    
    def is_valid_ddmmyyyy(value):
        val = s(value)
        if val in ('', '-'):
            return False

        formats = [
            "%Y-%m-%d","%d/%m/%Y","%d-%m-%Y","%m/%d/%Y",
        ]

        for fmt in formats:
            try:
                datetime.strptime(val, fmt)
                return True
            except ValueError:
                continue

        return False
    
    DATE_FIELDS = {
        'birth_date': 'Tanggal_Lahir_Customer',
        'bpkb_birth_date': 'Tanggal_lahir_pemilik_BPKB',
        'agreement_date': 'Tanggal_Perjanjian',
        'spb_date': 'Tanggal_SPB',
        'go_live_date': 'Tanggal_Awal_Cicilan',
        'installment_end_date': 'Tanggal_Akhir_Cicilan'
    }

    def resolve_branch_code(excel_or_model_value):
        """
        Kembalikan branch_code (str) jika ditemukan di master Branch, else None.
        """
        val = s(excel_or_model_value)
        if not val:
            return None

        norm_code = pad_3(val) if val.isdigit() else val
        b = Branch.objects.filter(branch_code__iexact=norm_code).first()
        if b:
            return b.branch_code

        b = Branch.objects.filter(branch_name__iexact=val).first()
        if b:
            return b.branch_code

        logger.warning(f"[BRANCH] Tidak ditemukan dari Excel input='{val}' (norm='{norm_code}')")
        return None

    CUSTOMER_TYPE_MAP = {
        'personal': 'I', 'individu': 'I', 'individual': 'I',
        'company': 'C', 'corporate': 'C', 'perusahaan': 'C',
        'i': 'I', 'c': 'C', 'bpkb': 'BPKB', 'debitur': 'DEBITUR'
    }
    def normalize_customer_type(v):
        key = s(v).lower()
        out = CUSTOMER_TYPE_MAP.get(key, s(v))
        return out.upper() if isinstance(out, str) else out

    def normalize_agreement_type(v):
        v = str(v).strip()
        val = v.lower()

        if 'retail consumer financing' in val:
            return 'KONVE'
        elif 'syariah' in val:
            return 'SYARIAH'
        else:
            return v.upper()
        
    FORBIDDEN_SUBSTRINGS = {
        "SD1","SD2","SD3","SD4","SD5", "SD", "1SD", "2SD", "3SD", "4SD", "5SD",
        "EUC1","EUC2","EUC3","EUC4","EUC5", "EUC", "1EUC", "2EUC", "3EUC", "4EUC", "5EUC"
    }

    CHASSIS_FIELD_ALIASES = ["chassis_number", "no_rangka", "frame_number", "vin"]
    ENGINE_FIELD_ALIASES  = ["engine_number", "no_mesin", "engine_no"]

    def contains_forbidden(val: str) -> bool:
        if not val:
            return False
        txt = str(val).upper().lstrip()
        return any(txt.startswith(p) for p in FORBIDDEN_SUBSTRINGS)

    def first_present(d: dict, keys: list[str]):
        for k in keys:
            if k in d and s(d.get(k)) not in ("", "-"):
                return d.get(k)
        return None

    bpkb_required = [
        'bpkb_birth_place','bpkb_birth_date','bpkb_birth_date_words','bpkb_occupation',
        'bpkb_nationality','bpkb_address','bpkb_rt','bpkb_rw','bpkb_subdistrict',
        'bpkb_district','bpkb_city','bpkb_province','bpkb_postal_code','bpkb_ktp'
    ]
    debtor_required = [
        'debtor_name','gender','birth_place','birth_date','birth_date_words',
        'occupation','nationality','address','rt','rw','subdistrict','district',
        'city','province','postal_code','id_number'
    ]

    try:
        if ext == '.xls':
            df = pd.read_excel(excel_file, engine='xlrd', dtype=str)
        elif ext == '.xlsx':
            df = pd.read_excel(excel_file, engine='openpyxl', dtype=str)
        else:
            return Response({'error': f'Unsupported file type: {ext}. Upload .xls or .xlsx only.'}, status=400)

        df.columns = [str(col).strip() if col else '' for col in df.columns]
        original_headers = df.columns.tolist()
        logger.info(f"📄 Header asli setelah strip: {original_headers}")

        if original_headers.count('') >= 3 or all(str(h).startswith('Unnamed') for h in original_headers):
            logger.warning("⚠️ Header kosong atau tidak valid. Membaca ulang dengan header=1.")
            df = pd.read_excel(excel_file, engine=('openpyxl' if ext == '.xlsx' else 'xlrd'), header=1)
            df.columns = [str(col).strip() if col else '' for col in df.columns]
            original_headers = df.columns.tolist()
            logger.info(f"📄 Header setelah fallback: {original_headers}")

        empty_headers = [i for i, h in enumerate(original_headers) if not h]
        if empty_headers:
            return Response({
                'error': f'Terdapat {len(empty_headers)} kolom tanpa nama di header Excel. ''Mohon isi nama kolom tersebut atau hapus kolom yang tidak digunakan.'
            }, status=400)

        unknown_headers = [
            h for h in original_headers
            if normalize_header(h) not in FIELD_MAPPING_NORMALIZED
        ]
        if unknown_headers:
            return Response({
                'error': f'Terdapat kolom tidak dikenali: {", ".join(unknown_headers)}. ''Periksa nama kolom agar sesuai dengan template.'
            }, status=400)

        # Format Mizuho punya kolom kembar (NAMA_BPKB / NAMA BPKB) dan kolom yang diabaikan:
        # simpan kemunculan pertama saja, buang kolom bermapping None.
        kept_headers, seen_norm = [], set()
        for h in original_headers:
            norm = normalize_header(h)
            if norm in seen_norm or FIELD_MAPPING_NORMALIZED.get(norm) is None:
                continue
            seen_norm.add(norm)
            kept_headers.append(h)
        df = df[kept_headers]
        original_headers = kept_headers

        mapped_columns, seen_columns, duplicate_mapped = [], set(), []
        for col in original_headers:
            mapped = FIELD_MAPPING_NORMALIZED.get(normalize_header(col))
            if mapped in seen_columns and mapped is not None:
                duplicate_mapped.append(mapped)
            else:
                seen_columns.add(mapped)
            mapped_columns.append(mapped)

        if duplicate_mapped:
            readable_duplicates = [col if col else '(kolom kosong)' for col in duplicate_mapped]
            return Response({
                'error': f'Duplikat kolom setelah mapping: {", ".join(readable_duplicates)}. ''Periksa header Excel agar tidak menimbulkan konflik.'
            }, status=400)

        rename_map = {
            col: FIELD_MAPPING_NORMALIZED.get(normalize_header(col))
            for col in original_headers
            if normalize_header(col) in FIELD_MAPPING_NORMALIZED
        }

        df_raw = df.rename(columns=rename_map)
        df_filled = df_raw.fillna('')
        for col in df_filled.select_dtypes(include=['datetime64[ns]']).columns:
            df_filled[col] = df_filled[col].dt.strftime('%Y-%m-%d')
        for col in MIZUHO_DATE_FIELDS:
            if col in df_filled.columns:
                df_filled[col] = df_filled[col].map(normalize_date_str)

        all_required_fields = list(df_raw.columns)

        errors, warnings = [], []
        seen_contracts, duplicate_in_db, invalid_contracts = set(), [], []
        preview_rows = []
        rows_for_preview = []
        
        for row in df_filled.to_dict(orient='records'):
            row_excel = {}
            for header in original_headers:
                model_field = FIELD_MAPPING_NORMALIZED.get(normalize_header(header))
                row_excel[header] = row.get(model_field, '') if model_field else ''
            rows_for_preview.append(row_excel)
        
        contract_numbers = df_filled.get('agreement_number', []).astype(str).tolist()

        existing_contracts = set(   
            Consumer.objects.filter(
                agreement_number__in=contract_numbers
            ).values_list('agreement_number', flat=True)
        )

        if IS_FAST_MODE:

            errors = []
            seen_contracts = set()
            duplicate_in_db = []
            invalid_contracts = []

            # ===== VALIDASI TANGGAL (RPA MODE) =====
            for field_name, field_label in DATE_FIELDS.items():

                for idx, raw_date in enumerate(df_filled.get(field_name, []), start=1):

                    if raw_date in (None, '', '-'):
                        continue

                    val_str = s(raw_date)

                    # Jika datetime object dari pandas
                    if isinstance(raw_date, (datetime, date)):
                        val_str = raw_date.strftime("%d/%m/%Y", "%d-%m-%y")

                    parsed_date = None
                    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%d-%m-%y"):
                        try:
                            parsed_date = datetime.strptime(val_str, fmt)
                            break
                        except ValueError:
                            continue

                    if not parsed_date:
                        errors.append(
                            f"Baris {idx}: Format {field_label} harus DD/MM/YYYY. "
                            f"Contoh: 07/01/2027"
                        )

            # ===== VALIDASI NOMOR PERJANJIAN =====
            for idx, contract_number in enumerate(df_filled.get('agreement_number', []), start=1):

                contract_number = str(contract_number).strip()

                if contract_number == '':
                    errors.append(f"Baris {idx}: Nomor perjanjian kosong.")
                    invalid_contracts.append('-')

                elif not contract_number.isdigit():
                    errors.append(f"Baris {idx}: Nomor perjanjian harus angka.")
                    invalid_contracts.append(contract_number)

                elif contract_number in seen_contracts:
                    errors.append(f"Baris {idx}: Duplikat dalam file: {contract_number}")
                    invalid_contracts.append(contract_number)

                elif contract_number in existing_contracts:
                    errors.append(f"Baris {idx}: Sudah ada di database: {contract_number}")
                    duplicate_in_db.append(contract_number)
                    invalid_contracts.append(contract_number)

                else:
                    seen_contracts.add(contract_number)

            if errors:
                return Response({
                    'error': "Cek kembali Excel Anda.",
                    'error_count': len(errors),
                    'errors': errors,
                    'invalid_contracts': invalid_contracts,
                    'duplikat_di_database': duplicate_in_db,
                }, status=400)

            return Response({
                'columns': original_headers,
                'rows': rows_for_preview,
                'warnings': [],
                'message': 'Preview berhasil (Mode RPA cepat).'
            }, status=200)

        for i in range(len(df_filled)):
            # Sel kosong dari pandas = NaN; jadikan '' agar tidak terbaca sebagai teks 'nan'
            row_raw = {k: ('' if pd.isna(v) else v) for k, v in df_raw.iloc[i].to_dict().items()}
            row = {
                k: (v.strftime('%Y-%m-%d') if isinstance(v, (datetime, date)) else v)
                for k, v in df_filled.iloc[i].to_dict().items()
            }

            for field_name, field_label in MIZUHO_DATE_FIELDS.items():
                val_str = s(row.get(field_name))
                if val_str not in ('', '-') and not is_valid_ddmmyyyy(val_str):
                    errors.append(
                        f"Baris {i+1}: Format tanggal pada {field_label} tidak dikenali "
                        f"(gunakan tipe tanggal Excel atau DD/MM/YYYY)."
                    )

            for field_name, field_label in MIZUHO_REQUIRED_FIELDS.items():
                if s(row.get(field_name)) in ('', '-'):
                    errors.append(f"Baris {i+1}: {field_label} wajib diisi.")

            for field in all_required_fields:
                if s(row_raw.get(field, '')) == '':
                    row[field] = '-'
                    if field not in MIZUHO_OPTIONAL_FIELDS and field not in MIZUHO_REQUIRED_FIELDS:
                        warnings.append(f"Baris {i+1}: Kolom '{field}' kosong, diisi '-'.")

            for f in ['rt', 'rw']:
                before = s(row.get(f))
                padded = pad_3(before)
                row[f] = padded
                if before in ('0', '00') or (before.isdigit() and len(before) in (1, 2)):
                    warnings.append(f"Baris {i+1}: Kolom '{f}' dipadatkan menjadi 3 digit ({before} → {padded}).")

            pn_before = s(row.get('phone_number'))
            row['phone_number'] = normalize_phone(pn_before)
            if pn_before == '0':
                warnings.append(f"Baris {i+1}: Nomor telepon '0' dinormalisasi menjadi '0000000'.")

            for w in mizuho_row_warnings(row):
                warnings.append(f"Baris {i+1}: {w}")

            contract_number = s(row.get('agreement_number'))
            if contract_number in ('', '-'):
                invalid_contracts.append('-')
            elif not AGREEMENT_NUMBER_RE.fullmatch(contract_number):
                errors.append(f"Baris {i+1}: Nomor kontrak hanya boleh berisi huruf, angka, dan tanda '-'.")
                invalid_contracts.append(contract_number)
            elif contract_number in seen_contracts:
                errors.append(f"Baris {i+1}: Duplikat nomor kontrak dalam file: {contract_number}")
                invalid_contracts.append(contract_number)
            elif contract_number in existing_contracts:
                errors.append(f"Baris {i+1}: Nomor kontrak sudah ada di database: {contract_number}")
                duplicate_in_db.append(contract_number)
                invalid_contracts.append(contract_number)
            else:
                seen_contracts.add(contract_number)

            chassis_val = first_present(row, CHASSIS_FIELD_ALIASES)
            engine_val  = first_present(row, ENGINE_FIELD_ALIASES)
            bad_fields = []
            if contains_forbidden(chassis_val):
                bad_fields.append("chassis_number")
            if contains_forbidden(engine_val):
                bad_fields.append("engine_number")
            if bad_fields:
                errors.append(
                    f"Baris {i+1}: Nilai tidak valid pada {', '.join(bad_fields)} "
                    f"(mengandung pola terlarang: SD1–SD5, 1SD–5SD, EUC1–EUC5, 1EUC–5EUC)."
                )

            excel_code, excel_name = row_raw.get('branch_code'), row_raw.get('branch_name')
            if not s(excel_code):
                excel_code = branch_code_from_agreement(row_raw.get('agreement_number'))
            branch_label = s(excel_name) or s(excel_code)
            b_obj = find_branch_by_code_or_name(excel_code, excel_name)
            if b_obj:
                if is_staff_cabang and b_obj.branch_code not in user_branch_codes:
                    errors.append(f"Baris {i+1}: Cabang '{branch_label}' tidak termasuk akses user.")
                else:
                    row['branch_code'] = b_obj.branch_code
                    row['branch_name'] = b_obj.branch_name
            elif branch_label:
                errors.append(f"Baris {i+1}: Cabang '{branch_label}' tidak terdaftar di Cabang Periksa Kembali Excel Anda")
            elif is_staff_cabang and user_branch:
                warnings.append(
                    f"Baris {i+1}: Cabang kosong. Akan memakai cabang user ({user_branch.branch_code}) saat simpan."
                )
                row['branch_code'] = user_branch.branch_code
                row['branch_name'] = user_branch.branch_name
            else:
                errors.append(f"Baris {i+1}: Kolom cabang wajib diisi (BRANCH atau NAMA CABANG).")

            preview_rows.append(row)

        filtered_headers = original_headers

        rows_for_preview = []
        for row in preview_rows:
            row_excel = {}
            for header in filtered_headers:
                model_field = FIELD_MAPPING_NORMALIZED.get(normalize_header(header))
                row_excel[header] = row.get(model_field, '') if model_field else ''
            rows_for_preview.append(row_excel)

        if errors or duplicate_in_db:
            parts = []

            if duplicate_in_db:
                parts.append(f"Duplikat No Kontrak: {', '.join(sorted(set(duplicate_in_db)))}")

            branch_required = [e for e in errors if "kolom cabang wajib diisi" in e.lower()]
            branch_not_found = [e for e in errors if "tidak terdaftar di cabang" in e.lower()]

            if branch_required:
                parts.append(f"{len(branch_required)} baris tanpa cabang")
            if branch_not_found:
                parts.append(f"{len(branch_not_found)} baris cabang tidak terdaftar")

            headline = " ; ".join(parts) if parts else (str(errors[0]) if errors else "")

            if headline:
                headline = headline.strip() + ". Cek kembali Excel Anda."
            else:
                headline = "Cek kembali Excel Anda."

            return Response({
                'error': headline,
                'error_count': len(errors),
                'errors': errors,
                'warnings': warnings,
                'invalid_contracts': invalid_contracts,
                'duplikat_di_database': duplicate_in_db,
                'branch_errors': {
                    'missing_branch_required': len(branch_required),
                    'branch_not_found': len(branch_not_found),
                },
                'all_errors': "; ".join(errors) if errors else ""
            }, status=400)

        return Response({
            'columns': filtered_headers,
            'rows': rows_for_preview,
            'warnings': warnings,
            'message': 'Preview berhasil dimuat.'
        }, status=200)

    except Exception as e:
        import traceback
        logger.error(f"❌ Failed to process Excel: {e}\n{traceback.format_exc()}")
        return Response({'error': 'Gagal memproses file Excel. Periksa format dan isi file Anda.'}, status=400)
    
def _norm_agreement(v):
    s = '' if v is None else str(v).strip()
    return re.sub(r'\D', '', s) or None

logger.warning(f"in_atomic_block={connection.in_atomic_block}, autocommit={connection.get_autocommit()}")

def _to_date(v):
    if v in (None, '', '-'):
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        v = v.strip()
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(v, fmt).date()
            except ValueError:
                continue
    return None

def _to_int(v):
    if v in (None, '', '-'):
        return None
    if isinstance(v, (int,)):
        return v
    try:
        s = str(v).replace('Rp', '').replace('.', '').replace(',', '').strip()
        return int(s) if s else None
    except Exception:
        return None

def _s(v):  # stringify aman
    return '' if v is None else str(v).strip()

def _pad3(v):
    v = _s(v)
    if v in ('', '-'):
        return v
    return v.zfill(3) if v.isdigit() and len(v) < 3 else v

def _normalize_phone(v):
    v = _s(v)
    return '0000000' if v == '0' else v

def _db_alias_for(model_cls):
    return getattr(settings, 'DB_ALIAS_WRITER', 'default')

CUSTOMER_TYPE_MAP = {
        'personal': 'I', 'individu': 'I', 'individual': 'I',
        'company': 'C', 'corporate': 'C', 'perusahaan': 'C',
        'i': 'I', 'c': 'C', 'i/c': 'I/C',
        'bpkb': 'BPKB', 'debitur': 'DEBITUR', 'debitor': 'DEBITUR'
    }

def normalize_customer_type(v):
    key = _s(v).lower()
    out = CUSTOMER_TYPE_MAP.get(key, _s(v))
    return out.upper() if isinstance(out, str) else out

def get_val(item_excel_or_model: dict, model_field: str):
        if model_field in item_excel_or_model:
            return item_excel_or_model.get(model_field)
        for excel_header, mapped in FIELD_MAPPING.items():
            if mapped == model_field and excel_header in item_excel_or_model:
                return item_excel_or_model.get(excel_header)
        return None
    
def normalize_agreement_type(v):
    v = str(v).strip()
    val = v.lower()

    if 'retail consumer financing' in val:
        return 'KONVE'
    elif 'syariah' in val:
        return 'SYARIAH'
    else:
        return v.upper()
    
def resolve_branch_code(val):
    val = _s(val)
    if not val:
        return None
    norm = _pad3(val) if val.isdigit() else val
    b = Branch.objects.filter(branch_code__iexact=norm).first()
    if b: return b.branch_code
    b = Branch.objects.filter(branch_name__iexact=val).first()
    if b: return b.branch_code
    logger.warning(f"[BRANCH] Tidak ditemukan dari Excel input='{val}' (norm='{norm}')")
    return None

def handle_rpa_import(request, data_list, filename, file_url_upload):
    from AdminPnbp.services import PnbpRateResolver, apply_dynamic_pnbp

    user = request.user
    db_alias = _db_alias_for(Consumer)
    pnbp_resolver = PnbpRateResolver.from_database(using=db_alias)

    success_count = 0
    skipped = []
    errors = []

    consumers_to_create = []
    documents_to_create = []
    statuses_to_create = []
    logs_to_create = []

    existing_agreements = set(
        Consumer.objects.using(db_alias)
        .values_list('agreement_number', flat=True)
    )

    batch_agreements = set()

    # ==========================
    # CACHE BRANCH
    # ==========================

    branch_map = {
        b.branch_name.strip().lower(): b
        for b in Branch.objects.all()
    }

    try:
        with transaction.atomic(using=db_alias):

            for idx, raw_item in enumerate(data_list, start=1):

                agreement_number = _s(
                    raw_item.get('agreement_number')
                    or get_val(raw_item, 'agreement_number')
                )

                if not agreement_number:
                    skipped.append({
                        'row': idx,
                        'agreement_number': '-',
                        'message': 'agreement_number kosong'
                    })
                    continue

                if agreement_number in existing_agreements:
                    skipped.append({
                        'row': idx,
                        'agreement_number': agreement_number,
                        'message': 'Agreement number already exists (DB)'
                    })
                    continue

                if agreement_number in batch_agreements:
                    skipped.append({
                        'row': idx,
                        'agreement_number': agreement_number,
                        'message': 'Duplicate in same file'
                    })
                    continue

                batch_agreements.add(agreement_number)

                # ==========================
                # BUILD ITEM
                # ==========================

                item = {}
                for excel_col, value in raw_item.items():
                    norm_col = normalize_header(excel_col)
                    model_field = FIELD_MAPPING_NORMALIZED.get(norm_col)

                    if not model_field:
                        continue

                    item[model_field] = value

                # ==========================
                # BRANCH RESOLVE
                # ==========================

                excel_branch = (
                    raw_item.get('branch_name')
                    or item.get('branch_name')
                )

                branch_obj = None

                if excel_branch:
                    branch_obj = branch_map.get(
                        str(excel_branch).strip().lower()
                    )

                if not branch_obj:
                    skipped.append({
                        'row': idx,
                        'agreement_number': agreement_number,
                        'message': f"Branch tidak ditemukan: {excel_branch}"
                    })
                    continue

                consumer = Consumer(
                    agreement_number=agreement_number,
                    uploader=user,
                    upload_date=timezone.now().date(),
                    branch=branch_obj,
                    branch_name=branch_obj.branch_name
                )

                for field_name, value in item.items():

                    if not hasattr(consumer, field_name):
                        continue

                    field_obj = Consumer._meta.get_field(field_name)

                    if field_name == 'customer_type' and value:
                        value = normalize_customer_type(value)

                    if field_name == 'agreement_type' and value:
                        value = normalize_agreement_type(value)

                    if field_name in ['rt', 'rw', 'bpkb_rt', 'bpkb_rw']:
                        value = _pad3(value)

                    if field_name == 'phone_number':
                        value = _normalize_phone(value)

                    if isinstance(field_obj, models.DateField):
                        value = _to_date(value)

                    if not isinstance(field_obj, (models.CharField, models.TextField)):
                        if value == '':
                            value = None

                    setattr(consumer, field_name, value)

                # ==========================
                # RT RW FORMAT
                # ==========================

                rt = _pad3(item.get('rt'))
                rw = _pad3(item.get('rw'))

                if rt or rw:
                    consumer.rt_rw = f"{rt}/{rw}".strip('/')

                apply_dynamic_pnbp(
                    consumer,
                    using=db_alias,
                    resolver=pnbp_resolver,
                )
                consumers_to_create.append(consumer)

            # ==========================
            # BULK CREATE CONSUMER
            # ==========================

            created_consumers = Consumer.objects.using(db_alias).bulk_create(
                consumers_to_create,
                batch_size=1000
            )

            # ==========================
            # BUILD RELATED TABLES
            # ==========================

            for consumer in created_consumers:

                consumer_name = (
                    getattr(consumer, 'name_on_bpkb', None)
                    or getattr(consumer, 'customer_name', None)
                    or getattr(consumer, 'name', None)
                    or '-'
                )

                documents_to_create.append(
                    ConsumerDocument(
                        agreement_number=consumer.agreement_number,
                        consumer_name=consumer_name,
                        agreement_date=consumer.agreement_date or timezone.now().date(),
                        document_type='Excel Upload',
                        description=f'Imported by {user.username} from {filename or "-"}',
                        file_url='',
                        modified_by_id=getattr(user, 'id', None),
                    )
                )

                statuses_to_create.append(
                    ConsumerStatus(
                        consumer=consumer,
                        status='Check',
                        updated_by=user
                    )
                )

                logs_to_create.append(
                    ActivityLog(
                        log_name='CONSUMER_REG_UPLOAD',
                        description='created',
                        causer=user,
                        causer_type='AdminUser.User',
                        subject_id=consumer.id,
                        subject_type='Consumer',
                        properties={
                            "agreement_number": consumer.agreement_number,
                            "branch_name": consumer.branch_name,
                            "filename": filename or '-'
                        }
                    )
                )

            ConsumerDocument.objects.bulk_create(documents_to_create, batch_size=1000)
            ConsumerStatus.objects.bulk_create(statuses_to_create, batch_size=1000)
            ActivityLog.objects.bulk_create(logs_to_create, batch_size=1000)

            success_count = len(created_consumers)
            
            if filename and success_count > 0:
                try:
                    file_url = file_url_upload or f"/media/uploads/{filename}"

                    def _company_name(u):
                        comp = getattr(u, "company", None)
                        if hasattr(comp, "name"):
                            return comp.name
                        if isinstance(comp, str):
                            return comp
                        return None

                    uploader_company = _company_name(user)

                    fp = FileProcessings.objects.create(
                        file_name=filename,
                        status='Terkirim',
                        old_path='',
                        new_location=file_url,
                        uploader_company=uploader_company,
                        modified_by=user,
                        created_at=timezone.now(),
                        updated_at=timezone.now(),
                        category="1"
                    )

                    ActivityLog.objects.create(
                        log_name='UPLOAD_FILE',
                        description=f'Upload Excel RPA berhasil ({success_count} baris)',
                        causer=user,
                        causer_type='AdminUser.User',
                        subject_id=fp.id,
                        subject_type='FileProcessings',
                        properties={
                            "file_name": filename,
                            "new_location": file_url,
                            "success_count": success_count,
                            "skipped_count": len(skipped),
                            "error_count": len(errors),
                        }
                    )

                except Exception as e:
                    print(f"❌ Gagal simpan FileProcessing (RPA): {e}")

    except Exception as e:
        return Response({'error': str(e)}, status=500)

    return Response({
        'success_count': success_count,
        'skipped_count': len(skipped),
        'error_count': len(errors),
        'filename': filename,
        'skipped': skipped,
        'errors': errors
    })

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
@parser_classes([MultiPartParser])
@transaction.non_atomic_requests
def save_excel_data(request):
    if not user_has_permission(request.user, 'import_staff_cabang'):
        return Response({'message': 'Forbidden'}, status=403)

    uploaded_file = request.FILES.get('file')
    saved_filename = None
    file_url_upload = None
    if uploaded_file:
        fs = FileSystemStorage(location=os.path.join(settings.MEDIA_ROOT, 'uploads'))
        saved_filename = fs.save(uploaded_file.name, uploaded_file)
        filename = uploaded_file.name
        file_url_upload = f"{settings.MEDIA_URL}uploads/{saved_filename}"
        logger.info(f"📁 File disimpan ke: {os.path.join('media/uploads', saved_filename)}")
    else:
        filename = request.data.get('filename')
        if filename:
            file_url_upload = f"/media/uploads/{filename}"

    try:
        raw_data = request.data.get('data')
        data_list = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
    except Exception as e:
        logger.error(f"❌ Gagal parsing data JSON: {e}")
        return Response({'error': 'Format data tidak valid.'}, status=status.HTTP_400_BAD_REQUEST)
    if not data_list:
        return Response({'error': 'No data provided.'}, status=status.HTTP_400_BAD_REQUEST)

    user = request.user
    role_names = list(get_user_role_names(user))
    is_privileged = (
        has_role(role_names, ROLE_KEYS["SUPERADMIN"])
        or has_role(role_names, ROLE_KEYS["HO"])
        or has_role(role_names, ROLE_KEYS["OPERATOR"])
        or has_role(role_names, ROLE_KEYS["RPA"])
        or getattr(user, "is_superuser", False)
    )
    is_ho = is_privileged
    is_rpa = has_role(role_names, ROLE_KEYS["RPA"])
    is_staff_cabang = user_is_branch_restricted(user)
    
    if is_rpa:
        return handle_rpa_import(
            request,
            data_list,
            saved_filename or filename,
            file_url_upload
        )

    user_branch_codes = get_user_branch_codes(user)
    user_branch = Branch.objects.filter(branch_code=user_branch_codes[0]).first() if len(user_branch_codes) == 1 else None
    if not is_privileged and not user_branch_codes:
        return Response({'message': 'Branch wajib diisi untuk user non-HO.'}, status=403)

    allowed_fields = [
        f.name for f in Consumer._meta.fields
        if f.name not in ['id', 'created_at', 'updated_at', 'branch']
    ]
    
    FORBIDDEN_SUBSTRINGS = {
        "SD1","SD2","SD3","SD4","SD5", "SD", "1SD", "2SD", "3SD", "4SD", "5SD",
        "EUC1","EUC2","EUC3","EUC4","EUC5", "EUC", "1EUC", "2EUC", "3EUC", "4EUC", "5EUC"
    }
    
    CHASSIS_FIELD_ALIASES = ["chassis_number", "no_rangka", "frame_number", "vin"]
    ENGINE_FIELD_ALIASES  = ["engine_number", "no_mesin", "engine_no"]
    
    def _contains_forbidden(val: str) -> bool:
        s = _s(val).upper().replace(" ", "")
        if not s:
            return False
        return any(s.startswith(bad) for bad in FORBIDDEN_SUBSTRINGS)

    def _first_present(d: dict, keys: list[str]):
        for k in keys:
            if k in d and _s(d.get(k)) not in ("", "-"):
                return d.get(k)
        return None

    def find_branch(val):
        val = _s(val)
        if not val:
            return None
        if val.isdigit():
            norm = _pad3(val)
            b = Branch.objects.filter(branch_code__iexact=norm).first()
            if b: return b
        b = Branch.objects.filter(branch_name__iexact=val).first()
        if b: return b
        return Branch.objects.filter(branch_name__icontains=val).first()

    TEMPLATE_FIELDS = [mf for mf in FIELD_MAPPING.values() if mf]

    def _guess_consumer_name(consumer_obj, item_dict):
        return (
            getattr(consumer_obj, 'name_on_bpkb', None)
            or getattr(consumer_obj, 'customer_name', None)
            or getattr(consumer_obj, 'name', None)
            or _s(item_dict.get('name_on_bpkb'))
            or _s(item_dict.get('customer_name'))
            or _s(item_dict.get('name'))
            or '-'
        )

    success_count, skipped, errors = 0, [], []
    
    viol_rows = []
    for idx, raw_item in enumerate(data_list, start=1):
        item = {}
        for excel_col, value in raw_item.items():
            norm_col = normalize_header(excel_col)
            model_field = FIELD_MAPPING_NORMALIZED.get(norm_col)

            if not model_field:
                continue

            item[model_field] = value

        chassis_val = _first_present(item, CHASSIS_FIELD_ALIASES) or _first_present(raw_item, CHASSIS_FIELD_ALIASES)
        engine_val  = _first_present(item, ENGINE_FIELD_ALIASES)  or _first_present(raw_item, ENGINE_FIELD_ALIASES)

        bad_fields = []
        if _contains_forbidden(chassis_val):
            bad_fields.append("chassis_number")
        if _contains_forbidden(engine_val):
            bad_fields.append("engine_number")

        if bad_fields:
            agno = _s(
                item.get('agreement_number') or
                raw_item.get('agreement_number') or
                get_val(raw_item, 'agreement_number') or
                '-'
            )
            errors.append({
                'row': idx,
                'agreement_number': agno,
                'error': (
                    "Batch ditolak: ditemukan pola terlarang pada "
                    f"{', '.join(bad_fields)} (SD1–SD5, 1SD–5SD, EUC1–EUC5, 1EUC–5EUC, termasuk 'SD'/'EUC')."
                )
            })
            viol_rows.append(idx)

    if viol_rows and not is_rpa:
        logger.warning(f"❌ Batch dibatalkan: {len(viol_rows)} baris mengandung pola terlarang. Tidak ada data yang disimpan.")
        return Response({
            'success_count': 0,
            'skipped_count': 0,
            'error_count': len(errors),
            'filename': saved_filename or filename,
            'skipped': [],
            'errors': errors
        }, status=status.HTTP_207_MULTI_STATUS)

    for idx, raw_item in enumerate(data_list, start=1):
        try:
            with transaction.atomic(using=_db_alias_for(Consumer)):
                item = {}
                for excel_col, value in raw_item.items():
                    norm_col = normalize_header(excel_col)
                    model_field = FIELD_MAPPING_NORMALIZED.get(norm_col)

                    if not model_field:
                        continue

                    item[model_field] = value

                chassis_val = _first_present(item, CHASSIS_FIELD_ALIASES)
                engine_val  = _first_present(item, ENGINE_FIELD_ALIASES)

                if chassis_val in (None, "", "-"):
                    chassis_val = _first_present(raw_item, CHASSIS_FIELD_ALIASES)
                if engine_val in (None, "", "-"):
                    engine_val  = _first_present(raw_item, ENGINE_FIELD_ALIASES)

                bad_fields = []
                if _contains_forbidden(chassis_val):
                    bad_fields.append("chassis_number")
                if _contains_forbidden(engine_val):
                    bad_fields.append("engine_number")

                if bad_fields and not is_rpa:
                    errors.append({
                        'row': idx,
                        'agreement_number': agreement_number or '-',
                        'error': f"Invalid value on {', '.join(bad_fields)}: " f"mengandung pola terlarang (SD1-SD5, 1SD-5SD, EUC1-EUC5, 1EUC-5EUC)."
                    })
                    continue

                for fld in TEMPLATE_FIELDS:
                    if _s(item.get(fld, '')) == '':
                        item[fld] = '-'

                for f in ['rt','rw','bpkb_rt','bpkb_rw']:
                    if f in item:
                        item[f] = _pad3(item.get(f))
                if 'phone_number' in item:
                    item['phone_number'] = _normalize_phone(item.get('phone_number'))
                for k, v in MIZUHO_DEFAULTS.items():
                    if _s(item.get(k)) in ('', '-'):
                        item[k] = v

                agreement_number = _s(item.get('agreement_number'))
                if agreement_number in ('', '-'):
                    skipped.append({'row': idx, 'agreement_number': '-', 'message': 'agreement_number kosong, skipped.'})
                    continue

                for df in ['birth_date','agreement_date','go_live_date','spb_date','installment_end_date',
                        'bpkb_birth_date','deed_date','decree_date','director_birth_date','installment_start_date',
                        'certificate_date','upload_date']:
                    if df in item:
                        item[df] = _to_date(item.get(df))
                if not item.get('upload_date'):
                    item['upload_date'] = timezone.now().date()
                if not item.get('agreement_date'):
                    item['agreement_date'] = timezone.now().date()

                for nf in ['guarantee_value','service_fee','pnbp_fee','production_year','tenor']:
                    if nf in item:
                        item[nf] = _to_int(item.get(nf))

                excel_code = get_val(raw_item, 'branch_code')
                excel_name = get_val(raw_item, 'branch_name')
                if not _s(excel_code):
                    excel_code = branch_code_from_agreement(agreement_number)
                branch_obj = find_branch_by_code_or_name(excel_code, excel_name)
                if not branch_obj and not (_s(excel_code) or _s(excel_name)) and is_staff_cabang:
                    branch_obj = user_branch
                if not branch_obj:
                    errors.append({'row': idx, 'agreement_number': agreement_number, 'error': f"Cabang '{_s(excel_name) or _s(excel_code)}' tidak terdaftar."})
                    continue
                if is_staff_cabang and branch_obj.branch_code not in user_branch_codes:
                    errors.append({'row': idx, 'agreement_number': agreement_number, 'error': f"Cabang '{branch_obj.branch_name}' tidak termasuk akses user."})
                    continue

                agmt_date = item.get('agreement_date')
                dupe_qs = Consumer.objects.using(_db_alias_for(Consumer)).filter(
                    agreement_number=agreement_number
                )

                if not is_rpa:
                    agmt_date = item.get('agreement_date')
                    dupe_qs = dupe_qs.filter(
                        agreement_date=agmt_date
                    ) if agmt_date else dupe_qs.filter(
                        agreement_date__isnull=True
                    )

                if dupe_qs.exists():
                    skipped.append({
                        'row': idx,
                        'agreement_number': agreement_number,
                        'message': 'Agreement number already exists.'
                    })
                    continue

                consumer = Consumer()
                for f in allowed_fields:
                    if f in item:
                        val = item[f]
                        field_obj = Consumer._meta.get_field(f)

                        if isinstance(field_obj, models.DateField):
                            val = _to_date(val)

                        if not isinstance(field_obj, (models.CharField, models.TextField, models.SlugField, models.JSONField)):
                            if val in ('', '-'):
                                val = None

                        setattr(consumer, f, val)

                if branch_obj:
                    consumer.branch = branch_obj
                    consumer.branch_name = branch_obj.branch_name

                rt = item.get('rt') or ''
                rw = item.get('rw') or ''
                if rt or rw:
                    consumer.rt_rw = f"{_s(rt)}/{_s(rw)}".strip('/')

                consumer.uploader = request.user
                consumer.updated_at = timezone.now()
                if not consumer.upload_date:
                    consumer.upload_date = timezone.now().date()

                if getattr(consumer, 'production_year', None):
                    try:
                        consumer.assembly_year = str(consumer.production_year)[:4]
                    except Exception:
                        consumer.assembly_year = None

                if not is_rpa:
                    consumer.full_clean(exclude=None)
                consumer.save(using=_db_alias_for(Consumer))

                try:
                    consumer_name = (
                        getattr(consumer, 'debtor_name', None)
                        or getattr(consumer, 'name_on_bpkb', None)
                        or getattr(consumer, 'customer_name', None)
                        or getattr(consumer, 'name', None)
                        or _s(item.get('name_on_bpkb')) or _s(item.get('customer_name')) or _s(item.get('name')) or '-'
                    )
                    doc_defaults = {
                        'consumer_name': consumer_name,
                        'agreement_date': consumer.agreement_date or item.get('agreement_date') or timezone.now().date(),
                        'document_type': 'Excel Upload',
                        'description': f'Imported by {user.username} from {filename or "-"}',
                        'notes': None,
                        'modified_by_id': getattr(user, 'id', None),
                    }

                    doc_qs = ConsumerDocument.objects.using(_db_alias_for(ConsumerDocument))
                    doc, created_doc = doc_qs.get_or_create(
                        agreement_number=consumer.agreement_number,
                        defaults=doc_defaults,
                    )

                    if not created_doc:
                        update_fields = []
                        if doc.consumer_name != consumer_name:
                            doc.consumer_name = consumer_name; update_fields.append('consumer_name')
                        new_date = consumer.agreement_date or item.get('agreement_date') or doc.agreement_date or timezone.now().date()
                        if doc.agreement_date != new_date:
                            doc.agreement_date = new_date; update_fields.append('agreement_date')
                        if doc.document_type != 'Excel Upload':
                            doc.document_type = 'Excel Upload'; update_fields.append('document_type')
                        new_desc = f'Imported by {user.username} from {filename or "-"}'
                        if doc.description != new_desc:
                            doc.description = new_desc; update_fields.append('description')
                        if doc.file_url != file_url_upload:
                            doc.file_url = file_url_upload; update_fields.append('file_url')
                        if getattr(doc, 'modified_by_id', None) != getattr(user, 'id', None):
                            doc.modified_by_id = getattr(user, 'id', None); update_fields.append('modified_by')
                        doc.updated_at = timezone.now(); update_fields.append('updated_at')

                        if update_fields:
                            doc.save(using=_db_alias_for(ConsumerDocument), update_fields=update_fields)

                    logger.info(f"🧾 consumer_documents {'created' if created_doc else 'updated'} " f"for agreement={consumer.agreement_number} (doc_id={getattr(doc, 'id', None)})")
                except Exception as e:
                    logger.error(f"❌ consumer_documents upsert failed for agreement={consumer.agreement_number}: {e}")
                    raise

                ConsumerStatus.objects.using(_db_alias_for(Consumer)).create(consumer=consumer, status='Check', updated_by=user)
                ActivityLog.objects.using(_db_alias_for(ActivityLog)).create(
                    log_name='CONSUMER_REG_UPLOAD',
                    description='created',
                    causer=user,
                    causer_type='AdminUser.User',
                    subject_id=consumer.id,
                    subject_type='Consumer',
                    properties={
                        "agreement_number": consumer.agreement_number,
                        "branch_name": consumer.branch_name,
                        "filename": filename or '-'
                    }
                )

                success_count += 1
                logger.info(f"[Row {idx}] ✅ OK: {agreement_number}")

        except IntegrityError as e:
            diag = getattr(getattr(e, '__cause__', None), 'diag', None)
            constraint = getattr(diag, 'constraint_name', None) if diag else None
            errors.append({'row': idx, 'agreement_number': agreement_number, 'error': f"Integrity error: {constraint or str(e)}"})
        except DatabaseError as e:
            cause = getattr(e, '__cause__', None)
            diag  = getattr(cause, 'diag', None)
            msg   = getattr(diag, 'message_detail', None) or getattr(cause, 'pgerror', None) or str(e)
            errors.append({'row': idx, 'agreement_number': agreement_number, 'error': msg})
        except Exception as e:
            errors.append({'row': idx, 'agreement_number': agreement_number, 'error': str(e)})

    if (saved_filename or filename) and success_count > 0:
        try:
            file_url = file_url_upload or f"/media/uploads/{saved_filename or filename}"
            def _company_name(u):
                comp = getattr(u, "company", None)
                if hasattr(comp, "name"):
                    return comp.name
                if isinstance(comp, str):
                    return comp
                return None
            uploader_company = _company_name(request.user)

            fp = FileProcessings.objects.create(
                file_name=saved_filename or filename,
                status='Terkirim',
                old_path='',
                new_location=file_url,
                uploader_company=uploader_company,
                modified_by=request.user,
                created_at=timezone.now(),
                updated_at=timezone.now(),
                category="1"
            )
            ActivityLog.objects.create(
                log_name='UPLOAD_FILE',
                description=f'Upload Excel berhasil ({success_count} baris)',
                causer=request.user,
                causer_type='AdminUser.User',
                subject_id=fp.id,
                subject_type='FileProcessings',
                properties={
                    "file_name": saved_filename or filename,
                    "new_location": file_url,
                    "success_count": success_count,
                    "skipped_count": len(skipped),
                    "error_count": len(errors),
                }
            )
        except Exception as doc_error:
            logger.error(f"❌ Gagal menyimpan log file_processings: {doc_error}")

    logger.info(f" 📊 Upload selesai: {success_count} sukses, {len(skipped)} dilewati, {len(errors)} error")
    return Response({
        'success_count': success_count,
        'skipped_count': len(skipped),
        'error_count': len(errors),
        'filename': saved_filename or filename,
        'skipped': skipped,
        'errors': errors
    }, status=status.HTTP_200_OK if not errors else status.HTTP_207_MULTI_STATUS)

# Field mapping format order baku Mizuho (70 kolom, A–BR).
# Nilai None = kolom dikenali tetapi diabaikan saat import.
# Beberapa kolom TAF dipakai ulang untuk data Mizuho (lihat requirement §12):
#   spb_date = tanggal kuasa, director_* = data pasangan, house_number = no. polisi.
FIELD_MAPPING = {
    'BRANCH': 'branch_code',
    'NAMA CABANG': 'branch_name',

    # Pemberi fidusia (= debitur)
    'PEMBERI FIDUSIA': 'debtor_name',
    'JENIS KELAMIN PEMBERI FIDUSIA': 'gender',
    'MARITAL STATUS PEMBERI FIDUSIA': 'marital_status',
    'TEMPAT LAHIR': 'birth_place',
    'TANGGAL LAHIR': 'birth_date',
    'PEKERJAAN': 'occupation',
    'ALAMAT': 'address',
    'RT': 'rt',
    'RW': 'rw',
    'KELURAHAN': 'subdistrict',
    'KECAMATAN': 'district',
    'KABUPATEN': 'city',
    'PROVINSI': 'province',
    'IDENTITAS NUMBER PEMBERI FIDUSIA': 'id_number',
    'TANGGAL KUASA': 'spb_date',
    'KODE POS PEMBERI FIDUSIA': 'postal_code',
    'NO HANDPHONE PEMBERI FIDUSIA': 'phone_number',

    # Pasangan (hanya terisi bila MARRIED)
    'PASANGAN PEMBERI FIDUSIA': 'spouse_name',
    'JENIS KELAMIN PASANGAN PEMBERI FIDUSIA': 'director_gender',
    'MARITAL STATUS PASANGAN PEMBERI FIDUSIA': 'director_status',
    'TEMPAT LAHIR PASANGAN': 'director_birth_place',
    'TANGGAL LAHIR PASANGAN': 'director_birth_date',

    # Alamat fidusia & data debitur: selalu sama dengan pemberi fidusia
    'ALAMAT FIDUSIA': None,
    'RT FIDUSIA': None,
    'RW FIDUSIA': None,
    'KELURAHAN FIDUSIA': None,
    'KECAMATAN FIDUSIA': None,
    'KABUPATEN FIDUSIA': None,
    'PROVINSI FIDUSIA': None,
    'IDENTITAS NUMBER FIDUSIA': None,
    'NAMA DEBITUR': None,
    'JENIS KELAMIN DEBITUR': None,
    'MARITAL STATUS DEBITUR': None,
    'TEMPAT LAHIR DEBITUR': None,
    'TANGGAL LAHIR DEBITUR': None,
    'ALAMAT DEBITUR': None,
    'RT DEBITUR': None,
    'RW DEBITUR': None,
    'KELURAHAN DEBITUR': None,
    'KECAMATAN DEBITUR': None,
    'KABUPATEN DEBITUR': None,
    'PROVINSI DEBITUR': None,
    'IDENTITAS NUMBER DEBITUR': None,

    # Perjanjian & nilai
    'NOMOR KONTRAK': 'agreement_number',
    'TANGGAL KONTRAK': 'agreement_date',
    'HUTANG POKOK': 'principal_loan',
    'NILAI JAMINAN': 'guarantee_value',
    'NILAI BARANG': 'otr_value',
    'TENOR': 'tenor',
    'MULAI ANGSURAN': 'installment_start_date',
    'BERAKHIR ANGSURAN': 'installment_end_date',

    # Kendaraan
    'MERK': 'brand',
    'TYPE': 'type',
    'TAHUN': 'production_year',  # di-mirror ke assembly_year saat simpan
    'WARNA': 'color',
    'NOMOR RANGKA': 'chassis_number',
    'NOMOR MESIN': 'engine_number',
    'NOMOR BPKB/ COVER NOTE': 'bpkb_number',
    'NAMA_BPKB': 'name_on_bpkb',  # kolom BO "NAMA BPKB" = duplikat, dibuang saat baca
    'ATAS NAMA KUITANSI': 'receipt_name',
    'NO POL': 'house_number',
    'TIPE': 'vehicle_condition',
    'NO BPKB': None,

    # Diisi sistem untuk file feedback, selalu kosong saat order
    'NO AKTA': None,
    'AKTA DATE': None,
    'NO SERTIFIKAT': None,
    'SERTIFIKAT DATE': None,
}

# Nilai default yang tidak ada di Excel Mizuho
MIZUHO_DEFAULTS = {
    'customer_type': 'I',
    'agreement_type': 'KONVE',
    'nationality': 'WNI',
    'fiducia_provider': 'Atas Nama Customer',
}

# Kolom yang wajib terisi; baris ditolak bila kosong
MIZUHO_REQUIRED_FIELDS = {
    'agreement_number': 'NOMOR KONTRAK',
    'debtor_name': 'PEMBERI FIDUSIA',
    'id_number': 'IDENTITAS NUMBER PEMBERI FIDUSIA',
    'guarantee_value': 'NILAI JAMINAN',
    'chassis_number': 'NOMOR RANGKA',
    'engine_number': 'NOMOR MESIN',
}

# Kolom yang boleh kosong tanpa peringatan
MIZUHO_OPTIONAL_FIELDS = {
    'spouse_name', 'director_gender', 'director_status',
    'director_birth_place', 'director_birth_date', 'receipt_name',
    'branch_code',  # kosong -> diambil dari 4 digit pertama nomor kontrak
}

MIZUHO_DATE_FIELDS = {
    'birth_date': 'TANGGAL LAHIR',
    'spb_date': 'TANGGAL KUASA',
    'director_birth_date': 'TANGGAL LAHIR PASANGAN',
    'agreement_date': 'TANGGAL KONTRAK',
    'installment_start_date': 'MULAI ANGSURAN',
    'installment_end_date': 'BERAKHIR ANGSURAN',
}

AGREEMENT_NUMBER_RE = re.compile(r'[0-9A-Za-z]+(-[0-9A-Za-z]+)*')
ADDRESS_RT_RW_RE = re.compile(r'\bRT\b.*\bRW\b')

def normalize_date_str(v):
    """Ubah nilai tanggal dari Excel (mis. '2001-10-14 00:00:00', '14/10/2001') ke 'YYYY-MM-DD'.
    Nilai yang tidak dikenali dikembalikan apa adanya agar tertangkap validasi."""
    val = '' if v is None else str(v).strip()
    if val in ('', '-'):
        return val
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(val, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return val

def branch_code_from_agreement(agreement_number):
    """Kode cabang Mizuho = 4 digit pertama nomor kontrak (mis. '0015014718-001' -> '0015')."""
    head = _s(agreement_number)[:4]
    return head if len(head) == 4 and head.isdigit() else ''

def find_branch_by_code_or_name(code, name):
    """Cari cabang berdasarkan kode lalu nama (kode Mizuho belum tentu ada di master)."""
    code, name = _s(code), _s(name)
    if code:
        b = Branch.objects.filter(branch_code__iexact=code).first()
        if b:
            return b
    if name:
        b = Branch.objects.filter(branch_name__iexact=name).first()
        if b:
            return b
        return Branch.objects.filter(branch_name__icontains=name).first()
    return None

def mizuho_row_warnings(row):
    """Peringatan data (tidak menolak upload), requirement §11."""
    warnings = []
    nik = _s(row.get('id_number'))
    if nik not in ('', '-'):
        digits = re.sub(r'\D', '', nik)
        if len(digits) != 16:
            warnings.append(f"NIK harus 16 digit (terisi {len(digits)} digit).")
    chassis = _s(row.get('chassis_number')).replace(' ', '')
    if chassis not in ('', '-') and len(chassis) != 17:
        warnings.append(f"Nomor rangka umumnya 17 karakter (terisi {len(chassis)}).")
    if ADDRESS_RT_RW_RE.search(_s(row.get('address')).upper()):
        warnings.append("Alamat memuat teks RT/RW; berpotensi tercetak dobel di akta.")
    return warnings

FIELD_MAPPING_NORMALIZED = {
    normalize_header(k): v for k, v in FIELD_MAPPING.items()
}

def parse_value(field_name, value):
    if value in ['', None]:
        return None
    try:
        field = Consumer._meta.get_field(field_name)
        if isinstance(field, models.DecimalField):
            return float(value)
        if isinstance(field, models.IntegerField):
            return int(value)
    except FieldDoesNotExist:
        pass
    return value

from django.shortcuts import render
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from django.contrib.auth.decorators import login_required
from .models import ConsumerMU, Branch
from .serializers import ConsumerSerializerMU
import json
from import_document.models import FileProcessings
import pandas as pd
from datetime import date, datetime
from django.db import models
from django.core.exceptions import FieldDoesNotExist
from dateutil import parser
from accesslogmu.models import ActivityLogMU
from import_document_mu.models import ConsumerDocumentMU
from django.db import connection
from rest_framework.parsers import JSONParser
from django.core.files.storage import FileSystemStorage
from rest_framework.permissions import IsAuthenticated
import os
from django.conf import settings
import logging
from django import template
from rest_framework.exceptions import PermissionDenied
from django.utils import timezone
from AdminMenu.auth_helpers import (
    class_permission_required,
    user_has_permission,
    get_user_permissions,
    permission_required,
    get_user_role_names
)
from common.const.role_map import has_role, ROLE_KEYS
from AdminUser.branch_access import (
    get_primary_user_branch_code,
    get_user_branch_codes,
    user_is_branch_restricted,
)
from common.validators import validate_excel_file_or_error

logger = logging.getLogger(__name__)

register = template.Library()

@register.filter
def user_permissions(user):
    return get_user_permissions(user)

@permission_required('read-upload-mu')
@login_required(login_url='')
def view_staff_cabang_import_mu(request):
    branches = Branch.objects.filter(consumermu__uploader=request.user).distinct()
    consumers = ConsumerMU.objects.filter(uploader=request.user)

    # ✅ Ambil permission & role user dan masukkan ke context
    user_permissions = list(get_user_permissions(request.user))
    user_roles = list(get_user_role_names(request.user))

    return render(request, 'staff_cabang/page-staff_cabang-import-mu.html', {
        'branches': branches,
        'consumer_list': consumers,
        'user_permissions': user_permissions,
        'user_roles': user_roles,
    })

class ConsumerViewSetMU(viewsets.ModelViewSet):
    serializer_class = ConsumerSerializerMU
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        logger.debug(f"User {self.request.user} accessing ConsumerViewSet")
        if not user_has_permission(self.request.user, 'read-upload-mu'):
            logger.warning("User not authorized, returning empty queryset.")
            return ConsumerMU.objects.none()
        return ConsumerMU.objects.filter(uploader=self.request.user)

    def get_object(self):
        obj = super().get_object()
        action = self.action
        permission_map = {
            'retrieve': 'read-upload-mu',
            'update': 'update-upload-mu',
            'partial_update': 'update-upload-mu',
            'destroy': 'delete-upload-mu',
        }

        needed_perm = permission_map.get(action)
        if needed_perm and not user_has_permission(self.request.user, needed_perm):
            raise PermissionDenied(f"You do not have permission to {action} this object.")
        return obj

    @class_permission_required('create-upload-mu')
    def create(self, request, *args, **kwargs):
        data = request.data.copy()
        data['uploader'] = request.user.id
        data['branch'] = get_primary_user_branch_code(request.user)
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @class_permission_required('update-upload-mu')
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @class_permission_required('update-upload-mu')
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @class_permission_required('delete-upload-mu')
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    @class_permission_required('read-upload-mu')
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
@parser_classes([MultiPartParser])
def preview_excel_mu(request):
    if not user_has_permission(request.user, 'create-upload-mu'):
        return Response({'message': 'Forbidden'}, status=403)

    excel_file = request.FILES.get('excel_file')
    if not excel_file:
        return Response({'error': 'No file uploaded.'}, status=status.HTTP_400_BAD_REQUEST)

    # Validate Excel file
    validation_error = validate_excel_file_or_error(excel_file)
    if validation_error:
        return validation_error

    ext = os.path.splitext(excel_file.name)[1].lower()
    logger.info(f"(MU) 📁 File Excel diterima: {excel_file.name}")

    # === Identitas user & role/branch (untuk validasi cabang di preview) ===
    user = request.user
    role_names = list(get_user_role_names(user))
    is_privileged = (
        has_role(role_names, ROLE_KEYS["SUPERADMIN"])
        or has_role(role_names, ROLE_KEYS["HO"])
        or has_role(role_names, ROLE_KEYS["OPERATOR"])
        or getattr(user, "is_superuser", False)
    )
    is_ho = is_privileged
    is_staff_cabang = user_is_branch_restricted(user)

    user_branch_codes = get_user_branch_codes(user)
    user_branch = Branch.objects.filter(branch_code=user_branch_codes[0]).first() if len(user_branch_codes) == 1 else None
    if not is_ho and not user_branch_codes:
        return Response({'message': 'Branch wajib diisi untuk user non-HO.'}, status=403)

    # --- Mapping header Excel -> field model (siapkan di settings/module yang sama)
    try:
        FIELD_MAPPING_MU  # jika belum ada, fallback ke FIELD_MAPPING
    except NameError:
        FIELD_MAPPING_MU = FIELD_MAPPING

    # === Helpers umum ===
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

        # toleransi penulisan
        b = Branch.objects.filter(branch_name__icontains=val).first()
        return b

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

        logger.warning(f"(MU)[BRANCH] Tidak ditemukan dari Excel input='{val}' (norm='{norm_code}')")
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
        v = str(v).strip()  # pastikan string
        val = v.lower()

        if 'retail consumer financing' in val:
            return 'KONVE'
        elif 'syariah' in val:
            return 'SYARIAH'
        else:
            return v.upper()
        
    FORBIDDEN_SUBSTRINGS = {
        # SDx and xSD
        "SD1","SD2","SD3","SD4","SD5", "SD", "1SD", "2SD", "3SD", "4SD", "5SD",
        # EUCx and xEUC
        "EUC1","EUC2","EUC3","EUC4","EUC5", "EUC", "1EUC", "2EUC", "3EUC", "4EUC", "5EUC"
    }

    # Alias kemungkinan header/field untuk no rangka & no mesin
    CHASSIS_FIELD_ALIASES = ["chassis_number", "no_rangka", "frame_number", "vin"]
    ENGINE_FIELD_ALIASES  = ["engine_number", "no_mesin", "engine_no"]

    def contains_forbidden(val: str) -> bool:
        if not val:
            return False
        # Normalisasi: abaikan spasi di depan & case-insensitive
        txt = str(val).upper().lstrip()   # hanya buang spasi di depan
        return any(txt.startswith(p) for p in FORBIDDEN_SUBSTRINGS)

    def first_present(d: dict, keys: list[str]):
        for k in keys:
            if k in d and s(d.get(k)) not in ("", "-"):
                return d.get(k)
        return None

    try:
        # --- Baca Excel
        if ext == '.xls':
            df = pd.read_excel(excel_file, engine='xlrd')
        elif ext == '.xlsx':
            df = pd.read_excel(excel_file, engine='openpyxl')
        else:
            return Response({'error': f'Unsupported file type: {ext}. Upload .xls or .xlsx only.'}, status=400)

        # --- Trim header
        df.columns = [str(col).strip() if col else '' for col in df.columns]
        original_headers = df.columns.tolist()
        logger.info(f"(MU) 📄 Header setelah strip: {original_headers}")

        # --- Fallback bila header invalid
        if original_headers.count('') >= 3 or all(str(h).startswith('Unnamed') for h in original_headers):
            logger.warning("(MU) ⚠️ Header kosong/tidak valid. Membaca ulang dengan header=1.")
            try:
                excel_file.seek(0)
            except Exception:
                pass
            df = pd.read_excel(excel_file, engine=('openpyxl' if ext == '.xlsx' else 'xlrd'), header=1)
            df.columns = [str(col).strip() if col else '' for col in df.columns]
            original_headers = df.columns.tolist()
            logger.info(f"(MU) 📄 Header fallback: {original_headers}")

        # --- Validasi kolom tanpa nama
        empty_headers = [i for i, h in enumerate(original_headers) if not h]
        if empty_headers:
            return Response({'error': f'Terdapat {len(empty_headers)} kolom tanpa nama di header Excel. '
                                      'Mohon isi nama kolom atau hapus kolom tidak terpakai.'}, status=400)

        # --- Validasi kolom tidak dikenal (harus ada di FIELD_MAPPING_MU)
        unknown_headers = [h for h in original_headers if h.strip() not in FIELD_MAPPING_MU]
        if unknown_headers:
            return Response({'error': f'Terdapat kolom tidak dikenali: {", ".join(unknown_headers)}. '
                                      'Periksa nama kolom agar sesuai template.'}, status=400)

        # --- Cek duplikasi mapping
        mapped_columns, seen_columns, duplicate_mapped = [], set(), []
        for col in original_headers:
            mapped = FIELD_MAPPING_MU.get(col)
            if mapped in seen_columns and mapped is not None:
                duplicate_mapped.append(mapped)
            else:
                seen_columns.add(mapped)
            mapped_columns.append(mapped)
        if duplicate_mapped:
            nice = [c if c else '(kolom kosong)' for c in duplicate_mapped]
            return Response({'error': f'Duplikat kolom setelah mapping: {", ".join(nice)}. '
                                      'Periksa header Excel agar tidak konflik.'}, status=400)

        # --- Rename kolom ke field model
        rename_map = {k: v for k, v in FIELD_MAPPING_MU.items() if v is not None}
        df_raw = df.rename(columns=rename_map)   # raw (NaN tetap NaN)
        df_filled = df_raw.fillna('')            # kosong -> ''
        for col in df_filled.select_dtypes(include=['datetime64[ns]']).columns:
            df_filled[col] = df_filled[col].dt.strftime('%Y-%m-%d')

        # --- Set kolom wajib (semua yg termapping)
        all_required_fields = list(df_raw.columns)

        # --- Blok wajib untuk BPKB & Debitur (opsional, sama dgn consumer)
        bpkb_required = [
            'bpkb_birth_place', 'bpkb_birth_date', 'bpkb_birth_date_words',
            'bpkb_occupation', 'bpkb_nationality', 'bpkb_address',
            'bpkb_rt', 'bpkb_rw', 'bpkb_subdistrict', 'bpkb_district',
            'bpkb_city', 'bpkb_province', 'bpkb_postal_code', 'bpkb_ktp'
        ]
        debtor_required = [
            'debtor_name', 'gender', 'birth_place', 'birth_date',
            'birth_date_words', 'occupation', 'nationality', 'address',
            'rt', 'rw', 'subdistrict', 'district', 'city',
            'province', 'postal_code', 'id_number'
        ]

        errors, warnings = [], []
        preview_rows = []

        # --- untuk deteksi duplikat chassis+engine (file & DB)
        seen_pairs = set()
        duplicate_in_db = []
        invalid_pairs = []  # untuk menampung tuple invalid

        # === Loop baris
        for i in range(len(df_filled)):
            row_raw = df_raw.iloc[i].to_dict()
            row = {
                k: (v.strftime('%Y-%m-%d') if isinstance(v, (datetime, date)) else v)
                for k, v in df_filled.iloc[i].to_dict().items()
            }

            # 1) Isi '-' utk kolom template yg kosong (warning)
            for field in all_required_fields:
                if s(row_raw.get(field, '')) == '':
                    row[field] = '-'
                    warnings.append(f"Baris {i+1}: Kolom '{field}' kosong, diisi '-'.")

            # 2) Pad RT/RW & BPKB_RT/RW ke 3 digit (warning)
            for f in ['rt', 'rw', 'bpkb_rt', 'bpkb_rw']:
                before = s(row.get(f))
                padded = pad_3(before)
                row[f] = padded
                if before in ('0', '00') or (before.isdigit() and len(before) in (1, 2)):
                    warnings.append(f"Baris {i+1}: Kolom '{f}' dipadatkan menjadi 3 digit ({before} → {padded}).")

            # 3) Tanggal “Tidak Muncul” (error) – parity dg consumer
            if s(row_raw.get('birth_date')) == '':
                errors.append(f"Baris {i+1}: Tanggal Lahir Customer tidak muncul.")
            if s(row_raw.get('bpkb_birth_date')) == '':
                errors.append(f"Baris {i+1}: Tanggal Lahir BPKB tidak muncul.")
            if s(row_raw.get('agreement_date')) == '':
                errors.append(f"Baris {i+1}: Tanggal Perjanjian tidak muncul.")
            if s(row_raw.get('spb_date')) == '':
                errors.append(f"Baris {i+1}: Tanggal SPB tidak muncul.")
            if s(row_raw.get('go_live_date')) == '':
                errors.append(f"Baris {i+1}: Tanggal Awal Cicilan tidak muncul.")
            if s(row_raw.get('installment_end_date')) == '':
                errors.append(f"Baris {i+1}: Tanggal Akhir Cicilan tidak muncul.")

            # 4) Tahun pembuatan vs perakitan (error)
            py, ay = s(row.get('production_year')), s(row.get('assembly_year'))
            if 'assembly_year' in row and (ay in ('', '-') or py == ay):
                errors.append(f"Baris {i+1}: Tahun Pembuatan & Perakitan sama / Tahun Perakitan tidak muncul.")

            # 5) Normalisasi jenis customer
            orig_ctype = s(row.get('customer_type'))
            norm_ctype = normalize_customer_type(orig_ctype)
            row['customer_type'] = norm_ctype
            if orig_ctype.lower() in ('personal', 'company', 'corporate', 'perusahaan', 'individu', 'individual'):
                warnings.append(f"Baris {i+1}: customer_type dinormalisasi ({orig_ctype} → {norm_ctype}). (Seharusnya I/C)")

            # 5b) Normalisasi agreement_type
            if 'agreement_type' in row:
                orig_atype = s(row.get('agreement_type'))
                norm_atype = normalize_agreement_type(orig_atype)
                if orig_atype != '' and orig_atype != norm_atype:
                    warnings.append(f"Baris {i+1}: agreement_type dinormalisasi ({orig_atype} → {norm_atype}).")
                row['agreement_type'] = norm_atype

            # 6) Phone 0 → 0000000
            pn_before = s(row.get('phone_number'))
            row['phone_number'] = normalize_phone(pn_before)
            if pn_before == '0':
                warnings.append(f"Baris {i+1}: Nomor telepon '0' dinormalisasi menjadi '0000000'.")

            # 7) Kelengkapan blok BPKB/Debitur
            ctype_lower = norm_ctype.lower()
            if ctype_lower == 'bpkb':
                missing_bpkb = [f for f in bpkb_required if s(row.get(f)) in ('', '-')]
                if missing_bpkb:
                    errors.append(f"Baris {i+1}: Kolom Data BPKB wajib lengkap: {', '.join(missing_bpkb)}")
            elif ctype_lower == 'debitur':
                missing_deb = [f for f in debtor_required if s(row.get(f)) in ('', '-')]
                if missing_deb:
                    errors.append(f"Baris {i+1}: Kolom Data Debitur wajib lengkap: {', '.join(missing_deb)}")

            # 8) VALIDASI KHUSUS MU: kombinasi chassis_number + engine_number
            chassis = s(row.get('chassis_number')).upper()
            engine  = s(row.get('engine_number')).upper()

            if chassis == '' or engine == '':
                errors.append(f"Baris {i+1}: Nomor Rangka / Nomor Mesin kosong.")
                invalid_pairs.append((chassis or '-', engine or '-'))
            else:
                pair = (chassis, engine)
                if pair in seen_pairs:
                    errors.append(f"Baris {i+1}: Duplikat kombinasi No Rangka & No Mesin di file: {chassis} / {engine}")
                    invalid_pairs.append(pair)
                elif ConsumerMU.objects.filter(chassis_number__iexact=chassis,
                                               engine_number__iexact=engine).exists():
                    errors.append(f"Baris {i+1}: Kombinasi No Rangka & No Mesin sudah ada di database: {chassis} / {engine}")
                    duplicate_in_db.append(pair)
                    invalid_pairs.append(pair)
                else:
                    seen_pairs.add(pair)
                    
            # 8b) VALIDASI: chassis & engine TIDAK BOLEH mengandung SD1..5, 1SD..5SD, EUC1..5, 1EUC..5EUC
            # Ambil dari DF yang sudah di-rename (row = model field), fallback ke sumber mentah (row_raw = header Excel)
            chassis_val = first_present(row, CHASSIS_FIELD_ALIASES)
            engine_val  = first_present(row, ENGINE_FIELD_ALIASES)

            if chassis_val in (None, "", "-"):
                chassis_val = first_present(row_raw, CHASSIS_FIELD_ALIASES)
            if engine_val in (None, "", "-"):
                engine_val  = first_present(row_raw, ENGINE_FIELD_ALIASES)

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

            # 9) VALIDASI CABANG (HO vs Non-HO)
            excel_branch_input = row_raw.get('branch_code') or row_raw.get('branch_name')  # dari sumber mentah
            if is_ho:
                if not s(excel_branch_input):
                    errors.append(f"Baris {i+1}: User HO: kolom cabang wajib diisi (branch_code atau branch_name).")
                else:
                    b_obj = find_branch(excel_branch_input)
                    if not b_obj:
                        errors.append(f"Baris {i+1}: User HO: Cabang '{s(excel_branch_input)}' tidak terdaftar di master Branch.")
                    else:
                        # tampilkan branch_code yang akan dipakai saat simpan
                        row['branch_code'] = b_obj.branch_code
            else:
                resolved_code = resolve_branch_code(excel_branch_input)
                if is_staff_cabang and resolved_code and resolved_code not in user_branch_codes:
                    errors.append(f"Baris {i+1}: Cabang '{s(excel_branch_input)}' tidak termasuk akses user.")
                elif resolved_code:
                    row['branch_code'] = resolved_code
                else:
                    if user_branch:
                        warnings.append(
                            f"Baris {i+1}: Cabang tidak dikenali/kosong. "
                            f"Akan fallback ke cabang user ({user_branch.branch_code}) saat simpan."
                        )
                        row['branch_code'] = user_branch.branch_code
                    else:
                        warnings.append(
                            f"Baris {i+1}: Cabang tidak dikenali/kosong dan user tidak punya branch. "
                            f'Nilai "branch_code" akan kosong saat simpan.'
                        )

            # simpan utk preview
            preview_rows.append(row)

        # --- Rekonstruksi untuk UI: pakai header Excel asli
        filtered_headers = original_headers
        rows_for_preview = []
        for row in preview_rows:
            row_excel = {}
            for header in filtered_headers:
                model_field = FIELD_MAPPING_MU.get(header)
                row_excel[header] = row.get(model_field, '') if model_field else ''
            rows_for_preview.append(row_excel)

        # === Compose headline error (duplikat MU + error cabang)
        if errors or duplicate_in_db:
            parts = []

            if duplicate_in_db:
                dup_list = [f"{c}/{e}" for (c, e) in sorted(set(duplicate_in_db))]
                parts.append(f"Duplikat di database (MU): {', '.join(dup_list)}")

            # ringkasan khusus cabang (HO)
            lower_errors = [e.lower() for e in errors]
            branch_required = [e for e in lower_errors if "kolom cabang wajib diisi" in e]
            branch_not_found = [e for e in lower_errors if "tidak terdaftar di master branch" in e]

            if branch_required:
                parts.append(f"{len(branch_required)} baris tanpa cabang (HO)")
            if branch_not_found:
                parts.append(f"{len(branch_not_found)} baris cabang tidak terdaftar (HO)")

            headline = " ; ".join(parts) if parts else (str(errors[0]) if errors else "")
            headline = (headline.strip() + ". Cek kembali Excel Anda.") if headline else "Cek kembali Excel Anda."

            return Response({
                'error': headline,
                'error_count': len(errors),
                'errors': errors,
                'warnings': warnings,
                'invalid_pairs': invalid_pairs,
                'duplikat_di_database': [list(p) for p in duplicate_in_db],
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
            'message': 'Preview berhasil dimuat (MU).'
        }, status=200)

    except Exception as e:
        import traceback
        logger.error(f"(MU) ❌ Failed to process Excel: {e}\n{traceback.format_exc()}")
        return Response({'error': 'Gagal memproses file Excel. Periksa format dan isi file Anda.'}, status=400)

# def get_next_consumer_id():
#     from django.db import connection
#     with connection.cursor() as cursor:
#         cursor.execute("""
#             SELECT COALESCE(
#                 (SELECT MIN(t1.id) + 1
#                  FROM consumers t1
#                  WHERE NOT EXISTS (SELECT 1 FROM consumers t2 WHERE t2.id = t1.id + 1)),
#                 1
#             ) AS next_id
#         """)
#         row = cursor.fetchone()
#     return row[0]

def _s(v):  # stringify aman
    return '' if v is None else str(v).strip()

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def save_excel_data_mu(request):
    if not user_has_permission(request.user, 'create-upload-mu'):
        return Response({'message': 'Forbidden'}, status=403)

    # --- simpan file (opsional) ---
    uploaded_file = request.FILES.get('file')
    saved_filename = None
    if uploaded_file:
        fs = FileSystemStorage(location=os.path.join(settings.MEDIA_ROOT, 'uploads'))
        saved_filename = fs.save(uploaded_file.name, uploaded_file)
        filename = uploaded_file.name
        logger.info(f"(MU) 📁 File disimpan ke: {os.path.join('media/uploads', saved_filename)}")
    else:
        filename = request.data.get('filename')

    # --- ambil payload rows (boleh header Excel / model field) ---
    try:
        raw_data = request.data.get('data')
        data_list = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
    except Exception as e:
        logger.error(f"(MU) ❌ Gagal parsing data JSON: {e}")
        return Response({'error': 'Format data tidak valid.'}, status=status.HTTP_400_BAD_REQUEST)
    if not data_list:
        return Response({'error': 'No data provided.'}, status=status.HTTP_400_BAD_REQUEST)

    # --- branch user fallback ---
    user = request.user

    role_names = list(get_user_role_names(user))
    is_privileged = (
        has_role(role_names, ROLE_KEYS["SUPERADMIN"])
        or has_role(role_names, ROLE_KEYS["HO"])
        or has_role(role_names, ROLE_KEYS["OPERATOR"])
        or getattr(user, "is_superuser", False)
    )
    is_ho = is_privileged
    is_staff_cabang = user_is_branch_restricted(user)

    if is_ho:
        user_branch = None
    else:
        user_branch_codes = get_user_branch_codes(user)
        user_branch = Branch.objects.filter(branch_code=user_branch_codes[0]).first() if len(user_branch_codes) == 1 else None
        if not user_branch_codes:
            return Response({'message': 'Branch wajib diisi untuk user non-HO.'}, status=403)

    # --- allowed fields (model) ---
    allowed_fields = [
        f.name for f in ConsumerMU._meta.fields
        if f.name not in ['id', 'created_at', 'updated_at', 'uploader', 'branch']
    ]
    
    FORBIDDEN_SUBSTRINGS = {
        # SDx and xSD
        "SD1","SD2","SD3","SD4","SD5", "SD", "1SD", "2SD", "3SD", "4SD", "5SD",
        # EUCx and xEUC
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

    # --- mapping header Excel → nama field model ---
    try:
        FIELD_MAPPING_MU  # harus ada di module yg sama
    except NameError:
        FIELD_MAPPING_MU = FIELD_MAPPING

    # --- helpers konsisten dgn preview ---
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
        Input bisa kode (numeric/string) atau nama cabang. Return Branch instance atau None.
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
        b = Branch.objects.filter(branch_name__icontains=val).first()  # toleransi
        return b

    CUSTOMER_TYPE_MAP = {
        'personal': 'I', 'individu': 'I', 'individual': 'I',
        'company': 'C', 'corporate': 'C', 'perusahaan': 'C',
        'i': 'I', 'c': 'C', 'i/c': 'I/C',
        'bpkb': 'BPKB', 'debitur': 'DEBITUR', 'debitor': 'DEBITUR'
    }
    def normalize_customer_type(v):
        key = s(v).lower()
        out = CUSTOMER_TYPE_MAP.get(key, s(v))
        return out.upper() if isinstance(out, str) else out
    
    def normalize_agreement_type(v):
        v = str(v).strip()  # pastikan string
        val = v.lower()

        if 'retail consumer financing' in val:
            return 'KONVE'
        elif 'syariah' in val:
            return 'SYARIAH'
        else:
            return v.upper()

    def resolve_branch_code(excel_or_model_value):
        val = s(excel_or_model_value)
        if not val:
            return None
        norm_code = pad_3(val) if val.isdigit() else val
        b = Branch.objects.filter(branch_code__iexact=norm_code).first()
        if b: return b.branch_code
        b = Branch.objects.filter(branch_name__iexact=val).first()
        if b: return b.branch_code
        logger.warning(f"(MU)[BRANCH] Tidak ditemukan input='{val}' (norm='{norm_code}')")
        return None

    def safe_normalize_date(v):
        if not v:
            return None
        if isinstance(v, date) and not isinstance(v, datetime):
            return v
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, str):
            vs = v.strip()
            for fmt in (
                "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y",
                "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
            ):
                try:
                    return datetime.strptime(vs, fmt).date()
                except Exception:
                    continue
        return None

    # REQUIRED blocks (opsional)
    BPKB_REQUIRED = [
        'bpkb_birth_place','bpkb_birth_date','bpkb_birth_date_words','bpkb_occupation',
        'bpkb_nationality','bpkb_address','bpkb_rt','bpkb_rw','bpkb_subdistrict',
        'bpkb_district','bpkb_city','bpkb_province','bpkb_postal_code','bpkb_ktp'
    ]
    DEBITOR_REQUIRED = [
        'debtor_name','gender','birth_place','birth_date','birth_date_words',
        'occupation','nationality','address','rt','rw','subdistrict','district',
        'city','province','postal_code','id_number'
    ]

    TEMPLATE_FIELDS = [mf for mf in FIELD_MAPPING_MU.values() if mf]

    # fungsi ambil nilai berdasarkan model_field atau header Excel
    def get_val(item_excel_or_model: dict, model_field: str):
        if model_field in item_excel_or_model:
            return item_excel_or_model.get(model_field)
        for excel_header, mapped in FIELD_MAPPING_MU.items():
            if mapped == model_field and excel_header in item_excel_or_model:
                return item_excel_or_model.get(excel_header)
        return None

    success_count, skipped, errors = 0, [], []
    
    # === PRE-SCAN FORBIDDEN (batch-reject) ===
    # Jika ADA satu saja baris yang mengandung pola terlarang pada nomor rangka/mesin,
    # seluruh batch ditolak (tidak ada yang disimpan).
    viol_rows = []
    for idx, raw_item in enumerate(data_list, start=1):
        # rakit 'item' minimal agar konsisten dengan mapping saat simpan
        item = {}
        for excel_col, model_field in FIELD_MAPPING.items():
            if not model_field:
                continue
            val = get_val(raw_item, model_field)
            if val is None:
                val = raw_item.get(excel_col)
            item[model_field] = val

        # ambil nilai chassis/engine dari item lalu fallback ke raw_item
        chassis_val = _first_present(item, CHASSIS_FIELD_ALIASES) or _first_present(raw_item, CHASSIS_FIELD_ALIASES)
        engine_val  = _first_present(item, ENGINE_FIELD_ALIASES)  or _first_present(raw_item, ENGINE_FIELD_ALIASES)

        bad_fields = []
        if _contains_forbidden(chassis_val):
            bad_fields.append("chassis_number")
        if _contains_forbidden(engine_val):
            bad_fields.append("engine_number")

        if bad_fields:
            # ambil agreement_number aman (belum tentu terisi di item saat ini)
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

    # Jika ada pelanggaran → langsung pulang tanpa menyimpan apapun
    if viol_rows:
        logger.warning(f"❌ Batch dibatalkan: {len(viol_rows)} baris mengandung pola terlarang. Tidak ada data yang disimpan.")
        return Response({
            'success_count': 0,
            'skipped_count': 0,
            'error_count': len(errors),
            'filename': saved_filename or filename,
            'skipped': [],
            'errors': errors
        }, status=status.HTTP_207_MULTI_STATUS)
    
    seen_pairs = set()

    for idx, raw_item in enumerate(data_list):
        try:
            # --- susun item model-field dari payload yg mungkin pakai header Excel ---
            item = {}
            for excel_col, model_field in FIELD_MAPPING_MU.items():
                if not model_field:
                    continue
                val = get_val(raw_item, model_field)
                if val is None:
                    val = raw_item.get(excel_col)  # fallback
                item[model_field] = val

            row_errors = []

            # 1) isi '-' utk kosong sesuai template
            for fld in TEMPLATE_FIELDS:
                if s(item.get(fld, '')) == '':
                    item[fld] = '-'

            # 2) normalisasi pad & telp (tidak memblokir)
            for f in ['rt','rw','bpkb_rt','bpkb_rw']:
                item[f] = pad_3(item.get(f))
            item['phone_number'] = normalize_phone(item.get('phone_number'))

            # 3) normalisasi customer_type + agreement_type
            item['customer_type']   = normalize_customer_type(item.get('customer_type'))
            item['agreement_type']  = normalize_agreement_type(item.get('agreement_type'))

            # 4) (opsional) VALIDASI BLOK BPKB/DEBITUR
            ctype = s(item.get('customer_type')).lower()
            if ctype == 'bpkb':
                missing = [f for f in BPKB_REQUIRED if s(item.get(f)) in ('', '-')]
                if missing:
                    row_errors.append("Kolom Data BPKB wajib lengkap: " + ", ".join(missing))
            elif ctype == 'debitur':
                missing = [f for f in DEBITOR_REQUIRED if s(item.get(f)) in ('', '-')]
                if missing:
                    row_errors.append("Kolom Data Debitur wajib lengkap: " + ", ".join(missing))

            # 5) VALIDASI KHUSUS MU: kombinasi chassis_number + engine_number
            chassis = s(item.get('chassis_number')).upper()
            engine  = s(item.get('engine_number')).upper()
            if chassis in ('', '-') or engine in ('', '-'):
                row_errors.append("Nomor Rangka / Nomor Mesin kosong.")
            else:
                pair = (chassis, engine)
                if pair in seen_pairs:
                    row_errors.append(f"Duplikat dalam file: {chassis} / {engine}")
                elif ConsumerMU.objects.filter(chassis_number__iexact=chassis, engine_number__iexact=engine).exists():
                    row_errors.append(f"Kombinasi sudah ada di database: {chassis} / {engine}")
                else:
                    seen_pairs.add(pair)

            # 6) (opsional) agreement_number harus angka (tanpa cek unik DB)
            agreement_number = s(item.get('agreement_number'))
            if agreement_number not in ('', '-') and not agreement_number.isdigit():
                row_errors.append("agreement_number harus angka")

            # 7) === BRANCH HANDLING (HO vs Non-HO) ===
            excel_branch_input = get_val(raw_item, 'branch_code') or get_val(raw_item, 'branch_name')
            if is_ho:
                # HO WAJIB isi dan harus ada di master Branch
                if not s(excel_branch_input):
                    row_errors.append("User HO: kolom cabang wajib diisi (branch_code atau branch_name).")
                else:
                    b_obj = find_branch(excel_branch_input)
                    if not b_obj:
                        row_errors.append(f"User HO: Cabang '{s(excel_branch_input)}' tidak terdaftar di master Branch.")
                    # jika valid, nanti set ke update_data pada saat build (di bawah)
            # Non-HO → handled saat build update_data (fallback ke user_branch bila invalid)

            if row_errors:
                errors.append({'row': idx, 'agreement_number': agreement_number or '-', 'error': row_errors})
                continue

            # 8) siapkan update_data hanya untuk allowed_fields
            update_data = {}
            for model_field in TEMPLATE_FIELDS:
                if model_field in allowed_fields:
                    val = item.get(model_field)
                    update_data[model_field] = None if val == '' else val
            
            chassis_val = _first_present(item, CHASSIS_FIELD_ALIASES)
            engine_val  = _first_present(item, ENGINE_FIELD_ALIASES)

            # Jika belum ketemu di 'item', coba cari langsung dari raw_item berdasarkan header Excel
            if chassis_val in (None, "", "-"):
                chassis_val = _first_present(raw_item, CHASSIS_FIELD_ALIASES)
                if engine_val in (None, "", "-"):
                    engine_val  = _first_present(raw_item, ENGINE_FIELD_ALIASES)

                bad_fields = []
                if _contains_forbidden(chassis_val):
                    bad_fields.append("chassis_number")
                if _contains_forbidden(engine_val):
                    bad_fields.append("engine_number")

                if bad_fields:
                    errors.append({
                        'row': idx,
                        'agreement_number': agreement_number or '-',
                        'error': f"Invalid value on {', '.join(bad_fields)}: "
                                f"mengandung pola terlarang (SD1-SD5, 1SD-5SD, EUC1-EUC5, 1EUC-5EUC)."
                    })
                    continue    

            # === 9) Set branch_code sesuai aturan role ===
            if is_ho:
                # sudah dipastikan valid; ambil branch_code final dari master
                b_obj = find_branch(excel_branch_input)  # aman, karena barusan divalidasi
                update_data['branch_code'] = b_obj.branch_code if b_obj else None
            else:
                # Non-HO: jika Excel valid pakai itu; jika tidak → fallback user_branch (jika ada)
                resolved_code = resolve_branch_code(excel_branch_input)
                if is_staff_cabang and resolved_code and resolved_code not in user_branch_codes:
                    resolved_code = None
                resolved_code = resolved_code or (user_branch.branch_code if user_branch else None)
                update_data['branch_code'] = resolved_code

            # write-only agar rt_rw terbentuk di serializer
            update_data['customer_address_rt'] = item.get('rt') or ''
            update_data['customer_address_rw'] = item.get('rw') or ''

            # uploader & timestamp
            update_data.update({
                'uploader': request.user.id,
                'updated_at': timezone.now(),
                'upload_date': timezone.now().date()
            })
            
            if update_data.get('production_year'):
                try:
                    # pastikan yang diambil hanya tahunnya
                    update_data['assembly_year'] = str(update_data['production_year'])[:4]
                except Exception:
                    update_data['assembly_year'] = None

            # 10) normalisasi tanggal aman (jika ada)
            for date_field in ['birth_date','agreement_date','go_live_date','spb_date','installment_end_date','bpkb_birth_date']:
                if date_field in update_data:
                    update_data[date_field] = safe_normalize_date(update_data.get(date_field))

            # 11) save via serializer MU
            serializer = ConsumerSerializerMU(data=update_data, context={'request': request, 'filename': filename})
            if serializer.is_valid():
                serializer.save()
                success_count += 1
                logger.info(f"(MU)[Row {idx}] ✅ Tersimpan: {agreement_number or '-'} (chassis/engine={chassis}/{engine}, branch={update_data.get('branch_code')})")
            else:
                errors.append({'row': idx, 'agreement_number': agreement_number or '-', 'error': serializer.errors})

        except Exception as e:
            import traceback
            logger.error(f"(MU)[Row {idx}] ❌ Exception: {str(e)}\n{traceback.format_exc()}")
            errors.append({'row': idx, 'agreement_number': s(raw_item.get('agreement_number')) or '-', 'error': str(e)})

    # --- log dokumen jika ada sukses ---
    if (saved_filename or filename) and success_count > 0:
        try:
            file_url = f"/media/uploads/{saved_filename or filename}"

            # coba ambil nama/perusahaan uploader (aman walau None)
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
                status='Terkirim',            # atau 'SUCCESS'
                old_path='',                   # isi kalau ada path awal
                new_location=file_url,         # lokasi file tersimpan
                uploader_company=uploader_company,
                modified_by=request.user,      # FK ke Users
                created_at=timezone.now(),
                updated_at=timezone.now(),
                category="3"                   # ⬅️ otomatis isi "3"
            )

            ActivityLogMU.objects.create(
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
            import traceback
            logger.error(f"❌ Gagal menyimpan log file_processings: {doc_error}\n{traceback.format_exc()}")

    logger.info(f"(MU) 📊 Upload selesai: {success_count} sukses, {len(skipped)} dilewati, {len(errors)} error")

    return Response({
        'success_count': success_count,
        'skipped_count': len(skipped),
        'error_count': len(errors),
        'filename': saved_filename or filename,
        'skipped': skipped,
        'errors': errors
    }, status=status.HTTP_200_OK if not errors else status.HTTP_207_MULTI_STATUS)

# FIELD_MAPPING = {
#     'branch': None,
#     'fiduciary_status': None,
#     'fiducia_provider': 'fiducia_provider',
#     'customer_type': 'customer_type',
#     'agreement_type': 'agreement_type',
#     'go_live_date': None,
#     'customer_number': None,
#     'agreement_number': 'agreement_number',
#     'agreement_date': 'agreement_date',
#     'pp_date_(spelled)': None,
#     'spb_date': 'spb_date',
#     'spb_date_(spelled)': None,
#     'bpkb_name': 'bpkb_name',
#     'bpkb_birth_place': 'bpkb_birth_place',
#     'bpkb_birth_date': 'bpkb_birth_date',
#     'bpkb_owner_birthdate_(spelled)': None,
#     'bpkb_occupation': 'bpkb_occupation',
#     'bpkb_nationality': 'bpkb_nationality',
#     'bpkb_address': 'bpkb_address',
#     'bpkb_rt': 'bpkb_rt',
#     'bpkb_rw': 'bpkb_rw',
#     'bpkb_subdistrict': 'bpkb_subdistrict',
#     'bpkb_district': 'bpkb_district',
#     'bpkb_city': 'bpkb_city',
#     'bpkb_province': 'bpkb_province',
#     'bpkb_postal_code': 'bpkb_postal_code',
#     'bpkb_ktp': 'bpkb_ktp',
#     'debtor_name': 'debtor_name',
#     'gender': 'gender',
#     'birth_place': 'birth_place',
#     'birth_date': 'birth_date',
#     'customer_birthdate_(spelled)': None,
#     'occupation': 'occupation',
#     'nationality': 'nationality',
#     'address': 'address',
#     'rt_rw': 'rt_rw',
#     'subdistrict': 'subdistrict',
#     'district': 'district',
#     'city': 'city',
#     'province': 'province',
#     'postal_code': 'postal_code',
#     'id_number': 'id_number',
#     'unit_quantity': None,
#     'brand': 'brand',
#     'model': None,
#     'type': 'type',
#     'production_year': 'production_year',
#     'color': 'color',
#     'chassis_number': 'chassis_number',
#     'engine_number': 'engine_number',
#     'otr_value': 'otr_value',
#     'otr_(spelled)': None,
#     'principal_loan': 'principal_loan',
#     'af_value_(spelled)': None,
#     'guarantee_value': 'guarantee_value',
#     'ar_value_(spelled)': None,
#     'vehicle_condition': 'vehicle_condition',
#     'company_director_name': 'company_director_name',
#     'installment_end_date': 'installment_end_date',
#     'tenor': 'tenor',
#     'phone_number': 'phone_number',
#     'marital_status': 'marital_status',
#     'id_type': 'id_type',
#     'company_type': 'company_type',
#     'company_npwp': 'company_npwp',
#     'deed_number': 'deed_number',
#     'deed_date': 'deed_date',
#     'decree_date': 'decree_date',
#     'director_gender': 'director_gender',
#     'director_status': 'director_status',
#     'director_birth_place': 'director_birth_place',
#     'director_birth_date': 'director_birth_date',
#     'director_nationality': 'director_nationality',
#     'director_id_type': 'director_id_type',
#     'director_id_number': 'director_id_number',
#     'director_address': 'director_address',
#     'director_rt_rw': 'director_rt_rw',
#     'director_subdistrict': 'director_subdistrict',
#     'director_district': 'director_district',
#     'director_city': 'director_city',
#     'director_province': 'director_province',
#     'director_occupation': 'director_occupation',
#     'director_position': 'director_position',
#     'director_notary_name': 'director_notary_name',
#     'notary_city': 'notary_city',
#     'agreement_name': 'agreement_name',
#     'installment_start_date': 'installment_start_date',
#     'assembly_year': 'assembly_year',
#     'bpkb_number': 'bpkb_number',
#     'pnbp_fee': 'pnbp_fee',
#     'service_fee': 'service_fee',
#     'npwp_provider': 'npwp_provider',
#     'bpkb_id_number': 'bpkb_id_number',
#     'bpkb_npwp_number': 'bpkb_npwp_number',
#     'receipt_name': 'receipt_name',
#     'spouse_name': 'spouse_name',
#     'uploader_company': 'uploader_company',
#     'upload_group': 'upload_group',
#     'st_process': 'st_process',
#     'is_st_used': 'is_st_used',
#     'house_number': 'house_number',
#     'rw': 'rw',
#     'status': 'status',
#     'upload_date': 'upload_date',
#     'document_status': 'document_status',
#     'invoice_st': 'invoice_st',
#     'over_credit': 'over_credit',
#     'sync': 'sync'
# }

FIELD_MAPPING = {
    'No' : 'No',
    'Cabang' : 'branch_name',

    'Fiduciary_Status': 'fiduciary_status',
    'Pemberi_Fidusia': 'fiducia_provider',
    'Jenis_Nasabah': 'customer_type',
    'Jenis_perjanjian': 'agreement_type',
    'Go_Live_Date': 'go_live_date',

    'No_Customer': 'customer_number',
    'No_Kontrak': 'agreement_number',
    'Tanggal_Kontrak': 'agreement_date',
    'Tanggal_PP_(Terbilang)': 'agreement_date_words',
    'Tanggal_SPB': 'spb_date',
    'Tanggal_SPB_(terbilang)': 'spb_date_words',

    # BPKB
    'Nama_BPKB': 'name_on_bpkb',
    'Tempat_lahir_pemilik_BPKB': 'bpkb_birth_place',
    'Tanggal_lahir_pemilik_BPKB': 'bpkb_birth_date',
    'Tanggal_lahir_pemilik_BPKB_(terbilang)': 'bpkb_birth_date_words',
    'Pekerjaan_dari_pemilik_BPKB': 'bpkb_occupation',
    'Kewarganegaraan_pemilik_BPKB': 'bpkb_nationality',
    'Alamat_BPKB': 'bpkb_address',
    'RT_Alamat_BPKB': 'bpkb_rt',
    'RW_Alamat_BPKB': 'bpkb_rw',
    'Kelurahan_Alamat_BPKB': 'bpkb_subdistrict',
    'Kecamatan_Alamat_BPKB': 'bpkb_district',
    'Kabupaten_Alamat_BPKB': 'bpkb_city',
    'Provinsi_Alamat_BPKB': 'bpkb_province',
    'Kode_Pos_Alamat_BPKB': 'bpkb_postal_code',
    'No KTP_(Pemilik BPKB)': 'bpkb_ktp',  # <- disesuaikan ke field yang ada

    # Customer (Debitur)
    'Nama_Customer': 'debtor_name',
    'Jenis_Kelamin_Customer': 'gender',
    'Tempat_Lahir_Customer': 'birth_place',
    'Tanggal_Lahir_Customer': 'birth_date',
    'Tanggal_Lahir_Customer_(Terbilang)': 'birth_date_words',
    'Pekerjaan_Customer': 'occupation',
    'Kewarganegaraan_Customer': 'nationality',
    'Alamat_Customer': 'address',
    'RT_Alamat_Customer': 'rt',        # <- butuh field rt di model (lihat patch di bawah)
    'RW_Alamat_Customer': 'rw',
    'Kelurahan_Alamat_Customer': 'subdistrict',
    'Kecamatan_Alamat_Customer': 'district',
    'Kabupaten_Alamat_Customer': 'city',
    'Provinsi_Alamat_Customer': 'province',
    'Kode_Pos_Alamat_Customer': 'postal_code',
    'No_KTP_Customer': 'id_number',

    # Kendaraan
    'Jumlah_Unit': 'unit_quantity',
    'Merk': 'brand',
    'Model': 'model',
    'Type': 'type',                    # <- disesuaikan ke field model
    'Tahun_Mobil': 'production_year',
    'Warna': 'color',
    'No_Rangka': 'chassis_number',
    'No_Mesin': 'engine_number',
    'OTR_/_Nilai_Objek': 'otr_value',
    'OTR_(terbilang)': 'otr_value_words',

    # Pembiayaan
    'Nilai_AF': 'principal_loan',
    'Nilai_AF_(terbilang)': 'principal_loan_words',
    'Nilai_AR': 'guarantee_value',
    'Nilai_AR_(terbilang)': 'guarantee_value_words',

    # Lainnya
    'Status_Kendaraan': 'vehicle_condition',
    'Nama_Direktur_Signer': 'company_director_name',
    'Maturity_Date_(Tanggal_Lunas)': 'installment_end_date',
    'Tenor': 'tenor',
    'Mobile_Phone': 'phone_number',
}

def parse_value(field_name, value):
    if value in ['', None]:
        return None
    try:
        field = ConsumerMU._meta.get_field(field_name)
        if isinstance(field, models.DecimalField):
            return float(value)
        if isinstance(field, models.IntegerField):
            return int(value)
    except FieldDoesNotExist:
        pass
    return value


def normalize_date(value):
    if not value or str(value).strip() in ['-', '', 'NaT', 'None']:
        return None
    try:
        return parser.parse(str(value)).date().isoformat()  # Format: YYYY-MM-DD
    except Exception:
        return None
# def map_field_name_reverse():
#     return {k: v for k, v in FIELD_MAPPING.items() if v is not None}

# def map_field_name(field):
#     return FIELD_MAPPING.get(field.strip().lower())

# def parse_value(field, value):
#     if value == '':
#         return None
#     if 'date' in field:
#         return parse_date(value)
#     if 'decimal' in str(ConsumerMU._meta.get_field(field)):
#         return float(value) if value else None
#     if 'integer' in str(ConsumerMU._meta.get_field(field)):
#         return int(value) if value else None
#     return value

# def parse_date(date_value):
#     if pd.isna(date_value) or date_value == '':
#         return None
#     if isinstance(date_value, datetime):
#         return date_value.date()
#     try:
#         return pd.to_datetime(date_value, format="%Y-%m-%dT%H:%M:%S").date()
#     except:
#         return None

# def get_branch_by_code(branch_code):
#     if not branch_code:
#         return None
#     try:
#         return Branch.objects.get(branch_code=branch_code)
#     except Branch.DoesNotExist:
#         return None

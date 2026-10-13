from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from .models import TemporaryUpdatePF
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from .serializers import TemporaryUpdateSerializerPF
from rest_framework.renderers import TemplateHTMLRenderer
from AdminMenu.auth_helpers import class_permission_required, get_user_role_names
from common.const.role_map import has_role, ROLE_KEYS
from AdminUser.branch_access import get_primary_user_branch_code, get_user_branch_codes, user_is_branch_restricted
from django.views import View
from django.shortcuts import render
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
import pandas as pd
from django.http import HttpResponse
from django.views.decorators.http import require_GET
from django.utils import timezone
from change_fiduciary.models import change_of_fiduciary
from django.db.models import Q
from django.db import transaction
from common.validators import validate_excel_file_or_error


def _is_superadmin(user):
    role_names = list(get_user_role_names(user))
    return (
        has_role(role_names, ROLE_KEYS["SUPERADMIN"])
        or has_role(role_names, ROLE_KEYS["HO"])
        or has_role(role_names, ROLE_KEYS["OPERATOR"])
        or has_role(role_names, ROLE_KEYS["WARKAH"])
    )


def _user_branch_code(user):
    return get_primary_user_branch_code(user)

def _user_branch_codes(user):
    return get_user_branch_codes(user)

def _user_is_staff_cabang(user):
    """Check if user has staff_cabang role"""
    return user_is_branch_restricted(user)


def _agreement_numbers_for_branch(model, branch_code):
    return _agreement_numbers_for_branches(model, [branch_code] if branch_code else [])

def _agreement_numbers_for_branches(model, branch_codes):
    if not branch_codes:
        return set()

    base_qs = model.objects.filter(
        Q(branch__branch_code__in=branch_codes) | Q(branch_id__in=branch_codes)
    )
    agreements = set(base_qs.values_list("agreement_number", flat=True))

    try:
        model._meta.get_field("new_agreement_number")
        agreements.update(base_qs.values_list("new_agreement_number", flat=True))
    except Exception:
        pass

    return {value for value in agreements if value}


def _scope_updates_queryset(qs, user, consumer_model):
    if not _user_is_staff_cabang(user):
        return qs

    branch_codes = _user_branch_codes(user)
    if not branch_codes:
        return qs.none()

    allowed = _agreement_numbers_for_branches(consumer_model, branch_codes)
    if not allowed:
        return qs.none()

    return qs.filter(agreement_number__in=allowed)


def _allowed_agreements_or_403(user, consumer_model):
    if not _user_is_staff_cabang(user):
        return None, None

    branch_codes = _user_branch_codes(user)
    if not branch_codes:
        return None, Response({"detail": "Branch wajib diisi untuk user non-superadmin."}, status=403)

    return _agreement_numbers_for_branches(consumer_model, branch_codes), None

@method_decorator(login_required, name='dispatch')
class TemporaryUpdatePageViewPF(View):
    login_url = ''
    
    @class_permission_required("read-data")
    def get(self, request):
        context = {
            "range_options": [(i, i + 49) for i in range(1, 1501, 50)]
        }
        return render(request, 'operator/page-operator-update-data-pf.html', context)

class TemporaryUpdateListCreateAPIViewPF(APIView):
    permission_classes = [IsAuthenticated]

    @class_permission_required('read-data')
    def get(self, request):
        tab = request.GET.get('tab', 'all')
        try:
            draw = int(request.GET.get('draw', 1))
        except (TypeError, ValueError):
            draw = 1
        try:
            start = int(request.GET.get('start', 0))
        except (TypeError, ValueError):
            start = 0
        try:
            length = int(request.GET.get('length', 10))
        except (TypeError, ValueError):
            length = 10

        search_value = (request.GET.get('search[value]', '') or '').strip()
        order_col = request.GET.get('order[0][column]', None)
        order_dir = request.GET.get('order[0][dir]', 'desc')
        status_filter = (request.GET.get('status', '') or '').strip()

        if tab == 'history':
            qs_base = TemporaryUpdatePF.objects.select_related('uploaded_by', 'approved_by').filter(is_approved=True)
            key = 'history'
        else:
            qs_base = TemporaryUpdatePF.objects.select_related('uploaded_by', 'approved_by').filter(is_approved=False)
            key = 'all'

        qs_base = _scope_updates_queryset(qs_base, request.user, change_of_fiduciary)

        if status_filter:
            qs_base = qs_base.filter(update_status=status_filter)

        if search_value:
            qs_base = qs_base.filter(
                Q(category__icontains=search_value) |
                Q(agreement_number__icontains=search_value) |
                Q(replacement_value__icontains=search_value) |
                Q(uploaded_by__username__icontains=search_value) |
                Q(approved_by__username__icontains=search_value)
            )

        order_map = {
            '0': 'created_at',
            '1': 'category',
            '2': 'agreement_number',
            '3': 'replacement_value',
            '4': 'is_approved',
            '5': 'uploaded_by__username',
            '6': 'approved_by__username',
        }

        records_total = _scope_updates_queryset(
            TemporaryUpdatePF.objects.filter(is_approved=(tab == 'history')),
            request.user,
            change_of_fiduciary,
        ).count()
        records_filtered = qs_base.count()

        user_orders = []
        if order_col and order_col in order_map:
            field = order_map[order_col]
            if field != 'created_at':
                if order_dir == 'desc':
                    field = '-' + field if not field.startswith('-') else field
                else:
                    field = field.lstrip('-')
                user_orders.append(field)

        user_orders = [o for o in user_orders if o.lstrip('-') != 'created_at']
        orders = ['-created_at'] + user_orders

        if '-id' not in orders and 'id' not in [o.lstrip('-') for o in orders]:
            orders.append('-id')

        qs_page = qs_base.order_by(*orders)[start:start + length]

        serializer = TemporaryUpdateSerializerPF(qs_page, many=True)

        resp = {
            key: serializer.data,
            'draw': draw,
            'recordsTotal': records_total,
            'recordsFiltered': records_filtered,
        }
        if key == 'all':
            resp['history'] = []
        else:
            resp['all'] = []

        return Response(resp)

    @class_permission_required('create-data')
    def post(self, request):
        allowed_agreements, error_response = _allowed_agreements_or_403(request.user, change_of_fiduciary)
        if error_response:
            return error_response

        agreement_number = request.data.get("agreement_number")
        if allowed_agreements is not None and agreement_number not in allowed_agreements:
            return Response({"detail": "Forbidden branch scope."}, status=403)

        serializer = TemporaryUpdateSerializerPF(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class TemporaryUpdateDetailAPIViewPF(APIView):
    permission_classes = [IsAuthenticated]

    def get_queryset(self, request):
        return _scope_updates_queryset(TemporaryUpdatePF.objects.all(), request.user, change_of_fiduciary)

    def get_object(self, request, pk):
        return get_object_or_404(self.get_queryset(request), pk=pk)

    def find_category_info(self, category_key):
        """
        Cari info parent, key, dan value berdasarkan category_key.

        :param category_key: string yang dicari, misal "jabatan_pj_badan"
        :return: (parent, key, value) jika ketemu, jika tidak None
        """

        categories_dict = {
            "consumers": {
                "jenis_nasabah": "customer_type",
                "pemberi_fidusia": "fiducia_provider",
                "nama_pada_bpkb": "name_on_bpkb",
                "nama_debitur": "debtor_name",
                "jenis_kelamin": "gender",
                "status_pernikahan": "marital_status",
                "tmp_lahir": "birth_place",
                "tgl_lahir": "birth_date",
                "pekerjaan": "occupation",
                "kewarganegaraan": "nationality",
                "jenis_identitas": "id_type",
                "no_identitas": "id_number",
                "alamat": "address",
                "rt_rw": "rt_rw",
                "kelurahan": "subdistrict",
                "kecamatan": "district",
                "kota": "city",
                "provinsi": "province",
                "kode_pos": "postal_code",
                "rt_rw_pj_badan": "director_rt_rw",
                "jenis_perjanjian": "agreement_type",
                "nama_perjanjian": "agreement_name",
                "tgl_perjanjian": "agreement_date",
                "nilai_hutang_pokok": "principal_loan",
                "nilai_penjaminan": "guarantee_value",
                "tgl_awal_cicilan": "go_live_date",
                "tenor": "tenor",
                "kondisi_kendaraan": "vehicle_condition",
                "merk": "brand",
                "model": "model",
                "tipe": "type",
                "warna": "color",
                "tahun_perakitan": "assembly_year",
                "tahun_pembuatan": "production_year",
                "no_rangka": "chassis_number",
                "no_mesin": "engine_number",
                "no_bpkb": "bpkb_number",
                "nilai_objek_otr": "otr_value",
                "tgl_akhir_cicilan": "installment_end_date",
                "kewarganegaraan_bpkb": "bpkb_nationality",
                "tempat_lahir_bpkb": "bpkb_birth_place",
                "tgl_lahir_bpkb": "bpkb_birth_date",
                "pekerjaan_bpkb": "bpkb_occupation",
                "provinsi_bpkb": "bpkb_province",
                "tgl_spb": "spb_date",
                "ktp_bpkb": "bpkb_ktp",
                "kabupaten_bpkb": "bpkb_city",
                "kecamatan_bpkb": "bpkb_district",
                "kelurahan_bpkb": "bpkb_subdistrict",
                "alamat_bpkb": "bpkb_address",
                "kode_pos_bpkb": "bpkb_postal_code",
                "rt_bpkb": "bpkb_rt",
                "rw_bpkb": "bpkb_rw",
            },
            "business_entity": {
                "jenis_badan": "entity_type",
                "npwp_badan": "npwp",
                "no_akta_badan": "deed_number",
                "tgl_akta_badan": "deed_date",
            },
            "business_entity_director": {
                "nama_pj_badan": "company_director_name",
                "jk_pj_badan": "director_gender",
                "status_pj_badan": "director_status",
                "tempat_lahir_pj_badan": "director_birth_place",
                "tgl_lahir_pj_badan": "director_birth_date",
                "alamat_pj_badan": "director_address",
                "kota_debitur": "city",
                "jabatan_pj_badan": "director_position",
                "kewarganegaraan_pj_badan": "director_nationality",
                "pekerjaan_pj_badan": "director_occupation",
                "rt_rw_pj_badan": "director_rt_rw",
                "kelurahan_pj_badan": "director_subdistrict",
                "kecamatan_pj_badan": "director_district",
                "provinsi_pj_badan": "director_province",
                "noid_pj_badan": "director_id_number",
                "notaris_badan_city": "notary_city",
                "notaris_pj_badan": "director_notary_name",
                "kota_pj_badan": "director_city",
            },
            "LegalDocument": {
                "biaya_pnbp": "pnbp_fee",
                "biaya_jasa": "code_value",
            }
        }

        for parent, mapping in categories_dict.items():
            for key, value in mapping.items():
                if key == category_key:
                    return parent, key, value
        return None

    @class_permission_required('read-data')
    def get(self, request, pk):
        instance = self.get_object(request, pk)
        serializer = TemporaryUpdateSerializerPF(instance, context={'request': request})
        return Response(serializer.data)

    @class_permission_required('update-data')
    def put(self, request, pk):
        instance = self.get_object(request, pk)
        incoming_category = request.data.get('category', None)
        category = incoming_category if incoming_category not in (None, '') else getattr(instance, 'category', None)

        result = self.find_category_info(category)
        if not result:
            return Response({"error": f"Kategori {category} tidak valid."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = TemporaryUpdateSerializerPF(instance,data=request.data,partial=True,context={'request': request})

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            serializer.save()

            category = serializer.validated_data.get('category') or getattr(instance, 'category')
            replacement_value = serializer.validated_data.get('replacement_value') or getattr(instance, 'replacement_value')
            agreement_number = serializer.validated_data.get('agreement_number') or getattr(instance, 'agreement_number')

            parent, key, field = result

            qs_consumer = change_of_fiduciary.objects.filter(agreement_number=agreement_number)
            if _user_is_staff_cabang(request.user):
                branch_codes = _user_branch_codes(request.user)
                if not branch_codes:
                    return Response({"detail": "Branch wajib diisi untuk user non-superadmin."}, status=403)
                qs_consumer = qs_consumer.filter(
                    Q(branch__branch_code__in=branch_codes) | Q(branch_id__in=branch_codes)
                )
            try:
                consumer_obj = qs_consumer.get()
                if hasattr(consumer_obj, field):
                    setattr(consumer_obj, field, replacement_value)
                    consumer_obj.save(update_fields=[field])
            except change_of_fiduciary.DoesNotExist:
                return Response(
                    {"error": f"Consumer dengan nomor perjanjian {agreement_number} tidak ditemukan."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        return Response(serializer.data)

    @class_permission_required('delete-data')
    def delete(self, request, pk):
        instance = self.get_object(request, pk)
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    
class TemporaryUpdateExcelUploadAPIViewPF(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @class_permission_required('create-data')
    def post(self, request, *args, **kwargs):
        excel_file = request.FILES.get('excel_file')

        if not excel_file:
            return Response({"error": "No file uploaded."}, status=status.HTTP_400_BAD_REQUEST)

        # Validate Excel file
        validation_error = validate_excel_file_or_error(excel_file)
        if validation_error:
            return validation_error

        allowed_agreements, error_response = _allowed_agreements_or_403(request.user, change_of_fiduciary)
        if error_response:
            return error_response

        try:
            df = pd.read_excel(excel_file)
        except Exception as e:
            return Response({"error": f"Gagal membaca file Excel: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

        # Map nama kolom ke header standar
        rename_map = {
            'kategori': 'category',
            'nomor perjanjian': 'agreement_number',
            'nilai pengganti': 'replacement_value'
        }
        df.rename(columns=rename_map, inplace=True)

        required_columns = {'category', 'agreement_number', 'replacement_value'}
        if not required_columns.issubset(df.columns):
            return Response({"error": f"Excel harus mengandung kolom: {list(rename_map.keys())}"}, status=status.HTTP_400_BAD_REQUEST)

        df = df.dropna(subset=['category', 'agreement_number', 'replacement_value'])

        # Ambil kombinasi unik untuk query awal
        key_tuples = set(zip(df['agreement_number'], df['category']))
        agreement_numbers = [k[0] for k in key_tuples]
        categories = [k[1] for k in key_tuples]

        if allowed_agreements is not None:
            agreement_numbers = [a for a in agreement_numbers if a in allowed_agreements]

        # Query semua yang relevan sekaligus
        approved_map = {
            (obj.agreement_number, obj.category): obj
            for obj in TemporaryUpdatePF.objects.filter(
                agreement_number__in=agreement_numbers,
                category__in=categories,
                is_approved=True
            )
        }

        existing_map = {
            (obj.agreement_number, obj.category): obj
            for obj in TemporaryUpdatePF.objects.filter(
                agreement_number__in=agreement_numbers,
                category__in=categories,
                is_approved=False
            )
        }

        created_objects = []
        updated_objects = []
        error_rows = []

        for index, row in df.iterrows():
            agreement_number = row['agreement_number']
            category = row['category']
            replacement_value = row['replacement_value']
            key = (agreement_number, category)

            if allowed_agreements is not None and agreement_number not in allowed_agreements:
                error_rows.append(f"Baris {index+2}: Nomor perjanjian di luar cabang.")
                continue

            if key in approved_map:
                error_rows.append(f"Baris {index+2}: Sudah di-approve, tidak bisa diubah.")
                continue

            if key in existing_map:
                obj = existing_map[key]
                obj.replacement_value = replacement_value
                obj.uploaded_by = request.user
                obj.update_status = 'Pending'
                updated_objects.append(obj)
            else:
                created_objects.append(TemporaryUpdatePF(
                    agreement_number=agreement_number,
                    category=category,
                    replacement_value=replacement_value,
                    uploaded_by=request.user,
                    is_approved=False,
                    update_status='Pending'
                ))

        # Bulk operation untuk efisiensi DB
        if updated_objects:
            TemporaryUpdatePF.objects.bulk_update(updated_objects, ['replacement_value', 'uploaded_by', 'update_status'])

        if created_objects:
            TemporaryUpdatePF.objects.bulk_create(created_objects)

        return Response({
            "message": f"{len(created_objects)} baris baru ditambahkan, {len(updated_objects)} baris diperbarui.",
            "errors": error_rows
        }, status=status.HTTP_201_CREATED if created_objects or updated_objects else status.HTTP_400_BAD_REQUEST)

class TemporaryUpdateBatchPatchAPIViewPF(APIView):
    permission_classes = [IsAuthenticated]

    def find_category_info(self, category_key):
        """
        Cari info parent, key, dan value berdasarkan category_key.

        :param category_key: string yang dicari, misal "jabatan_pj_badan"
        :return: (parent, key, value) jika ketemu, jika tidak None
        """

        categories_dict = {
            "consumers": {
                "jenis_nasabah": "customer_type",
                "pemberi_fidusia": "fiducia_provider",
                "nama_pada_bpkb": "name_on_bpkb",
                "nama_debitur": "debtor_name",
                "jenis_kelamin": "gender",
                "status_pernikahan": "marital_status",
                "tmp_lahir": "birth_place",
                "tgl_lahir": "birth_date",
                "pekerjaan": "occupation",
                "kewarganegaraan": "nationality",
                "jenis_identitas": "id_type",
                "no_identitas": "id_number",
                "alamat": "address",
                "rt_rw": "rt_rw",
                "kelurahan": "subdistrict",
                "kecamatan": "district",
                "kota": "city",
                "provinsi": "province",
                "kode_pos": "postal_code",
                "rt_rw_pj_badan": "director_rt_rw",
                "jenis_perjanjian": "agreement_type",
                "nama_perjanjian": "agreement_name",
                "tgl_perjanjian": "agreement_date",
                "nilai_hutang_pokok": "principal_loan",
                "nilai_penjaminan": "guarantee_value",
                "tgl_awal_cicilan": "go_live_date",
                "tenor": "tenor",
                "kondisi_kendaraan": "vehicle_condition",
                "merk": "brand",
                "model": "model",
                "tipe": "type",
                "warna": "color",
                "tahun_perakitan": "assembly_year",
                "tahun_pembuatan": "production_year",
                "no_rangka": "chassis_number",
                "no_mesin": "engine_number",
                "no_bpkb": "bpkb_number",
                "nilai_objek_otr": "otr_value",
                "tgl_akhir_cicilan": "installment_end_date",
                "kewarganegaraan_bpkb": "bpkb_nationality",
                "tempat_lahir_bpkb": "bpkb_birth_place",
                "tgl_lahir_bpkb": "bpkb_birth_date",
                "pekerjaan_bpkb": "bpkb_occupation",
                "provinsi_bpkb": "bpkb_province",
                "tgl_spb": "spb_date",
                "ktp_bpkb": "bpkb_ktp",
                "kabupaten_bpkb": "bpkb_city",
                "kecamatan_bpkb": "bpkb_district",
                "kelurahan_bpkb": "bpkb_subdistrict",
                "alamat_bpkb": "bpkb_address",
                "kode_pos_bpkb": "bpkb_postal_code",
                "rt_bpkb": "bpkb_rt",
                "rw_bpkb": "bpkb_rw",
            },
            "business_entity": {
                "jenis_badan": "entity_type",
                "npwp_badan": "npwp",
                "no_akta_badan": "deed_number",
                "tgl_akta_badan": "deed_date",
            },
            "business_entity_director": {
                "nama_pj_badan": "company_director_name",
                "jk_pj_badan": "director_gender",
                "status_pj_badan": "director_status",
                "tempat_lahir_pj_badan": "director_birth_place",
                "tgl_lahir_pj_badan": "director_birth_date",
                "alamat_pj_badan": "director_address",
                "kota_debitur": "city",
                "jabatan_pj_badan": "director_position",
                "kewarganegaraan_pj_badan": "director_nationality",
                "pekerjaan_pj_badan": "director_occupation",
                "rt_rw_pj_badan": "director_rt_rw",
                "kelurahan_pj_badan": "director_subdistrict",
                "kecamatan_pj_badan": "director_district",
                "provinsi_pj_badan": "director_province",
                "noid_pj_badan": "director_id_number",
                "notaris_badan_city": "notary_city",
                "notaris_pj_badan": "director_notary_name",
                "kota_pj_badan": "director_city",
            },
            "LegalDocument": {
                "biaya_pnbp": "pnbp_fee",
                "biaya_jasa": "code_value",
            }
        }

        for parent, mapping in categories_dict.items():
            for key, value in mapping.items():
                if key == category_key:
                    return parent, key, value
        return None

    @class_permission_required('update-data')
    def patch(self, request):
        ids = request.data.get('ids', [])
        if not isinstance(ids, list) or not ids:
            return Response({"error": "Parameter 'ids' harus berupa list dan tidak boleh kosong."}, status=status.HTTP_400_BAD_REQUEST)

        if _user_is_staff_cabang(request.user) and not _user_branch_codes(request.user):
            return Response({"detail": "Branch wajib diisi untuk user non-superadmin."}, status=403)

        is_approved = request.data.get('is_approved', True)
        update_status_value = request.data.get('update_status', 'approved')

        qs = _scope_updates_queryset(
            TemporaryUpdatePF.objects.filter(id__in=ids),
            request.user,
            change_of_fiduciary,
        )
        total_found = qs.count()
        self_blocked_count = 0

        if is_approved is True:
            base_target_qs = qs.filter(is_approved=False)
            self_blocked_count = base_target_qs.filter(uploaded_by=request.user).count()
            target_qs = base_target_qs.exclude(uploaded_by=request.user)
        else:
            target_qs = qs

        update_kwargs = {
            'is_approved': is_approved,
            'update_status': update_status_value,
            'approved_by_id': request.user.id,
        }
        try:
            TemporaryUpdatePF._meta.get_field('updated_at')
            update_kwargs['updated_at'] = timezone.now()
        except Exception:
            pass

        if not is_approved:
            updated_count = target_qs.update(**update_kwargs)
            not_updated = total_found - updated_count
            return Response({
                "total_requested": len(ids),
                "total_found": total_found,
                "updated": updated_count,
                "skipped": not_updated,
                "self_skipped": self_blocked_count,
            }, status=status.HTTP_200_OK)

        valid_ids = []
        invalid_ids = []

        to_process = list(target_qs.only('id', 'category', 'replacement_value', 'agreement_number'))

        for obj in to_process:
            result = self.find_category_info(obj.category)
            if result:
                valid_ids.append(obj.id)
            else:
                invalid_ids.append(obj.id)

        updated_count = 0
        if valid_ids:
            updated_count = _scope_updates_queryset(
                TemporaryUpdatePF.objects.filter(id__in=valid_ids),
                request.user,
                change_of_fiduciary,
            ).update(**update_kwargs)


        if updated_count > 0:
            updated_objs = _scope_updates_queryset(
                TemporaryUpdatePF.objects.filter(id__in=valid_ids, is_approved=True),
                request.user,
                change_of_fiduciary,
            )

            for obj in updated_objs:
                category = obj.category
                replacement_value = obj.replacement_value
                agreement_number = obj.agreement_number

                result = self.find_category_info(category)
                if not result:
                    continue
                parent, key, field = result
                qs_consumer = change_of_fiduciary.objects.filter(agreement_number=agreement_number)
                if _user_is_staff_cabang(request.user):
                    branch_codes = _user_branch_codes(request.user)
                    if not branch_codes:
                        continue
                    qs_consumer = qs_consumer.filter(
                        Q(branch__branch_code__in=branch_codes) | Q(branch_id__in=branch_codes)
                    )
                try:
                    consumer_obj = qs_consumer.get()
                    if hasattr(consumer_obj, field):
                        setattr(consumer_obj, field, replacement_value)
                        consumer_obj.save(update_fields=[field])
                except change_of_fiduciary.DoesNotExist:
                    pass

        not_updated = total_found - updated_count

        return Response({
            "total_requested": len(ids),
            "total_found": total_found,
            "updated": updated_count,
            "invalid_categories": len(invalid_ids),
            "skipped": not_updated,
            "self_skipped": self_blocked_count,
        }, status=status.HTTP_200_OK)

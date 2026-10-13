from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from import_cabang.models import Branch
from change_fiduciary.models import change_of_fiduciary, ActivityLogPf
from .serializers import ConsumerSerializerPF
from django.shortcuts import get_object_or_404, render
from AdminMenu.auth_helpers import class_permission_required, get_user_permissions, get_user_role_names
from common.const.role_map import has_role, ROLE_KEYS
from AdminUser.branch_access import get_primary_user_branch_code, get_user_branch_codes, user_is_branch_restricted
from rest_framework.permissions import IsAuthenticated
from django.http import JsonResponse, HttpResponse
from rest_framework.authentication import SessionAuthentication
from django.forms.models import model_to_dict
from datetime import datetime, date, timedelta, time
import openpyxl
from django.utils.timezone import localdate, make_aware, get_current_timezone, now, make_aware
from django.db.models.functions import Coalesce
import io
from django.db.models.deletion import ProtectedError
from django.db.models import OuterRef, Subquery
from prekonsumen.models import NotaryBranchArea
from openpyxl import Workbook
from decimal import Decimal
from warkahDokument.models import LegalDocument
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views import View

def _is_superadmin(user):
    role_names = list(get_user_role_names(user))
    return (
        has_role(role_names, ROLE_KEYS["SUPERADMIN"])
        or has_role(role_names, ROLE_KEYS["HO"])
        or has_role(role_names, ROLE_KEYS["OPERATOR"])
    )

def _user_branch_code(user):
    return get_primary_user_branch_code(user)

def _user_branch_codes(user):
    return get_user_branch_codes(user)

def _user_is_staff_cabang(user):
    """Check if user has staff_cabang role"""
    return user_is_branch_restricted(user)

class ConsumerListCreateAPIViewPF(LoginRequiredMixin, View):
    login_url = '/login/'

    @class_permission_required('read-pf')
    def get(self, request):
        consumers = change_of_fiduciary.objects.filter(st_process=0)
        if _user_is_staff_cabang(request.user):
            branch_codes = _user_branch_codes(request.user)
            if branch_codes:
                consumers = consumers.filter(branch__branch_code__in=branch_codes)
            else:
                consumers = consumers.none()
        branches = Branch.objects.filter(consumer__uploader=request.user).distinct()

        return render(request, 'operator/page-operator-prekonsumen-pf.html', {
            'consumers': consumers,
            'branches': branches,
            'data_list': consumers.count(),
        })

def first_filled(new_val, old_val):
    if new_val is None:
        return old_val
    if isinstance(new_val, str):
        return new_val.strip() if new_val.strip() != '' else old_val
    return new_val

def fmt_date(val):
    return val.strftime('%Y-%m-%d') if val else ''

def fmt_decimal(val):
    if val is None:
        return ''
    if isinstance(val, Decimal):
        return format(val, 'f')
    return str(val)

class ConsumerDataJSONPF(APIView):
    permission_classes = [IsAuthenticated]

    @class_permission_required('read-pf')
    def get(self, request):
        consumers = (
            change_of_fiduciary.objects
            .filter(st_process='0')
            .select_related('branch', 'uploader')
            .order_by('-created_at')
        )
        if _user_is_staff_cabang(request.user):
            branch_codes = _user_branch_codes(request.user)
            if branch_codes:
                consumers = consumers.filter(branch__branch_code__in=branch_codes)
            else:
                consumers = consumers.none()

        data = []
        for c in consumers:
            row = model_to_dict(c)

            row['branch_code'] = c.branch.branch_code if getattr(c, 'branch', None) else ''
            row['branch_name'] = c.branch_name or (c.branch.branch_name if getattr(c, 'branch', None) else '')
            row['uploader'] = c.uploader.username if getattr(c, 'uploader', None) else ''

            base_date_fields = [
                'agreement_date', 'upload_date', 'deed_date', 'birth_date',
                'bpkb_birth_date', 'decree_date', 'director_birth_date',
                'installment_start_date', 'installment_end_date', 'spb_date'
            ]
            for f in base_date_fields:
                row[f] = fmt_date(getattr(c, f, None))

            row['created_at'] = c.created_at.strftime('%Y-%m-%d %H:%M:%S') if c.created_at else ''
            row['updated_at'] = c.updated_at.strftime('%Y-%m-%d %H:%M:%S') if c.updated_at else ''

            row['status'] = c.status
            row['document_status'] = c.document_status
            row['invoice_st'] = c.invoice_st
            row['over_credit'] = c.over_credit
            row['sync'] = c.sync

            merge_pairs = [
                ('deed_number', 'new_deed_number', 'deed_number'),
                ('agreement_number', 'new_agreement_number', 'agreement_number'),
                ('address', 'new_address', 'address'),
                ('birth_place', 'new_birth_place', 'birth_place'),
                ('chassis_number', 'new_chassis_number', 'chassis_number'),
                ('engine_number', 'new_engine_number', 'engine_number'),
                ('type', 'new_type', 'type'),
                ('color', 'new_color', 'color'),
                ('brand', 'new_brand', 'brand'),
                ('name_on_bpkb', 'new_name_on_bpkb', 'name_on_bpkb'),
                ('nationality', 'new_nationality', 'nationality'),
                ('debtor_name', 'new_debtor_name', 'debtor_name'),
                ('occupation', 'new_occupation', 'occupation'),
                ('id_type', 'new_id_type', 'id_type'),
                ('id_number', 'new_id_number', 'id_number'),
                ('rt_rw', 'new_rt_rw', 'rt_rw'),
                ('subdistrict', 'new_subdistrict', 'subdistrict'),
                ('district', 'new_district', 'district'),
                ('city', 'new_city', 'city'),
                ('province', 'new_province', 'province'),
                ('postal_code', 'new_postal_code', 'postal_code'),

                ('bpkb_nationality', 'new_bpkb_nationality', 'bpkb_nationality'),
                ('bpkb_birth_place', 'new_bpkb_birth_place', 'bpkb_birth_place'),
                ('bpkb_birth_terbilang', 'new_bpkb_birth_terbilang', 'bpkb_birth_terbilang'),
                ('bpkb_occupation', 'new_bpkb_occupation', 'bpkb_occupation'),
                ('bpkb_city', 'new_bpkb_city', 'bpkb_city'),
                ('bpkb_address', 'new_bpkb_address', 'bpkb_address'),
                ('bpkb_rt', 'new_bpkb_rt', 'bpkb_rt'),
                ('bpkb_rw', 'new_bpkb_rw', 'bpkb_rw'),
                ('bpkb_district', 'new_bpkb_district', 'bpkb_district'),
                ('bpkb_subdistrict', 'new_bpkb_subdistrict', 'bpkb_subdistrict'),
                ('bpkb_province', 'new_bpkb_province', 'bpkb_province'),
                ('bpkb_postal_code', 'new_bpkb_postal_code', 'bpkb_postal_code'),

                ('gender', 'new_gender', 'gender'),
                ('marital_status', 'new_marital_status', 'marital_status'),
            ]
            for out_key, new_key, old_key in merge_pairs:
                row[out_key] = first_filled(getattr(c, new_key, None), getattr(c, old_key, None))

            merge_date_pairs = [
                ('deed_date', 'new_deed_date', 'deed_date'),
                ('birth_date', 'new_birth_date', 'birth_date'),
                ('bpkb_birth_date', 'new_bpkb_birth_date', 'bpkb_birth_date'),
            ]
            for out_key, new_key, old_key in merge_date_pairs:
                eff_val = first_filled(getattr(c, new_key, None), getattr(c, old_key, None))
                row[out_key] = fmt_date(eff_val)

            row['otr_value'] = fmt_decimal(first_filled(getattr(c, 'new_otr_values', None), getattr(c, 'otr_value', None)))

            if hasattr(c, 'pnbp_fee'):
                row['pnbp_fee'] = fmt_decimal(getattr(c, 'pnbp_fee'))
            if hasattr(c, 'service_fee'):
                row['service_fee'] = fmt_decimal(getattr(c, 'service_fee'))

            data.append(row)

        return JsonResponse(data, safe=False)

class ConsumerDetailAPIViewPF(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, pk):
        return get_object_or_404(change_of_fiduciary, pk=pk)

    @class_permission_required('read-pf')
    def get(self, request, pk):
        consumer = self.get_object(pk)
        serializer = ConsumerSerializerPF(consumer)
        return Response(serializer.data)

    @class_permission_required('update-pf')
    def put(self, request, pk):
        consumer = self.get_object(pk)
        serializer = ConsumerSerializerPF(consumer, data=request.data, context={'request': request}, partial=True)
        if serializer.is_valid():
            updated_consumer = serializer.save()
            return Response(ConsumerSerializerPF(updated_consumer).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @class_permission_required('delete-pf')
    def delete(self, request, pk):
        try:
            consumer = self.get_object(pk)
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

def _filled(v):
    return v is not None and (not isinstance(v, str) or v.strip() != '')

def eff(new_val, old_val):
    """pilih nilai efektif: pakai new_val jika terisi, else old_val"""
    return new_val if _filled(new_val) else old_val

def fmt_num(val):
    if val is None:
        return ''
    if isinstance(val, Decimal):
        # openpyxl bisa terima float; kalau mau string juga boleh
        try:
            return float(val)
        except Exception:
            return str(val)
    if isinstance(val, (int, float)):
        return val
    return str(val)

class ConsumerExportAPIViewPF(APIView):
    permission_classes = [IsAuthenticated]

    @class_permission_required('read-pf')
    def get(self, request):
        download_type = request.GET.get('download', '').lower()
        if download_type != 'excel':
            return Response({"detail": "Invalid download type"}, status=400)

        # (opsional) filter tanggal jika diperlukan
        # tz = get_current_timezone()
        # today = localdate()
        # start_today = make_aware(datetime.combine(today, time.min), timezone=tz)
        # end_today = make_aware(datetime.combine(today + timedelta(days=1), time.min), timezone=tz)

        ld_latest = (
            LegalDocument.objects
            .filter(consumer__agreement_number=OuterRef('agreement_number'))
            .order_by('-document_date', '-id')
        )

        consumers = (
            change_of_fiduciary.objects
            .filter(st_process='0')
            .annotate(
                # === tiga kolom dari LegalDocument ===
                ld_document_number = Subquery(ld_latest.values('document_number')[:1]),
                ld_document_date   = Subquery(ld_latest.values('document_date')[:1]),
                #ld_notary_id       = Subquery(ld_latest.values('notary_id')[:1]),
                # (opsional) kalau perlu nama firma notaris:
                ld_notary_firm     = Subquery(ld_latest.values('notary__firm_name')[:1]),
            )
            .select_related('branch', 'uploader')
            .order_by('-created_at')
        )
        if _user_is_staff_cabang(request.user):
            branch_codes = _user_branch_codes(request.user)
            if branch_codes:
                consumers = consumers.filter(branch__branch_code__in=branch_codes)
            else:
                consumers = consumers.none()

        # 1) Workbook & sheet
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Consumers"

        # 2) Header (tetap, tidak diubah urutannya)
        headers = [
            'id','nama_cabang','jenis_nasabah', 'no_akta_lama', 'tgl_akta_lama', 'notaris_lama', 'pemberi_fidusia','nama_pada_bpkb','nama_debitur',
            'tgl_lahir_bpkb','tempat_lahir_bpkb','pekerjaan_bpkb','kewarganegaraan_bpkb','alamat_bpkb','rt_bpkb','rw_bpkb',
            'kelurahan_bpkb','kecamatan_bpkb','kabupaten_bpkb','provinsi_bpkb','kode_pos_bpkb','ktp_bpkb',
            'jenis_kelamin','status_pernikahan','tmp_lahir','tgl_lahir','pekerjaan','kewarganegaraan',
            'jenis_identitas','no_identitas','alamat','rt_rw','kelurahan','kecamatan','kota','provinsi','kode_pos',
            'jenis_badan','npwp_badan','no_akta_badan','tgl_akta_badan','nama_pj_badan','jk_pj_badan','status_pj_badan',
            'tempat_lahir_pj_badan','tgl_lahir_pj_badan','kewarganegaraan_pj_badan','typeid_pj_badan','noid_pj_badan',
            'alamat_pj_badan','rt_rw_pj_badan','kelurahan_pj_badan','kecamatan_pj_badan','kota_pj_badan','provinsi_pj_badan',
            'notaris_pj_badan','notaris_badan_city','jabatan_pj_badan',
            'jenis_perjanjian','nama_perjanjian','no_perjanjian','tgl_perjanjian','tgl_spb',
            'nilai_hutang_pokok','nilai_penjaminan','tgl_awal_cicilan','tenor','kondisi_kendaraan',
            'merk','tipe','warna','tahun_perakitan','tahun_pembuatan','no_rangka','no_mesin','no_bpkb',
            'nilai_objek_otr','biaya_pnbp','biaya_jasa','tgl_akhir_cicilan',
            'id_no_bpkb','no_npwp_bpkb','no_telp','an_kwitansi','nama_pasangan',
            'uploader_company','group_upload','invoice_st','proses_st','used_st','uploader_id',
            'created_at','updated_at','no','rw','nama_bpkb','status','upload_date','status_dokumen'
        ]

        # 2b) Tambahan header untuk kolom "new_*" (diletakkan DI BELAKANG agar tidak ganggu urutan lama)
        new_headers = [
            'no_perjanjian_baru', 'no_akta_badan_baru','tgl_akta_badan_baru', 'notaris_baru', 'pemberi_fidusia_Baru', 'nama_bpkb_baru',
            'nama_debitur_baru', 'tempat_lahir_bpkb_baru', 'tanggal_lahir_bpkb_baru', 'tanggal_lahir_bpkb_baru_terbilang',
            'pekerjaan_bpkb_baru', 'alamat_bpkb_baru', 'rt_bpkb_baru', 'rw_bpkb_baru', 'kalurahan_bpkb_baru', 'kecamatan_bpkb_baru',
            'kabupaten_bpkb_baru', 'provinsi_bpkb_baru', 'kode_pos_bpkb_baru', 'kewarganegaraan_bpkb_baru', 'ktp_bpkb_baru',
            'jenis_kelamin_baru', 'status_pernikahan_baru', 'jenis_id_baru', 'tmp_lahir_baru', 'tgl_lahir_baru', 'pekerjaan_baru',
            'alamat_baru', 'rt_rw_baru', 'kelurahan_baru', 'kecamatan_baru', 'kota_baru', 'provinsi_baru', 'kode_pos_baru',
            'kewarganegaraan_baru', 'nomor_id_baru', 'merk_baru', 'tipe_baru','warna_baru', 'nomor_rangka_baru', 'nomor_mesin_baru',
            'nilai_objek_otr_baru'
        ]

        ws.append(headers + new_headers)

        # 3) Data rows
        for c in consumers:
            # Dapatkan notaris firma dari branch (seperti punyamu)
            firm_name = ''
            if c.branch:
                nba = (
                    NotaryBranchArea.objects
                    .filter(branch__branch_code=c.branch.branch_code)
                    .select_related('notary')
                    .first()
                )
                if nba and nba.notary:
                    firm_name = nba.notary.firm_name or ''

            # ==== Row utama (pakai nilai EFEKTIF ke header lama) ====
            row = [
                c.id,
                c.branch.branch_code if c.branch else '',
                c.customer_type,
                (c.ld_document_number or ''),
                fmt_date(c.ld_document_date),
                (c.ld_notary_firm or ''),
                c.fiducia_provider,
                c.name_on_bpkb,                                  # nama_pada_bpkb (efektif)
                c.debtor_name,                                # nama_debitur (efektif)

                fmt_date(c.bpkb_birth_date),                  # tgl_lahir_bpkb (efektif)
                c.bpkb_birth_place,                           # tempat_lahir_bpkb (efektif)
                c.bpkb_occupation,                            # pekerjaan_bpkb (efektif)
                c.bpkb_nationality,                           # kewarganegaraan_bpkb (efektif)
                c.bpkb_address,                               # alamat_bpkb (efektif)
                c.bpkb_rt,                                    # rt_bpkb (efektif)
                c.bpkb_rw,                                    # rw_bpkb (efektif)
                c.bpkb_subdistrict,                           # kelurahan_bpkb (efektif)
                c.bpkb_district,                              # kecamatan_bpkb (efektif)
                c.bpkb_city,                                  # kabupaten_bpkb (efektif)
                c.bpkb_province,                              # provinsi_bpkb (efektif)
                c.bpkb_postal_code,                                # kode_pos_bpkb (efektif)
                c.bpkb_ktp,                                   # ktp_bpkb (efektif)

                c.gender,                                     # jenis_kelamin (efektif)
                c.marital_status,                              # status_pernikahan (efektif)
                c.birth_place,                                 # tmp_lahir (efektif)
                fmt_date(c.birth_date),                        # tgl_lahir (efektif)
                c.occupation,                                  # pekerjaan (efektif)
                c.nationality,                                 # kewarganegaraan (efektif)

                c.id_type,                                     # jenis_identitas (efektif)
                c.id_number,                                   # no_identitas (efektif)
                c.address,                                     # alamat (efektif)
                c.rt_rw,                                       # rt_rw (efektif)
                c.subdistrict,                                 # kelurahan (efektif)
                c.district,                                    # kecamatan (efektif)
                c.city,                                        # kota (efektif)
                c.province,                                    # provinsi (efektif)
                c.postal_code,                                  # kode_pos (efektif)

                c.company_type,
                c.company_npwp,
                c.deed_number,                                 # no_akta_badan (efektif)
                fmt_date(c.deed_date),                         # tgl_akta_badan (efektif)
                c.company_director_name,
                c.director_gender,
                c.director_status,
                c.director_birth_place,
                fmt_date(c.director_birth_date),
                c.director_nationality,

                c.director_id_type,
                c.director_id_number,
                c.director_address,
                c.director_rt_rw,
                c.director_subdistrict,
                c.director_district,
                c.director_city,
                c.director_province,

                c.director_notary_name,
                c.notary_city,
                c.director_position,

                c.agreement_type,
                c.agreement_name,
                c.agreement_number,                            # no_perjanjian (efektif)
                fmt_date(c.agreement_date),
                fmt_date(c.spb_date),

                fmt_num(c.principal_loan),
                fmt_num(c.guarantee_value),
                fmt_date(c.go_live_date),
                c.tenor,
                c.vehicle_condition,

                c.brand,                                      # merk (efektif)
                c.type,                                       # tipe (efektif)
                c.color,                                      # warna (efektif)
                c.assembly_year,
                c.production_year,
                c.chassis_number,                                    # no_rangka (efektif)
                c.engine_number,                                     # no_mesin (efektif)
                c.bpkb_number,

                fmt_num(c.otr_value),                               # nilai_objek_otr (efektif)
                fmt_num(c.pnbp_fee),
                fmt_num(c.service_fee),
                fmt_date(c.installment_end_date),

                c.bpkb_id_number,
                c.bpkb_npwp_number,
                c.phone_number,
                c.receipt_name,
                c.spouse_name,

                c.uploader_company,
                c.upload_group,
                c.invoice_st,
                c.st_process,
                c.is_st_used,
                (c.uploader.id if c.uploader else ''),

                c.created_at.strftime('%Y-%m-%d') if c.created_at else '',
                c.updated_at.strftime('%Y-%m-%d') if c.updated_at else '',
                c.house_number,
                c.rw,
                c.bpkb_name,
                c.status,
                fmt_date(c.upload_date),
                c.document_status
            ]

            # ==== Tambahan kolom new_* mentah (untuk audit); diletakkan di belakang ====
            new_tail = [
                c.new_agreement_number, # no_perjanjian_baru
                c.new_deed_number, # no_akta_badan_baru
                fmt_date(c.new_deed_date), # no_tanggal_akta_badan_baru
                firm_name, # notris_baru
                c.fiducia_provider, # pemberi_fidusia_Baru
                c.new_name_on_bpkb, # nama_bpkb_baru
                c.new_debtor_name, # nama_debitur_baru
                c.new_bpkb_birth_place, # tempat_lahir_bpkb_baru
                fmt_date(c.new_bpkb_birth_date), # tanggal_lahir_bpkb_baru
                c.new_bpkb_birth_terbilang, # tanggal_lahir_bpkb_baru_terbilang
                c.new_bpkb_occupation, # pekerjaan_bpkb_baru
                c.new_bpkb_address, # alamat_bpkb_baru
                c.new_bpkb_rt, # rt_bpkb_baru
                c.new_bpkb_rw, # rw_bpkb_baru
                c.new_bpkb_subdistrict, # kalurahan_bpkb_baru
                c.new_bpkb_district, # kecamatan_bpkb_baru
                c.new_bpkb_city, # kabupaten_bpkb_baru
                c.new_bpkb_province, # provinsi_bpkb_baru
                c.new_bpkb_postal_code, # kode_pos_bpkb_baru
                c.new_bpkb_nationality, # kewarganegaraan_bpkb_baru
                c.new_bpkb_ktp, # ktp_bpkb_baru
                c.new_gender, # jenis_kelamin_baru
                c.new_marital_status, # status_pernikahan_baru
                c.new_id_type, # jenis_id_baru
                c.new_birth_place, # tmp_lahir_baru
                fmt_date(c.new_birth_date), # tgl_lahir_baru
                c.new_occupation, # pekerjaan_baru
                c.new_address, # alamat_baru
                c.new_rt_rw, # rt_rw_baru
                c.new_subdistrict, # kelurahan_baru
                c.new_district, # kecamatan_baru
                c.new_city, # kota_baru
                c.new_province, # provinsi_baru
                c.new_postal_code, # kode_pos_baru
                c.new_nationality, # kewarganegaraan_baru
                c.new_id_number, # nomor_id_baru
                c.new_brand, # merk_baru
                c.new_type, # tipe_baru
                c.new_color, # warna_baru
                c.new_chassis_number, # nomor_rangka_baru
                c.new_engine_number, # nomor_mesin_baru
                fmt_num(c.new_otr_values), # nilai_objek_otr_baru
            ]

            ws.append(row + new_tail)

        # 4) Stream ke response
        stream = io.BytesIO()
        wb.save(stream)
        stream.seek(0)

        filename = "Konsumen.xlsx"
        response = HttpResponse(
            stream,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

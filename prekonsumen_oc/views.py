from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from import_cabang_oc.models import ConsumerOC, Branch
from .serializers import ConsumerSerializer
from django.shortcuts import get_object_or_404
from accesslogoc.models import activity_logs_oc
from AdminMenu.auth_helpers import class_permission_required, get_user_permissions, get_user_role_names
from common.const.role_map import has_role, ROLE_KEYS
from AdminUser.branch_access import get_primary_user_branch_code, get_user_branch_codes, user_is_branch_restricted
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import render
from django.http import JsonResponse
from rest_framework.authentication import SessionAuthentication
from django.utils.timezone import localdate
from django.forms.models import model_to_dict
from datetime import datetime, time, timedelta
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.parsers import JSONParser
from django.utils.timezone import localdate
from django.utils.timezone import make_aware
import openpyxl
from django.utils.timezone import localdate, make_aware, get_current_timezone
import io
from django.utils.timezone import now
from django.utils import timezone
from django.http import HttpResponse
from django.db.models.deletion import ProtectedError
from prekonsumen.models import NotaryBranchArea
from prekonsumen.views import _fmt_date, _fmt_datetime
from django.db.models import Q, F, Value
from django.db.models.functions import Coalesce
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views import View

def _is_superadmin(user):
    role_names = list(get_user_role_names(user))
    return (
        has_role(role_names, ROLE_KEYS["SUPERADMIN"])
        or has_role(role_names, ROLE_KEYS["HO"])
        or has_role(role_names, ROLE_KEYS["OPERATOR"])
    )

def _user_is_staff_cabang(user):
    """Check if user has staff_cabang role"""
    return user_is_branch_restricted(user)

def _user_branch_code(user):
    return get_primary_user_branch_code(user)

def _user_branch_codes(user):
    return get_user_branch_codes(user)
# ---------------------------
# LIST + CREATE (OC)
# ---------------------------
class ConsumerListCreateAPIViewOC(LoginRequiredMixin, View):
    login_url = '/login/'

    @class_permission_required('read-konsumen-oc')
    def get(self, request):
        consumers = ConsumerOC.objects.filter(st_process=0)
        if _user_is_staff_cabang(request.user):
            branch_codes = _user_branch_codes(request.user)
            if branch_codes:
                consumers = consumers.filter(branch__branch_code__in=branch_codes)
            else:
                consumers = consumers.none()
        branches = Branch.objects.filter(consumeroc__uploader=request.user).distinct()

        return render(request, 'operator/page-operator-prekonsumen-oc.html', {
            'consumers': consumers,
            'branches': branches,
            'data_list': consumers.count(),
        })

# ---------------------------
# DATA JSON (OC)
# ---------------------------
class ConsumerDataJSONOC(APIView):
    permission_classes = [IsAuthenticated]

    COLUMN_MAP = [
        'id','document_status','branch_name','customer_type','name_on_bpkb','debtor_name',
        'gender','marital_status','birth_place','birth_date','occupation','nationality',
        'id_type','id_number','address','rt_rw','subdistrict','district','city','province',
        'postal_code','company_type','company_npwp','deed_number','deed_date','company_director_name',
        'director_gender','director_status','director_birth_place','director_birth_date',
        'director_nationality','director_id_type','director_id_number','director_address',
        'director_rt_rw','director_subdistrict','director_district','director_city','director_province',
        'director_position','agreement_type','agreement_name','agreement_number','agreement_date',
        'principal_loan','guarantee_value','installment_start_date','tenor','vehicle_condition',
        'brand','type','color','assembly_year','production_year','chassis_number','engine_number',
        'bpkb_number','otr_value','pnbp_fee','service_fee'
    ]

    def _parse_params(self, request):
        """
        Dukung dua skema:
        - DataTables default via GET: draw, start, length, search[value], order[0][column], order[0][dir]
        - Payload ringkas via POST: draw, start, length, search_value, order_col_index|order_col_name, order_dir
        """
        method = request.method.upper()
        dget = request.GET

        if method == 'GET':
            draw   = int(dget.get('draw', '1'))
            start  = int(dget.get('start', '0'))
            length = int(dget.get('length', '10'))
            search_value = (dget.get('search[value]', '') or '').strip()

            col_idx = dget.get('order[0][column]')
            order_col_index = int(col_idx) if (col_idx and col_idx.isdigit()) else 0
            order_dir = (dget.get('order[0][dir]') or 'desc').lower()
            order_col_name = self.COLUMN_MAP[order_col_index] if 0 <= order_col_index < len(self.COLUMN_MAP) else 'id'
        else:
            data = request.data
            draw   = int(data.get('draw', 1))
            start  = int(data.get('start', 0))
            length = int(data.get('length', 10))
            search_value = (data.get('search_value') or '').strip()
            order_dir = (data.get('order_dir') or 'desc').lower()

            if 'order_col_name' in data and data.get('order_col_name') in self.COLUMN_MAP:
                order_col_name = data.get('order_col_name')
            else:
                order_col_index = int(data.get('order_col_index', 0))
                if 0 <= order_col_index < len(self.COLUMN_MAP):
                    order_col_name = self.COLUMN_MAP[order_col_index]
                else:
                    order_col_name = 'id'

        return draw, start, length, search_value, order_col_name, order_dir

    def _build_response(self, request, draw, start, length, search_value, order_col_name, order_dir):
        qs = (
            ConsumerOC.objects
            .filter(st_process='0')
            .select_related('branch', 'uploader')
            .annotate(
                branch_code_anno = Coalesce(F('branch__branch_code'), Value('')),
                branch_name_anno = Coalesce(F('branch__branch_name'), Value('')),
                uploader_anno    = Coalesce(F('uploader__username'), Value('')),
            )
        )

        if _user_is_staff_cabang(request.user):
            branch_codes = _user_branch_codes(request.user)
            if branch_codes:
                qs = qs.filter(branch__branch_code__in=branch_codes)
            else:
                qs = qs.none()
        records_total = qs.count()

        if search_value:
            SEARCHABLE_FIELDS = [
                'document_status','branch_name_anno','customer_type','name_on_bpkb','debtor_name',
                'gender','marital_status','birth_place','occupation','nationality','id_type','id_number',
                'address','rt_rw','subdistrict','district','city','province','postal_code','company_type',
                'company_npwp','deed_number','company_director_name','director_gender','director_status',
                'director_birth_place','director_nationality','director_id_type','director_id_number',
                'director_address','director_rt_rw','director_subdistrict','director_district','director_city',
                'director_province','director_position','agreement_type','agreement_name','agreement_number',
                'vehicle_condition','brand','type','color','chassis_number','engine_number','bpkb_number',
                'uploader_anno',
            ]
            q = Q()
            for f in SEARCHABLE_FIELDS:
                q |= Q(**{f'{f}__icontains': search_value})
            if search_value.isdigit():
                q |= Q(tenor__exact=int(search_value))
            qs = qs.filter(q)

        records_filtered = qs.count()

        field = order_col_name
        if field == 'branch_name':
            field = 'branch_name_anno'
        elif field == 'branch_code':
            field = 'branch_code_anno'

        if order_dir == 'desc':
            field = f'-{field}'
        qs = qs.order_by(field, 'id')

        if length != -1:
            qs = qs[start:start + length]

        data = []
        for c in qs:
            mapping_document_status = {
                '0': 'Uncomplete',
                '1': 'Complete',
                '2': 'Process',
                '3': 'Revisi',
            }
            document_status = mapping_document_status.get(c.document_status, 'unknown')
            data.append({
                'id': c.id,
                'document_status': document_status,
                'branch_name': c.branch_name or c.branch_name_anno,
                'customer_type': c.customer_type,
                'name_on_bpkb': c.name_on_bpkb,
                'debtor_name': c.debtor_name,
                'gender': c.gender,
                'marital_status': c.marital_status,
                'birth_place': c.birth_place,
                'birth_date': _fmt_date(c.birth_date),
                'occupation': c.occupation,
                'nationality': c.nationality,
                'id_type': c.id_type,
                'id_number': c.id_number,
                'address': c.address,
                'rt_rw': c.rt_rw,
                'subdistrict': c.subdistrict,
                'district': c.district,
                'city': c.city,
                'province': c.province,
                'postal_code': c.postal_code,

                'company_type': c.company_type,
                'company_npwp': c.company_npwp,
                'deed_number': c.deed_number,
                'deed_date': _fmt_date(c.deed_date),

                'company_director_name': c.company_director_name,
                'director_gender': c.director_gender,
                'director_status': c.director_status,
                'director_birth_place': c.director_birth_place,
                'director_birth_date': _fmt_date(c.director_birth_date),
                'director_nationality': c.director_nationality,
                'director_id_type': c.director_id_type,
                'director_id_number': c.director_id_number,
                'director_address': c.director_address,
                'director_rt_rw': c.director_rt_rw,
                'director_subdistrict': c.director_subdistrict,
                'director_district': c.director_district,
                'director_city': c.director_city,
                'director_province': c.director_province,
                'director_position': c.director_position,

                'agreement_type': c.agreement_type,
                'agreement_name': c.agreement_name,
                'agreement_number': c.agreement_number,
                'agreement_date': _fmt_date(c.agreement_date),
                'principal_loan': c.principal_loan,
                'guarantee_value': c.guarantee_value,
                'installment_start_date': _fmt_date(c.installment_start_date),
                'tenor': c.tenor,

                'vehicle_condition': c.vehicle_condition,
                'brand': c.brand,
                'type': c.type,
                'color': c.color,
                'assembly_year': c.assembly_year,
                'production_year': c.production_year,
                'chassis_number': c.chassis_number,
                'engine_number': c.engine_number,
                'bpkb_number': c.bpkb_number,

                'otr_value': c.otr_value,
                'pnbp_fee': c.pnbp_fee,
                'service_fee': c.service_fee,

                'created_at': _fmt_datetime(c.created_at),
                'updated_at': _fmt_datetime(c.updated_at),

                'over_credit': c.over_credit,
                'sync': c.sync,
            })

        return JsonResponse({
            'draw': draw,
            'recordsTotal': records_total,
            'recordsFiltered': records_filtered,
            'data': data,
        }, safe=False)

    @class_permission_required('read-konsumen-oc')
    def get(self, request):
        draw, start, length, search_value, order_col_name, order_dir = self._parse_params(request)
        return self._build_response(request, draw, start, length, search_value, order_col_name, order_dir)

    @class_permission_required('read-konsumen-oc')
    def post(self, request):
        draw, start, length, search_value, order_col_name, order_dir = self._parse_params(request)
        return self._build_response(request, draw, start, length, search_value, order_col_name, order_dir)

# ---------------------------
# DETAIL + UPDATE + DELETE (OC)
# ---------------------------
class ConsumerDetailAPIViewOC(APIView):
    authentication_classes = [JWTAuthentication]      # samakan dgn consumer
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]                     # terima body JSON

    def get_object(self, pk):
        return get_object_or_404(ConsumerOC, pk=pk)

    @class_permission_required('read-konsumen-oc')
    def get(self, request, pk):
        consumer = self.get_object(pk)
        ser = ConsumerSerializer(consumer, context={'request': request})
        return Response(ser.data)

    @class_permission_required('update-konsumen-oc')
    def put(self, request, pk):
        consumer = self.get_object(pk)
        ser = ConsumerSerializer(
            consumer, data=request.data,
            context={'request': request},
            partial=True  # partial PUT seperti consumer
        )
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data, status=status.HTTP_200_OK)

    @class_permission_required('update-konsumen-oc')
    def patch(self, request, pk):
        return self.put(request, pk)

    @class_permission_required('delete-konsumen-oc')
    def delete(self, request, pk):
        try:
            consumer = self.get_object(pk)
            consumer.delete()

            # logging: pakai yang sudah kamu tulis (jangan ubah fungsi)
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


# ---------------------------
# EXPORT EXCEL (OC) — SAME AS consumer
# ---------------------------
class ConsumerExportAPIViewOC(APIView):
    permission_classes = [IsAuthenticated]

    @class_permission_required('read-konsumen-oc')
    def get(self, request):
        download_type = request.GET.get('download', '').lower()
        if download_type != 'excel':
            return Response({"detail": "Invalid download type"}, status=400)

        # Filter berdasarkan tanggal hari ini
        tz = get_current_timezone()
        today = localdate()
        start_today = make_aware(datetime.combine(today, time.min), timezone=tz)
        end_today = make_aware(datetime.combine(today + timedelta(days=1), time.min), timezone=tz)

        consumers = ConsumerOC.objects.filter(
            st_process='0'
        )
        if _user_is_staff_cabang(request.user):
            branch_codes = _user_branch_codes(request.user)
            if branch_codes:
                consumers = consumers.filter(branch__branch_code__in=branch_codes)
            else:
                consumers = consumers.none()

        # 1) Buat workbook & sheet dulu
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Consumers"

        # 2) Header (Bahasa Indonesia)
        headers = [
            'id','nama_cabang','jenis_nasabah','pemberi_fidusia','nama_pada_bpkb','nama_debitur',
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
            'created_at','updated_at','no','rw','nama_bpkb','status','upload_date','status_dokumen','notaris'
        ]
        ws.append(headers)

        # 3) Baris data (di dalam loop)
        for c in consumers:
            mapping_document_status = {
                '0': 'Uncomplete',
                '1': 'Complete',
                '2': 'Process',
                '3': 'Revisi',
            }
            document_status = mapping_document_status.get(c.document_status, 'unknown')
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

            row = [
                c.id,                                        # id
                c.branch.branch_code if c.branch else '',    # kode_cabang
                c.customer_type,
                c.fiducia_provider,
                c.name_on_bpkb,
                c.debtor_name,

                c.bpkb_birth_date.strftime('%Y-%m-%d') if c.bpkb_birth_date else '',
                c.bpkb_birth_place,
                c.bpkb_occupation,
                c.bpkb_nationality,
                c.bpkb_address,
                c.bpkb_rt,
                c.bpkb_rw,
                c.bpkb_subdistrict,
                c.bpkb_district,
                c.bpkb_city,
                c.bpkb_province,
                c.bpkb_postal_code,
                c.bpkb_ktp,

                c.gender,
                c.marital_status,
                c.birth_place,
                c.birth_date.strftime('%Y-%m-%d') if c.birth_date else '',
                c.occupation,
                c.nationality,

                c.id_type,
                c.id_number,
                c.address,
                c.rt_rw,
                c.subdistrict,
                c.district,
                c.city,
                c.province,
                c.postal_code,

                c.company_type,
                c.company_npwp,
                c.deed_number,
                c.deed_date.strftime('%Y-%m-%d') if c.deed_date else '',
                c.company_director_name,
                c.director_gender,
                c.director_status,
                c.director_birth_place,
                c.director_birth_date.strftime('%Y-%m-%d') if c.director_birth_date else '',
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
                c.agreement_number,
                c.agreement_date.strftime('%Y-%m-%d') if c.agreement_date else '',
                c.spb_date.strftime('%Y-%m-%d') if c.spb_date else '',

                float(c.principal_loan) if c.principal_loan else '',
                float(c.guarantee_value) if c.guarantee_value else '',
                c.go_live_date.strftime('%Y-%m-%d') if c.go_live_date else '',
                c.tenor,
                c.vehicle_condition,

                c.brand,
                c.type if not c.model or c.model.strip() == '-' else c.model,
                c.color,
                c.assembly_year,
                c.production_year,
                c.chassis_number,
                c.engine_number,
                c.bpkb_number,

                float(c.otr_value) if c.otr_value else '',
                float(c.pnbp_fee) if c.pnbp_fee else '',
                float(c.service_fee) if c.service_fee else '',
                c.installment_end_date.strftime('%Y-%m-%d') if c.installment_end_date else '',

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
                c.upload_date.strftime('%Y-%m-%d') if c.upload_date else '',
                document_status,
                firm_name
            ]
            ws.append(row)

        # 4) Stream ke response
        stream = io.BytesIO()
        wb.save(stream)
        stream.seek(0)

        filename = f"KonsumenOC.xlsx"
        response = HttpResponse(
            stream,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

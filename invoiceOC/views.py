from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import render
from import_cabang_oc.models import ConsumerOC
from datetime import datetime, time
from django.utils.timezone import now, make_aware, is_aware
from AdminMenu.auth_helpers import (
    class_permission_required,
    user_has_permission,
    get_user_permissions,
    permission_required,
    get_user_role_names
)
from common.const.role_map import has_role, ROLE_KEYS
from AdminUser.branch_access import get_primary_user_branch_code, get_user_branch_codes, user_is_branch_restricted
import io, openpyxl, re, logging
from openpyxl.styles import Alignment
from AktaSertifikat.models import LegalDocumentsOC
from calendar import monthrange
from import_cabang.models import Branch
from AktaSertifikat.views import determine_status
from django.core.exceptions import MultipleObjectsReturned, ValidationError
from django.db.models import Q, F, DateTimeField, OuterRef, Subquery, Count, Value, Exists, CharField, DateField
from django.db import transaction, IntegrityError
from decimal import Decimal
from .models import tb_invoice_oc
from django.db.models.functions import Coalesce, Cast, TruncDate
from common.util.id_formatters import format_rupiah
from django.http import HttpResponse, FileResponse
from types import SimpleNamespace
from datetime import date, datetime

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
    )

def _user_is_staff_cabang(user):
    """Check if user has staff_cabang role"""
    return user_is_branch_restricted(user)
from django.forms.models import model_to_dict
from invoice.fees import SERVICE_FEE_KEY, SERVICE_FEE_TIER2_KEY, common_code_decimal
from collections import defaultdict
logger = logging.getLogger(__name__)

@permission_required('read-invoice')
def invoice_reguler_page_oc(request):
    deed_date_str = request.GET.get('deed_date')
    branches = Branch.objects.all()

    if not deed_date_str:
        deed_date_str = date.today().strftime('%Y-%m-%d')

    try:
        deed_date_obj = datetime.strptime(deed_date_str, "%Y-%m-%d").date()
    except ValueError:
        deed_date_obj = deed_date_str  # fallback ke string bila format tak valid

    docs_qs = (
        LegalDocumentsOC.objects
        .annotate(pay_date=Cast('payment_date', DateField()))  # <= kunci perbaikan
        .filter(
            pay_date=deed_date_obj,           # bandingkan date ke date
            consumer__invoice_st=0,
            consumer__agreement_number__isnull=False
        )
        .exclude(consumer__agreement_number='')
        .filter(document_number__isnull=False)
        .exclude(document_number=0)
        .filter(consumer__debtor_name__isnull=False)
        .exclude(consumer__debtor_name='')
        .filter(notary__isnull=False)
    )
    if _user_is_staff_cabang(request.user):
        branch_codes = _user_branch_codes(request.user)
        if not branch_codes:
            docs_qs = docs_qs.none()
            branches = Branch.objects.none()
        else:
            docs_qs = docs_qs.filter(consumer__branch__branch_code__in=branch_codes)
            branches = Branch.objects.filter(branch_code__in=branch_codes)

    latest_invoice_subquery = tb_invoice_oc.objects.filter(
        agreement_number=OuterRef('consumer__agreement_number')
    ).order_by('-id')

    docs_data = list(
        docs_qs.values(
            'voucher_code', 'registration_date', 'certificate_number',
            'payment_date', 'certificate_path', 'certificate_date',
            
            consumerId=F('consumer__id'),
            branch_code=F('consumer__branch__branch_code'),
            branch_name=Coalesce(
                F('consumer__branch__branch_name'),
                F('consumer__branch_name'),
                output_field=CharField()
            ),
            documentNumber=F('document_number'),
            documentDate=F('document_date'),
            agreement_number=F('consumer__agreement_number'),
            debtor_name=F('consumer__debtor_name'),
            guarantee_value=F('consumer__guarantee_value'),
            pnbp_fees=F('consumer__pnbp_fee'),
            service_cost=F('consumer__service_fee'),
            notaryId=F('notary__firm_name'),
            tgl_pay=F('payment_date'),
        ).annotate(
            no_invoice=Subquery(latest_invoice_subquery.values('no_invoice')[:1])
        )
    )

    allowed_statuses = {'draft', 'check', 'clear', 'paid', 'done', 'cleared'}
    final_data = []
    seen_consumer_ids = set()

    for doc_item in docs_data:
        status = determine_status(doc_item)
        if status.lower() in allowed_statuses:
            cid = doc_item.get('consumer_id')
            if cid and cid not in seen_consumer_ids:
                seen_consumer_ids.add(cid)
                final_data.append(doc_item)

    return render(
        request,
        'Finance/page-finance-invoice-oc.html',
        {
            'consumers': final_data,
            'branches': branches,
            'deed_date': deed_date_str,
        }
    )

class GenerateInvoiceViewOC(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, format=None):
        if not user_has_permission(request.user, 'read-invoice-oc'):
            return Response({'detail': 'Forbidden'}, status=403)

        no_invoice   = request.data.get('no_invoice') or request.data.get('invoice_number')
        branch_raw   = (request.data.get('branch') or request.data.get('branch_code') or "").strip()
        date_from    = request.data.get('date_from')
        date_to      = request.data.get('date_to')
        consumer_ids = request.data.get('consumer_ids', [])
        
        FEE_NORMAL = common_code_decimal(SERVICE_FEE_KEY, 70000)
        FEE_TIER2  = common_code_decimal(SERVICE_FEE_TIER2_KEY, 60000)
        
        def _month_bounds(d: date):
            start = d.replace(day=1)
            last_day = monthrange(d.year, d.month)[1]
            end = d.replace(day=last_day)
            return start, end

        month_counters = {}
        
        def _ensure_month_counter(d: date):
            """
            Pastikan counter bulan (year, month) terisi dengan jumlah existing
            invoice pada bulan tsb. Hitung dari tb_invoice.tgl_pay; jika tidak
            tersedia, fallback ke legal_documents.certificate_date.
            """
            key = (d.year, d.month)
            if key in month_counters:
                return key

            start_m, end_m = _month_bounds(d)
            try:
                cnt = tb_invoice_oc.objects.filter(
                    tgl_pay__date__gte=start_m, tgl_pay__date__lte=end_m
                ).count()
            except Exception:
                cnt = 0

            # fallback jika belum ada data di tb_invoice
            if cnt == 0:
                try:
                    cnt = LegalDocumentsOC.objects.filter(
                        certificate_date__date__gte=start_m,
                        certificate_date__date__lte=end_m
                    ).count()
                except Exception:
                    cnt = 0

            month_counters[key] = int(cnt)
            return key

        # --- hasil per cabang + total (tetap) ---
        per_branch_results = []
        total_matched = total_created = total_skipped = total_updated_rows = total_skipped_missing_payment = 0

        if not no_invoice:
            return Response({"detail": "no invoice wajib diisi."}, status=400)
        try:
            no_invoice = no_invoice.strip()
        except ValueError:
            return Response({"detail": "no invoice harus numerik."}, status=400)

        start_dt = end_dt = None
        if date_from:
            try:
                d = datetime.strptime(date_from, "%Y-%m-%d").date()
                start_dt = make_aware(datetime.combine(d, datetime.min.time()))
            except ValueError:
                return Response({"detail": "Format date_from tidak valid. Gunakan YYYY-MM-DD."}, status=400)
        if date_to:
            try:
                d = datetime.strptime(date_to, "%Y-%m-%d").date()
                end_dt = make_aware(datetime.combine(d, datetime.max.time()))
            except ValueError:
                return Response({"detail": "Format date_to tidak valid. Gunakan YYYY-MM-DD."}, status=400)

        # --- tentukan mode semua cabang atau satu cabang ---
        is_all = (branch_raw == "" or branch_raw.upper() in {"ALL", "*"})
        if _user_is_staff_cabang(request.user):
            user_branches = _user_branch_codes(request.user)
            if not user_branches:
                return Response({"detail": "Branch wajib diisi untuk user staff_cabang."}, status=403)
            if is_all:
                branch_raw = "ALL"
            elif branch_raw and str(branch_raw).strip() not in user_branches:
                return Response({"detail": "Forbidden branch scope."}, status=403)
        branches = []

        if is_all:
            if _user_is_staff_cabang(request.user):
                branches = list(Branch.objects.filter(branch_code__in=_user_branch_codes(request.user)).order_by('branch_code'))
            else:
                branches = list(Branch.objects.all().order_by('branch_code'))
        else:
            try:
                branch_obj = Branch.objects.get(
                    Q(branch_code=branch_raw) | Q(pk=branch_raw) | Q(branch_name=branch_raw)
                )
            except Branch.DoesNotExist:
                return Response({"detail": f"Branch '{branch_raw}' tidak ditemukan."}, status=404)
            except MultipleObjectsReturned:
                return Response({"detail": f"Branch '{branch_raw}' ambigu. Kirimkan branch_code."}, status=400)
            branches = [branch_obj]

        # --- consumer_ids (opsional) ---
        if consumer_ids:
            try:
                consumer_ids = [int(x) for x in consumer_ids]
            except Exception:
                return Response({"detail": "consumer_ids harus berupa array of integer."}, status=400)

        # --- helper normalisasi payload status ---
        def _status_payload(doc):
            data = model_to_dict(doc, fields=[
                'voucher_code',
                'registration_date',
                'certificate_number',
                'payment_date',
                'certificate_path',
                'certificate_date',
                'document_number',
                'document_date',
            ])
            if not data.get('certificate_number'):
                data['certificate_number'] = data.get('document_number')
            if not data.get('registration_date'):
                data['registration_date'] = data.get('document_date')
            for k in ('voucher_code','certificate_number','certificate_path'):
                if data.get(k) is None:
                    data[k] = ""
            return data

        allowed_statuses = {'draft','check','clear','paid','done','cleared','recheck','pending'}

        # --- counter service fee (global per bulan, konsisten meskipun multi cabang) ---
        today = now()
        start_month = today.replace(day=1).date()
        end_month = today.replace(day=monthrange(today.year, today.month)[1]).date()
        try:
            invoice_count_this_month = tb_invoice_oc.objects.filter(
                tgl_inv__range=(start_month, end_month)
            ).count()
        except Exception:
            invoice_count_this_month = LegalDocumentsOC.objects.filter(
                document_date__range=(start_month, end_month)
            ).count()
        global_counter = invoice_count_this_month  # akan bertambah setiap insert

        default_fee = 100000
        discounted_fee = 50000

        # --- hasil per cabang + total ---
        per_branch_results = []
        total_matched = total_created = total_skipped = total_updated_rows = total_skipped_missing_payment = 0

        # --- fungsi proses per cabang (atomic per cabang agar aman & terukur) ---
        def process_one_branch(branch_obj, nonlocal_counter):
            base_qs = (
                LegalDocumentsOC.objects
                .select_related('consumer', 'notary')
                .filter(
                    consumer__branch_id=branch_obj.branch_code,
                    consumer__invoice_st__in=[0, None, '0', False],
                    consumer__agreement_number__isnull=False,
                    document_number__isnull=False,
                    consumer__debtor_name__isnull=False,
                    notary__isnull=False,
                )
                .exclude(consumer__agreement_number__regex=r'^\s*$')
                .exclude(document_number__regex=r'^\s*$')
                .exclude(consumer__debtor_name__regex=r'^\s*$')
            )

            if start_dt:
                base_qs = base_qs.filter(payment_date__gte=start_dt)
            if end_dt:
                base_qs = base_qs.filter(payment_date__lte=end_dt)
            if consumer_ids:
                base_qs = base_qs.filter(consumer__id__in=consumer_ids)

            # Filter status
            docs = []
            for doc in base_qs.iterator(chunk_size=1000):
                st = determine_status(_status_payload(doc))
                st_norm = (st or '').strip().lower()
                if st_norm in allowed_statuses:
                    docs.append(doc)

            matched_count = len(docs)
            if matched_count == 0:
                return ({
                    "branch_code": branch_obj.branch_code,
                    "branch_name": branch_obj.branch_name,
                    "matched_count": 0,
                    "created_count": 0,
                    "skipped_existing": 0,
                    "invoice_st_updated": 0,
                    "skipped_missing_payment": 0,
                    "message": f"Tidak ada dokumen pada filter untuk {branch_obj.branch_code}."
                }, nonlocal_counter)

            # ==== VALIDASI DOKUMEN: wajib payment_date terisi untuk dibuat invoice ====
            valid_docs = [d for d in docs if (d.payment_date and str(d.payment_date).strip())]
            skipped_missing_payment = len(docs) - len(valid_docs)
            if not valid_docs:
                return ({
                    "branch_code": branch_obj.branch_code,
                    "branch_name": branch_obj.branch_name,
                    "matched_count": matched_count,
                    "created_count": 0,
                    "skipped_existing": 0,
                    "invoice_st_updated": 0,
                    "skipped_missing_payment": skipped_missing_payment,
                    "message": f"Tidak ada dokumen dengan payment_date terisi untuk {branch_obj.branch_code}."
                }, nonlocal_counter)

            now_dt = now()
            created = skipped = 0
            updated_rows = 0
            processed_consumer_ids = set()

            with transaction.atomic():
                for doc in valid_docs:
                    c = doc.consumer

                    # ======================
                    # Tentukan "tanggal dasar" untuk threshold bulanan:
                    # prioritas tgl_pay (= payment_date), jika None pakai certificate_date
                    # ======================
                    pay_dt = getattr(doc, 'payment_date', None)
                    cert_dt = getattr(doc, 'certificate_date', None)
                    base_dt = pay_dt.date() if hasattr(pay_dt, 'date') else (
                        cert_dt.date() if hasattr(cert_dt, 'date') else now_dt.date()
                    )

                    # Pastikan counter bulan untuk tanggal tsb tersedia
                    mkey = _ensure_month_counter(base_dt)

                    # Hitung biaya jasa: jika setelah penambahan melebihi 2000, gunakan tier2
                    month_counters[mkey] += 1
                    current_pos = month_counters[mkey]       # urutan ke-N di bulan itu
                    service_fee_val = FEE_TIER2 if current_pos > 2000 else FEE_NORMAL

                    # Nilai yang disimpan ke row invoice (boleh override jika doc.service_fee ada)
                    svc = getattr(doc, "service_fee", None)
                    service_cost_val = Decimal(svc) if svc not in (None, '') else service_fee_val

                    # Siapkan tgl_pay untuk tb_invoice
                    pay_date_only = pay_dt.date() if hasattr(pay_dt, "date") else None

                    defaults = {
                        "branch_id": branch_obj.branch_code,
                        "no_invoice": no_invoice,
                        "customer_id": getattr(c, "id", 0) or 0,
                        "path_invoice": "",
                        "log_accept": "",
                        "log_pay": "",
                        "service_cost": service_cost_val,
                        "pnbp_fees": getattr(doc, "pnbp_fee", None),
                        "kd_voucher": getattr(doc, "voucher_code", "") or "",
                        "tgl_inv": now_dt.date(),
                        "tgl_pay": pay_date_only,
                        "agreement_number": getattr(c, "agreement_number", "") or "",
                        "bank_name": getattr(c, "bank_name", "") or "",
                        "created_at": now_dt,
                        "updated_at": now_dt,
                    }
                    agreement_number = defaults["agreement_number"]

                    try:
                        obj, was_created = tb_invoice_oc.objects.get_or_create(
                            no_invoice=no_invoice,
                            agreement_number=agreement_number,
                            defaults=defaults
                        )
                    except IntegrityError:
                        try:
                            obj, was_created = tb_invoice_oc.objects.get_or_create(
                                no_invoice=no_invoice,
                                defaults=defaults
                            )
                        except MultipleObjectsReturned:
                            obj = (
                                tb_invoice_oc.objects
                                .filter(no_invoice=no_invoice, agreement_number=agreement_number)
                                .order_by('id')
                                .first()
                            )
                            was_created = False

                    # Update tgl_pay jika sebelumnya kosong
                    if (not was_created) and (getattr(obj, "tgl_pay", None) in (None, "")) and pay_date_only:
                        obj.tgl_pay = pay_date_only
                        obj.service_cost = service_cost_val  # ikut sesuaikan jika mau
                        obj.updated_at = now_dt
                        obj.save(update_fields=["tgl_pay", "service_cost", "updated_at"])

                    created += 1 if was_created else 0
                    skipped += 0 if was_created else 1

                    if c and c.id:
                        processed_consumer_ids.add(c.id)

                if processed_consumer_ids:
                    ConsumerModel = LegalDocumentsOC._meta.get_field('consumer').remote_field.model
                    update_filter = (
                        Q(id__in=processed_consumer_ids) &
                        (Q(invoice_st=0) | Q(invoice_st__isnull=True) | Q(invoice_st='0') | Q(invoice_st=False))
                    )
                    updated_rows = ConsumerModel.objects.filter(update_filter).update(
                        invoice_st=1, updated_at=now_dt
                    )

            result = {
                "branch_code": branch_obj.branch_code,
                "branch_name": branch_obj.branch_name,
                "matched_count": matched_count,
                "created_count": created,
                "skipped_existing": skipped,
                "invoice_st_updated": updated_rows,
                "skipped_missing_payment": skipped_missing_payment,
                "message": f"Generate invoice cabang {branch_obj.branch_code} selesai."
            }
            return (result, nonlocal_counter)

        # --- proses semua cabang (tetap) ---
        for b in branches:
            result, _ = process_one_branch(b, 0)   # nonlocal_counter tidak dipakai lagi
            per_branch_results.append(result)
            total_matched += result["matched_count"]
            total_created += result["created_count"]
            total_skipped += result["skipped_existing"]
            total_updated_rows += result["invoice_st_updated"]
            total_skipped_missing_payment += result["skipped_missing_payment"]

        # --- jika single cabang & tidak ada dokumen, kembalikan 404 seperti semula ---
        if not is_all and total_matched == 0:
            return Response({"detail": "Tidak ada dokumen pada filter tersebut."}, status=404)

        status_code = 201 if total_created > 0 else 200
        return Response({
            "mode": "ALL_BRANCHES" if is_all else "SINGLE_BRANCH",
            "no_invoice": no_invoice,
            "date_from": date_from,
            "date_to": date_to,
            "branch_total": len(branches),
            "summary": {
                "matched_total": total_matched,
                "created_total": total_created,
                "skipped_existing_total": total_skipped,
                "invoice_st_updated_total": total_updated_rows,
                "skipped_missing_payment_total": total_skipped_missing_payment,
            },
            "per_branch": per_branch_results,
        }, status=status_code)

class DownloadInvoiceViewOC(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, format=None):
        if not user_has_permission(request.user, 'read-invoice-oc'):
            return Response({'detail': 'Forbidden'}, status=403)
        # ===================== (REMOVED) Date filters =====================
        # start_date = request.GET.get('start_date')
        # end_date = request.GET.get('end_date')

        branch_code = (request.GET.get('branch_code') or '').strip()
        if _user_is_staff_cabang(request.user):
            user_branches = _user_branch_codes(request.user)
            if not user_branches:
                return Response({"detail": "Branch wajib diisi untuk user staff_cabang."}, status=403)
            branch_codes = user_branches
            branch_code = ""
        invoice_no  = (request.GET.get('invoice_no') or '').strip()
        logger.info("[DownloadInvoiceView] invoice_no=%s", invoice_no or '(ALL)')

        # logger.info("[DownloadInvoiceView][GET] start=%s end=%s branch=%s", start_date, end_date, branch_code or '(ALL)')

        # ===================== (REMOVED) Parse tanggal =====================
        # start_d = end_d = None
        # if start_date:
        #     try:
        #         start_d = datetime.strptime(start_date, "%Y-%m-%d").date()
        #     except ValueError:
        #         return Response({"detail": "Format start_date tidak valid. Gunakan YYYY-MM-DD."}, status=400)
        # if end_date:
        #     try:
        #         end_d = datetime.strptime(end_date, "%Y-%m-%d").date()
        #     except ValueError:
        #         return Response({"detail": "Format end_date tidak valid. Gunakan YYYY-MM-DD."}, status=400)

        # --- Query dasar
        docs = (
            LegalDocumentsOC.objects
            .select_related("consumer", "notary", "consumer__branch")
        )

        base_count = docs.count()
        logger.info("[DownloadInvoiceView] base_count=%s", base_count)

        # Filter cabang (opsional)
        if branch_code:
            docs = docs.filter(consumer__branch__branch_code__iexact=branch_code)
            logger.info("[DownloadInvoiceView] after_branch_filter_count=%s (branch=%s)", docs.count(), branch_code)

        # === Filter nomor invoice (exact / case-insensitive) ===
        if invoice_no:
            inv_qs = tb_invoice_oc.objects.filter(
                agreement_number=OuterRef('consumer__agreement_number'),
                no_invoice__iexact=invoice_no
            )
            docs = docs.annotate(has_inv=Exists(inv_qs)).filter(has_inv=True)
            logger.info("[DownloadInvoiceView] after_invoice_filter_count=%s (invoice_no=%s)", docs.count(), invoice_no)

        # ===================== (REMOVED) Filter berdasarkan tanggal =====================
        # if start_d or end_d:
        #     docs = docs.annotate(
        #         doc_date=Cast('payment_date', DateField())  # pakai payment_date
        #     )
        #     if start_d and end_d:
        #         docs = docs.filter(doc_date__range=(start_d, end_d))
        #     elif start_d:
        #         docs = docs.filter(doc_date__gte=start_d)
        #     else:
        #         docs = docs.filter(doc_date__lte=end_d)
        #
        #     after_date_count = docs.count()
        #     sample = list(docs.values_list('id', 'payment_date', 'created_at')[:5])
        #     logger.info("[DownloadInvoiceView] after_date_count=%s sample=%s", after_date_count, sample)

        # --- Hanya dokumen eligible untuk invoice
        docs = docs.filter(Q(consumer__invoice_st=True) | Q(consumer__invoice_st=1))

        # 1) certificate_path wajib terisi
        docs = docs.filter(certificate_path__isnull=False).exclude(certificate_path='')

        # 2) exclude certificate_number 'clear' atau 'pending'
        docs = docs.exclude(
            Q(certificate_number__iexact='clear') |
            Q(certificate_number__iexact='pending')
        )

        logger.info("[DownloadInvoiceView] after_all_filters_count=%s", docs.count())

        # --- Normalisasi status (tetap)
        canonical_statuses = {'draft': 'Draft', 'check': 'Check', 'clear': 'Clear', 'paid': 'Paid', 'done': 'Done'}
        valid_statuses_lower = {'draft', 'check', 'clear', 'paid', 'done', 'cleared', 'recheck', 'pending'}

        def normalize_status(raw):
            if not raw:
                return '_Other', False
            s = str(raw).strip().lower()
            is_valid = s in valid_statuses_lower
            if s in canonical_statuses:
                return canonical_statuses[s], is_valid
            if s == 'cleared':
                return 'Clear', True
            if s == 'recheck':
                return 'Check', True
            if s == 'pending':
                return 'Check', True
            return '_Other', is_valid

        status_counter = {'Draft': 0, 'Check': 0, 'Clear': 0, 'Paid': 0, 'Done': 0, '_Other': 0}
        valid_docs = []
        enriched_docs = []

        # ===================== (REMOVED) document_date_str untuk context =====================
        # if start_d and end_d:
        #     document_date_str = f"{start_d.isoformat()} s.d. {end_d.isoformat()}"
        # elif start_d:
        #     document_date_str = f">= {start_d.isoformat()}"
        # elif end_d:
        #     document_date_str = f"<= {end_d.isoformat()}"
        # else:
        #     document_date_str = "(all dates)"

        # --- Iterasi
        for doc in docs.iterator():
            c = getattr(doc, 'consumer', None)
            if not c:
                continue

            doc_item = {
                "voucher_code":       getattr(doc, "voucher_code", None),
                "registration_date":  getattr(doc, "registration_date", None),
                "certificate_number": getattr(doc, "certificate_number", None),
                "payment_date":       getattr(doc, "payment_date", None),
                "certificate_path":   getattr(doc, "certificate_path", None),
                "certificate_date":   getattr(doc, "certificate_date", None),
            }

            try:
                raw_status = determine_status(doc_item)
            except Exception as e:
                logger.exception("[DownloadInvoiceView] gagal hitung status doc_id=%s: %s",
                                 getattr(doc, 'id', None), e)
                raw_status = None

            status_for_counter, is_valid_status = normalize_status(raw_status)
            status_counter[status_for_counter] = status_counter.get(status_for_counter, 0) + 1

            invoice_st_ok = bool(getattr(c, "invoice_st", 0))

            if is_valid_status and invoice_st_ok:
                valid_docs.append(doc)

            # doc_date sebelumnya hasil annotate; sekarang fallback saja
            doc_date_value = getattr(doc, 'document_date', None) or getattr(doc, 'created_at', None)

            enriched_docs.append({
                "id": getattr(doc, "id", None),
                "agreement_number": getattr(doc, "agreement_number", None),
                "consumer": getattr(c, "name", None),
                "branch": getattr(getattr(c, "branch", None), "branch_code", None),
                "status_raw": raw_status,
                "status": status_for_counter,
                "invoice_st_ok": invoice_st_ok,
                "document_date": getattr(doc, "document_date", None),
                "created_at": getattr(doc, "created_at", None),
                "doc_date": doc_date_value,
                "voucher_code": doc_item["voucher_code"],
                "registration_date": doc_item["registration_date"],
                "certificate_number": doc_item["certificate_number"],
                "payment_date": doc_item["payment_date"],
                "certificate_path": doc_item["certificate_path"],
                "certificate_date": doc_item["certificate_date"],
            })

        context = {
            "enriched_docs": enriched_docs,
            # "deed_date": document_date_str,
        }

        logger.info("[DownloadInvoiceView] status_counter=%s", status_counter)
        logger.info("[DownloadInvoiceView] eligible_count=%s of filtered_count=%s",
                    len(valid_docs), docs.count())

        if not valid_docs:
            reason = (
                f"after_branch/invoice={docs.count()}, "
                f"status_counts={status_counter}, "
                f"note='memerlukan consumer.invoice_st=1 & status in {sorted(list(valid_statuses_lower))}'"
            )
            # logger.warning("[DownloadInvoiceView] NO DATA FOUND (%s) start=%s end=%s branch=%s", reason, start_date, end_date, branch_code or 'ALL')
            return Response({"detail": "Tidak ada data invoice pada filter tersebut.", "context": context}, status=404)

        # tanggal tidak dipakai lagi; tetap panggil dengan None agar kompatibel
        return self._generate_excel(valid_docs, start_date=None, end_date=None, branch_code=branch_code or None)

    def _generate_excel(self, legal_documents, start_date=None, end_date=None, branch_code=None):
        headers = ["No","KODE CABANG","NAMA CABANG","no_akta","tgl_akta",
                   "no_perjanjian","nama_debitur","Kode Voucher","NO. SERTIFIKAT",
                   "TGL. SERTIFIKAT","NILAI PENJAMINAN","Nominal PNBP","Jasa",
                   "NAMA NOTARIS", "Tanggal Pembayaran", "No. Invoice"]

        def sanitize_sheet_name(name: str) -> str:
            name = name or "UNKNOWN"
            name = re.sub(r'[\[\]\:\*\?\/\\]', '-', name)
            return name[:31]

        def as_int(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return 0

        def footer_totals(ws, total_pnbp: int, nominal_counter: dict):
            ws.append([])
            ws.append([''] * 10 + ['TOTAL', f'Rp{int(total_pnbp):,}'.replace(",", ".")] + [''])
            ws.append([]); ws.append([])
            ws.append(['', '', 'NOMINAL', 'JUMLAH', 'TOTAL'])
            nominal_order = ['Rp50.000','Rp100.000','Rp200.000','Rp450.000','Rp850.000','Rp1.800.000']
            total_rows = 0
            total_amount = 0
            for nominal in nominal_order:
                count = nominal_counter.get(nominal, 0)
                nominal_int = int(nominal.replace('Rp','').replace('.',''))
                total = nominal_int * count
                total_rows += count
                total_amount += total
                ws.append(['', '', nominal, count, f'Rp{total:,.0f}'.replace(",", ".")])
            ws.append(['', '', 'TOTAL', total_rows, f'Rp{total_amount:,.0f}'.replace(",", ".")])

            for col in ws.columns:
                for cell in col:
                    if isinstance(cell.value, (int, float, str)):
                        cell.alignment = Alignment(horizontal='center', vertical='center')

        # === Workbook ===
        wb = openpyxl.Workbook()

        # ===================== (REMOVED) Nama file berdasarkan tanggal =====================
        # if start_date and end_date:
        #     try:
        #         start_label = datetime.strptime(start_date, "%Y-%m-%d").strftime("%d-%m-%Y")
        #         end_label = datetime.strptime(end_date, "%Y-%m-%d").strftime("%d-%m-%Y")
        #         filename = f"Invoice_{branch_code or 'ALL'}_{start_label}_To_{end_label}.xlsx"
        #     except Exception:
        #         filename = f"Invoice_{branch_code or 'ALL'}.xlsx"
        # else:
        #     filename = f"Invoice_{branch_code or 'ALL'}.xlsx"

        # Hanya pakai branch_code (atau ALL)
        filename = f"Invoice_{branch_code or 'ALL'}.xlsx"

        # === Jika cabang dipilih → single sheet ===
        if branch_code:
            ws = wb.active
            ws.title = "Invoice"
            ws.append(headers)

            total_pnbp = 0
            nominal_counter = {}
            for idx, doc in enumerate(legal_documents, start=1):
                c = doc.consumer
                tgl_akta = (doc.document_date or doc.created_at)
                invoice = (
                    tb_invoice_oc.objects
                    .filter(agreement_number=c.agreement_number)
                    .order_by('-tgl_inv', '-created_at')
                    .first()
                )
                inv_pnbp = as_int(getattr(invoice, "pnbp_fees", 0)) if invoice else 0
                cons_pnbp = as_int(getattr(c, "pnbp_fee", 0))
                pnbp_val = inv_pnbp if inv_pnbp > 0 else cons_pnbp

                inv_service = as_int(getattr(invoice, "service_cost", 0)) if invoice else 0
                cons_service = as_int(getattr(c, "service_fee", 0))
                service_val = inv_service if inv_service > 0 else cons_service

                total_pnbp += pnbp_val
                nominal = f"Rp{pnbp_val:,}".replace(",", ".")
                nominal_counter[nominal] = nominal_counter.get(nominal, 0) + 1

                ws.append([
                    idx,
                    c.branch.branch_code if c and c.branch else '',
                    c.branch.branch_name if c and c.branch else '',
                    doc.document_number or '',
                    tgl_akta.strftime('%Y-%m-%d') if tgl_akta else '',
                    getattr(c, "agreement_number", "") or '',
                    getattr(c, "debtor_name", "") or '',
                    doc.voucher_code or '',
                    doc.certificate_number or '',
                    doc.certificate_date.strftime('%Y-%m-%d') if doc.certificate_date else '',
                    format_rupiah(getattr(c, "guarantee_value", 0)),
                    format_rupiah(pnbp_val),
                    format_rupiah(getattr(c, "service_fee", 0)),
                    doc.notary.full_name if doc.notary else '',
                    doc.payment_date or '',
                    invoice.no_invoice if invoice and invoice.no_invoice else '',
                ])

            footer_totals(ws, total_pnbp, nominal_counter)

        else:
            # === Tanpa cabang: REKAP + sheet per-cabang ===
            per_branch_rows = defaultdict(list)
            branch_names = {}
            branch_totals = defaultdict(int)
            branch_nominals = defaultdict(lambda: defaultdict(int))

            grand_nominal_counter = defaultdict(int)
            grand_total_pnbp = 0

            for doc in legal_documents:
                c = doc.consumer
                b = getattr(c, 'branch', None)
                bcode = getattr(b, 'branch_code', None) or 'UNKNOWN'
                bname = getattr(b, 'branch_name', '') if b else ''
                branch_names[bcode] = bname

                tgl_akta = (doc.document_date or doc.created_at)
                invoice = (
                    tb_invoice_oc.objects
                    .filter(agreement_number=c.agreement_number)
                    .order_by('-tgl_inv', '-created_at')
                    .first()
                )
                inv_pnbp = as_int(getattr(invoice, "pnbp_fees", 0)) if invoice else 0
                cons_pnbp = as_int(getattr(c, "pnbp_fee", 0))
                pnbp_val = inv_pnbp if inv_pnbp > 0 else cons_pnbp

                inv_service = as_int(getattr(invoice, "service_cost", 0)) if invoice else 0
                cons_service = as_int(getattr(c, "service_fee", 0))
                service_val = inv_service if inv_service > 0 else cons_service

                nominal_str = f"Rp{pnbp_val:,}".replace(",", ".")
                row_wo_no = [
                    bcode,
                    bname,
                    doc.document_number or '',
                    tgl_akta.strftime('%Y-%m-%d') if tgl_akta else '',
                    getattr(c, "agreement_number", "") or '',
                    getattr(c, "debtor_name", "") or '',
                    doc.voucher_code or '',
                    doc.certificate_number or '',
                    doc.certificate_date.strftime('%Y-%m-%d') if doc.certificate_date else '',
                    format_rupiah(getattr(c, "guarantee_value", 0)),
                    format_rupiah(pnbp_val),
                    format_rupiah(getattr(c, "service_fee", 0)),
                    doc.notary.full_name if doc.notary else '',
                    doc.payment_date or '',
                    invoice.no_invoice if invoice and invoice.no_invoice else '',
                ]
                per_branch_rows[bcode].append((row_wo_no, pnbp_val, nominal_str))

                branch_totals[bcode] += pnbp_val
                branch_nominals[bcode][nominal_str] += 1
                grand_total_pnbp += pnbp_val
                grand_nominal_counter[nominal_str] += 1

            wb.remove(wb.active)
            ws_rekap = wb.create_sheet(title="GABUNGAN", index=0)
            ws_rekap.append(headers)

            global_idx = 1
            for bcode in sorted(per_branch_rows.keys()):
                for row_wo_no, _, _ in per_branch_rows[bcode]:
                    ws_rekap.append([global_idx] + row_wo_no)
                    global_idx += 1

            footer_totals(ws_rekap, grand_total_pnbp, grand_nominal_counter)

            for bcode in sorted(per_branch_rows.keys()):
                bname = branch_names.get(bcode, '')
                sheet_name = sanitize_sheet_name(f"{bcode}-{bname}" if bname else bcode)
                ws_branch = wb.create_sheet(title=sheet_name)
                ws_branch.append(headers)

                idx = 1
                nominal_counter = defaultdict(int)
                total_pnbp = 0
                for row_wo_no, pnbp_val, nominal_str in per_branch_rows[bcode]:
                    ws_branch.append([idx] + row_wo_no)
                    idx += 1
                    total_pnbp += pnbp_val
                    nominal_counter[nominal_str] += 1

                footer_totals(ws_branch, total_pnbp, nominal_counter)

        file_stream = io.BytesIO()
        wb.save(file_stream)
        file_stream.seek(0)

        response = FileResponse(
            file_stream,
            as_attachment=True,
            filename=filename,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        logger.info("[DownloadInvoiceView] returning file %s", filename)
        return response

class ConsumerInvoiceDataViewOC(APIView):
    permission_classes = [IsAuthenticated]

    def _param(self, request, key, default=None):
        # Prioritaskan body (POST JSON), fallback ke query string
        return (request.data.get(key)
                if hasattr(request, 'data') and isinstance(request.data, dict)
                else None) or request.query_params.get(key, default)

    def _to_int(self, v, default):
        try:
            return int(v)
        except Exception:
            return default

    def _handle(self, request):
        if not user_has_permission(request.user, 'read-invoice-oc'):
            return Response({'detail': 'Forbidden'}, status=403)
        # ---- DataTables core params ----
        draw         = self._to_int(self._param(request, 'draw', '1'), 1)
        start        = self._to_int(self._param(request, 'start', '0'), 0)
        length       = self._to_int(self._param(request, 'length', '10'), 10)
        search_value = (self._param(request, 'search[value]', '') or '').strip()

        # ---- Custom filters ----
        start_date  = self._param(request, 'start_date')
        end_date    = self._param(request, 'end_date')
        branch_code = self._param(request, 'branch_code')
        if _user_is_staff_cabang(request.user):
            user_branches = _user_branch_codes(request.user)
            if not user_branches:
                return Response({"detail": "Branch wajib diisi untuk user staff_cabang."}, status=403)
            branch_codes = user_branches
            branch_code = ""
        invoice_st  = self._param(request, 'invoice_st')  # '1' = sudah generate
        invoice_no = (self._param(request, 'invoice_no') or '').strip()

        # ---- Base queryset ----
        base_qs = ConsumerOC.objects.select_related('branch')

        latest_doc_qs = LegalDocumentsOC.objects.filter(consumer=OuterRef('pk')).order_by('-id')
        latest_inv_qs = tb_invoice_oc.objects.filter(agreement_number=OuterRef('agreement_number')).order_by('-id')

        base_qs = base_qs.annotate(
            document_number=Subquery(latest_doc_qs.values('document_number')[:1]),
            document_date=Subquery(latest_doc_qs.values('document_date')[:1]),
            voucher_code=Subquery(latest_doc_qs.values('voucher_code')[:1]),
            certificate_number=Subquery(latest_doc_qs.values('certificate_number')[:1]),
            certificate_date=Subquery(latest_doc_qs.values('certificate_date')[:1]),
            registration_date=Subquery(latest_doc_qs.values('registration_date')[:1]),
            certificate_path=Subquery(latest_doc_qs.values('certificate_path')[:1]),
            payment_date=Subquery(latest_doc_qs.values('payment_date')[:1]),
            notary_firm=Subquery(latest_doc_qs.values('notary__firm_name')[:1]),
            no_invoice=Subquery(latest_inv_qs.values('no_invoice')[:1]),
            tgl_pay=Subquery(latest_doc_qs.values('payment_date')[:1]),
        )

        if branch_code:
            base_qs = base_qs.filter(branch__branch_code=branch_code)
        elif locals().get("branch_codes"):
            base_qs = base_qs.filter(branch__branch_code__in=branch_codes)

        if invoice_st == '1':
            base_qs = base_qs.filter(invoice_st=1)
        else:
            base_qs = base_qs.filter(Q(invoice_st=0) | Q(invoice_st__isnull=True))
            base_qs = base_qs.filter(certificate_path__isnull=False).exclude(certificate_path='')
            base_qs = base_qs.exclude(Q(certificate_number__iexact='clear') | Q(certificate_number__iexact='pending'))

        if not start_date and not end_date and not invoice_no:
            today_str = date.today().strftime('%Y-%m-%d')
            start_date = end_date = today_str

        if start_date:
            base_qs = base_qs.filter(payment_date__gte=start_date)
        if end_date:
            base_qs = base_qs.filter(payment_date__lte=end_date)
            
        if invoice_no:
            invoice_list = [s.strip() for s in re.split(r'[;,]', invoice_no) if s.strip()]
            inv_qs = tb_invoice_oc.objects.filter(agreement_number=OuterRef('agreement_number'))
            if len(invoice_list) == 1:
                inv_qs = inv_qs.filter(no_invoice__iexact=invoice_list[0])
            else:
                inv_qs = inv_qs.filter(no_invoice__in=invoice_list)
            base_qs = base_qs.annotate(has_inv=Exists(inv_qs)).filter(has_inv=True)

        records_total = base_qs.count()

        if search_value:
            base_qs = base_qs.filter(
                Q(agreement_number__icontains=search_value) |
                Q(debtor_name__icontains=search_value) |
                Q(document_number__icontains=search_value)
            )

        records_filtered = base_qs.count()

        order_map = {
            0: None,
            1: 'branch__branch_code',
            2: 'branch__branch_name',
            3: 'document_number',
            4: 'document_date',
            5: 'agreement_number',
            6: 'debtor_name',
            7: 'voucher_code',
            8: 'certificate_number',
            9: 'certificate_date',
            10: 'guarantee_value',
            11: 'pnbp_fee',
            12: 'service_fee',
            13: 'notary_firm',
            14: 'tgl_pay',
            15: 'no_invoice',
        }

        order_col_index = self._to_int(self._param(request, 'order[0][column]', '4'), 4)
        order_dir       = self._param(request, 'order[0][dir]', 'desc')

        order_field = order_map.get(order_col_index) or 'document_date'
        if order_dir == 'desc':
            order_field = f'-{order_field}'
        base_qs = base_qs.order_by(order_field, '-id')

        end_idx = start + length
        page_qs = base_qs[start:end_idx]

        data = []
        for consumer in page_qs:
            item_dict = {
                'voucher_code': consumer.voucher_code,
                'registration_date': consumer.registration_date,
                'certificate_number': consumer.certificate_number,
                'payment_date': consumer.payment_date,
                'certificate_path': consumer.certificate_path,
                'certificate_date': consumer.certificate_date,
            }
            data.append({
                'id': consumer.id,
                'branch_code': consumer.branch.branch_code if consumer.branch else '',
                'branch_name': consumer.branch.branch_name if consumer.branch else '',
                'document_number': consumer.document_number or '',
                'document_date': consumer.document_date.strftime('%Y-%m-%d') if consumer.document_date else '',
                'agreement_number': consumer.agreement_number or '',
                'debtor_name': consumer.debtor_name or '',
                'voucher_code': consumer.voucher_code or '',
                'certificate_number': consumer.certificate_number or '',
                'certificate_date': consumer.certificate_date.strftime('%Y-%m-%d') if consumer.certificate_date else '',
                'guarantee_value': consumer.guarantee_value or 0,
                'pnbp_fees': consumer.pnbp_fee or 0,
                'service_cost': consumer.service_fee or 0,
                'notary_id': consumer.notary_firm or '',
                'tgl_pay': consumer.tgl_pay.strftime('%Y-%m-%d') if consumer.tgl_pay else '',
                'no_invoice': consumer.no_invoice or '',
                'status': determine_status(item_dict),
            })

        return Response({
            'draw': draw,
            'recordsTotal': records_total,
            'recordsFiltered': records_filtered,
            'data': data,
        })

    def get(self, request):
        return self._handle(request)

    def post(self, request):
        return self._handle(request)

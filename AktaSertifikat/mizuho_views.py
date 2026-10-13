"""Daftar Akta & Sertifikat — fitur Mizuho (requirement §9): bukti bayar PNBP & laporan."""
import os
import re

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from AdminMenu.auth_helpers import permission_required
from invoice.fees import flat_service_fee
from . import mizuho_reports
from .models import LegalDocuments
from .views import (
    _filter_by_branch,
    _get_branch_code_from_consumer,
    _user_can_access_branch,
    _write_branch_codes_or_forbidden,
)

BUKTI_BAYAR_DIR = "bukti-bayar"
BUKTI_BAYAR_MAX_BYTES = 5 * 1024 * 1024
BRANCH_FIELDS = ["consumer__branch__branch_code", "consumer__branch_id", "consumer__branch__pk"]


def validate_bukti_bayar(uploaded):
    """Return pesan error, atau None bila file PDF valid."""
    if not uploaded:
        return "File belum dipilih."
    if not uploaded.name.lower().endswith(".pdf"):
        return "Bukti bayar harus berformat PDF."
    if uploaded.size > BUKTI_BAYAR_MAX_BYTES:
        return "Ukuran file maksimal 5 MB."
    head = uploaded.read(5)
    uploaded.seek(0)
    if head != b"%PDF-":
        return "File bukan PDF yang valid."
    return None


@login_required(login_url="")
@permission_required("update-sertifikat")
@require_POST
def upload_bukti_bayar(request, id):
    """Upload bukti setor PNBP (PDF) ke media/bukti-bayar/; path disimpan di legal_documents.voucher_input."""
    branch_codes = _write_branch_codes_or_forbidden(request.user)
    if branch_codes is None:
        return JsonResponse({"status": "error", "message": "Forbidden"}, status=403)
    doc_qs = LegalDocuments.objects.select_related("consumer").filter(pk=id)
    if branch_codes:
        doc_qs = doc_qs.filter(
            Q(consumer__branch__branch_code__in=branch_codes) | Q(consumer__branch_id__in=branch_codes)
        )
    doc = doc_qs.first()
    if not doc:
        return JsonResponse({"status": "error", "message": "Dokumen tidak ditemukan."}, status=404)

    uploaded = request.FILES.get("bukti_bayar")
    error = validate_bukti_bayar(uploaded)
    if error:
        return JsonResponse({"status": "error", "message": error}, status=400)

    agreement = re.sub(r"[^0-9A-Za-z-]", "_", doc.consumer.agreement_number or str(doc.id))
    filename = f"{agreement}_{timezone.now().strftime('%Y%m%d%H%M%S')}.pdf"
    rel_path = default_storage.save(f"{BUKTI_BAYAR_DIR}/{filename}", uploaded)

    doc.voucher_input = rel_path
    doc.save(update_fields=["voucher_input", "updated_at"])
    return JsonResponse({"status": "success", "path": rel_path})


@login_required(login_url="")
@permission_required("read-akser")
def download_bukti_bayar(request, id):
    document = get_object_or_404(LegalDocuments, pk=id)
    consumer = getattr(document, "consumer", None)
    if not _user_can_access_branch(request.user, _get_branch_code_from_consumer(consumer)):
        return HttpResponseForbidden("Forbidden")

    if not document.voucher_input:
        raise Http404("Bukti bayar belum diupload")
    media_root = os.path.normpath(settings.MEDIA_ROOT)
    full_path = os.path.normpath(os.path.join(media_root, document.voucher_input))
    if not full_path.startswith(media_root + os.sep) or not os.path.exists(full_path):
        raise Http404("File bukti bayar tidak ditemukan di server")

    return FileResponse(
        open(full_path, "rb"),
        as_attachment=True,
        filename=os.path.basename(full_path),
        content_type="application/pdf",
    )


def _report_docs(request):
    """LegalDocuments reguler sesuai filter Tanggal Akta & scope cabang. Return (qs, start, end, error)."""
    start_date = parse_date(request.GET.get("tanggal_mulai") or "")
    end_date = parse_date(request.GET.get("tanggal_selesai") or "")
    if not start_date or not end_date:
        return None, None, None, HttpResponse("Tanggal mulai dan tanggal selesai wajib diisi.", status=400)
    if start_date > end_date:
        return None, None, None, HttpResponse("Tanggal mulai tidak boleh lebih besar dari tanggal selesai.", status=400)
    if (end_date - start_date).days > 31:
        return None, None, None, HttpResponse("Rentang tanggal maksimal 31 hari.", status=400)

    docs = LegalDocuments.objects.select_related("consumer", "consumer__branch", "notary").filter(
        document_date__range=(start_date, end_date)
    )
    docs = _filter_by_branch(docs, request.user, BRANCH_FIELDS)
    return docs.order_by("document_date", "document_number", "id"), start_date, end_date, None


def _xlsx_response(wb, filename):
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


@login_required(login_url="")
@permission_required("read-akser")
def export_laporan_mizuho(request):
    docs, start_date, end_date, error = _report_docs(request)
    if error:
        return error
    return _xlsx_response(
        mizuho_reports.build_laporan_workbook(docs, default_fee=flat_service_fee()),
        f"laporan_mizuho_{start_date:%Y%m%d}_{end_date:%Y%m%d}.xlsx",
    )


@login_required(login_url="")
@permission_required("read-akser")
def export_feedback_mizuho(request):
    docs, start_date, end_date, error = _report_docs(request)
    if error:
        return error
    return _xlsx_response(
        mizuho_reports.build_feedback_workbook(docs),
        f"feedback_order_mizuho_{start_date:%Y%m%d}_{end_date:%Y%m%d}.xlsx",
    )

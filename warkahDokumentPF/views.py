from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.renderers import TemplateHTMLRenderer
from rest_framework import viewsets, status
from rest_framework.parsers import MultiPartParser, FormParser
from django.utils.dateparse import parse_date
from django.db.models import Q
from AdminNotaries.models import Notary
from django.core.files.storage import default_storage
from import_document_pf.models import ConsumerDocumentPF
import os
from assignaktaPF.models import LegalDocumentPF
from .serializers import LegalDocumentSerializer
from rest_framework.permissions import IsAuthenticated
from django.contrib.auth.mixins import LoginRequiredMixin
import io
from django.http import HttpResponse
import openpyxl
from datetime import datetime
import zipfile
import pandas as pd
from change_fiduciary.models import change_of_fiduciary
from AdminMenu.auth_helpers import class_permission_required, user_has_permission, resolve_permission_alias, user_has_role_names, get_user_role_names, permission_required
from common.const.role_map import has_role, ROLE_KEYS
from AdminUser.branch_access import get_primary_user_branch_code, get_user_branch_codes, user_is_branch_restricted
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.core.paginator import Paginator

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

class WarkahViewPF(LoginRequiredMixin, APIView):
    login_url = ''
    permission_classes = [IsAuthenticated]
    renderer_classes = [TemplateHTMLRenderer]
    template_name = 'warkah/page-warkah-dokument-pf.html'
    @class_permission_required("read-warkah-pf")
    def get(self, request):
        context = {
            "range_options": [(i, i + 49) for i in range(1, 1501, 50)]
        }
        return Response(context)

class WarkahDocumentDataAPIViewPF(APIView):
    """
    API view for handling document data requests with DataTables server-side processing.
    
    This view supports:
    1. Server-side processing for DataTables (filtering, sorting, pagination)
    2. Excel file downloads with customizable data ranges
    3. ZIP file downloads of document images with customizable data ranges
    4. Regular API responses for backward compatibility
    
    The response format depends on the request type:
    - For DataTables: Returns {draw, recordsTotal, recordsFiltered, data}
    - For downloads: Returns the appropriate file response
    - For regular API: Returns serialized document data
    """
    permission_classes = [IsAuthenticated]

    @class_permission_required("read-warkah-pf")
    def get(self, request):
        # Get DataTables server-side processing parameters
        try:
            draw = int(request.GET.get('draw', '1'))
        except (ValueError, TypeError):
            draw = 1
        
        try:
            start = int(request.GET.get('start', 0))
        except (ValueError, TypeError):
            start = 0
            
        try:
            length = int(request.GET.get('length', 10))
        except (ValueError, TypeError):
            length = 10
            
        search_value = request.GET.get('search[value]', '')
        
        # Get column ordering information
        try:
            order_column_index = int(request.GET.get('order[0][column]', 0))
        except (ValueError, TypeError):
            order_column_index = 0
            
        order_dir = request.GET.get('order[0][dir]', 'asc')
        if order_dir not in ['asc', 'desc']:
            order_dir = 'asc'
            
        # Get the column name for ordering
        try:
            order_column = request.GET.get(f'columns[{order_column_index}][data]', 'document_date')
            # If the column name is empty or None, use a default
            if not order_column:
                order_column = 'document_date'
        except Exception:
            # Fallback to default ordering if there's any error
            order_column = 'document_date'
        
        # Get custom filter parameters
        from_date = request.GET.get('from_date')
        firm_name = request.GET.get('firm_name')
        agreement_number = request.GET.get('agreement_number')
        document_number = request.GET.get('document_number')
        document_date = request.GET.get('document_date')
        
        # For backward compatibility with existing download functionality
        try:
            limit = int(request.GET.get('limit', length))
            if limit < 1:
                limit = length  # Use the DataTables length as fallback
        except (ValueError, TypeError):
            limit = length  # Use the DataTables length as fallback
            
        try:
            offset = int(request.GET.get('offset', start))
            if offset < 0:
                offset = start  # Use the DataTables start as fallback
        except (ValueError, TypeError):
            offset = start  # Use the DataTables start as fallback
            
        download_type = request.GET.get('download')  # 'excel' atau 'image'

        # Start with all documents
        documents = (LegalDocumentPF.objects.select_related('notary', 'power_user').exclude(Q(consumer__document_status=0) | Q(consumer__document_status='0')))
        
        try:
            if not (request.user.is_superuser or request.user.username.lower() == 'admin'):
                # pastikan cek role tidak case-sensitive
                if user_has_role_names(request.user, ['notaris', 'Notaris', 'NOTARIS']):
                    # 1) Jalur utama: kalau Notary punya FK user → ini saja sudah cukup.
                    scoped = documents.filter(notary__user=request.user)

                    # 2) Fallback kalau kosong (misal data belum di-link ke user)
                    if not scoped.exists():
                        nm_full  = (getattr(request.user, 'get_full_name', lambda: '')() or '').strip()
                        nm_first = (getattr(request.user, 'first_name', None) or '').strip()
                        nm_user  = (getattr(request.user, 'username', None) or '').strip()
                        email    = (getattr(request.user, 'email', None) or '').strip()

                        name_parts = [s for s in [nm_full, nm_first, nm_user] if s]

                        q = Q()
                        if email:
                            q |= Q(notary__email__iexact=email)

                        for s in name_parts:
                            q |= (
                                Q(notary__firm_name__icontains=s) |
                                Q(notary__full_name__icontains=s) |
                                Q(notary__full_title__icontains=s) |
                                Q(notary__short_title__icontains=s)
                            )

                        scoped = documents.filter(q)

                        # Jika tetap tidak ketemu apa pun, jangan bocor data → kosongkan
                        if not scoped.exists():
                            documents = documents.none()
                        else:
                            documents = scoped
                    else:
                        documents = scoped
        except Exception as e:
            print(f"[WARN] notary self-scope failed (safe-ignored): {e}")
        
        if _user_is_staff_cabang(request.user):
            user_branches = _user_branch_codes(request.user)
            if not user_branches:
                documents = documents.none()
            else:
                documents = documents.filter(consumer__branch__branch_code__in=user_branches)

        # Store total count before filtering
        records_total = documents.count()

        # Apply filters
        if from_date:
            try:
                date_obj = parse_date(from_date)
                if date_obj:
                    documents = documents.filter(document_date=date_obj)
                else:
                    # If parse_date returns None, try a more flexible approach
                    try:
                        # Try to parse with different formats
                        from datetime import datetime
                        for fmt in ['%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%Y/%m/%d']:
                            try:
                                date_obj = datetime.strptime(from_date, fmt).date()
                                documents = documents.filter(document_date=date_obj)
                                break
                            except ValueError:
                                continue
                    except Exception as e:
                        print(f"Failed to parse date with alternative formats: {e}")
            except Exception as e:
                print(f"Invalid from_date '{from_date}': {e}")

        if firm_name:
            documents = documents.filter(notary__firm_name__icontains=firm_name)

        if agreement_number:
            documents = documents.filter(consumer__agreement_number__icontains=agreement_number)

        if document_number:
            documents = documents.filter(document_number__icontains=document_number)
            
        # Apply global search if provided
        if search_value:
            documents = documents.filter(
                Q(consumer__branch__branch_code__icontains=search_value) |
                Q(consumer__debtor_name__icontains=search_value) |
                Q(consumer__agreement_number__icontains=search_value) |
                Q(document_number__icontains=search_value)
            )
        
        # Store filtered count
        records_filtered = documents.count()
        
        # Apply ordering
        if order_column == 'branch_code':
            order_field = 'consumer__branch__branch_code'
        elif order_column == 'consumer':
            order_field = 'consumer__debtor_name'
        elif order_column == 'agreement_number':
            order_field = 'consumer__agreement_number'
        elif order_column == 'document_number':
            order_field = 'document_number'
        else:
            order_field = '-document_date'  # Default ordering
            
        if order_dir == 'desc' and not order_field.startswith('-'):
            order_field = f'-{order_field}'
        elif order_dir == 'asc' and order_field.startswith('-'):
            order_field = order_field[1:]
            
        documents = documents.order_by(order_field)
        # For regular DataTable view, apply pagination
        if not download_type and not agreement_number:
            documents = documents[start:start + length]

        # ==== DOWNLOAD IMAGE ZIP ====
        if download_type == 'image':
            try:
                # Hitung total setelah semua filter
                total_count = documents.count()

                # Ambil offset & limit dari query param (sudah kamu parse di atas)
                # offset = int(request.GET.get('offset', start))
                # limit  = int(request.GET.get('limit', length))

                # ✅ Jika offset di luar total data → 204 (Data sudah habis)
                if offset >= total_count:
                    return Response(status=204)

                # Hitung end index aman
                end_index = min(offset + limit, total_count)

                # Slice sesuai range yang diminta
                paginated_documents = documents[offset:end_index]

                # ✅ Jika slice kosong → 204 (Data sudah habis)
                if not paginated_documents:
                    return Response(status=204)

                # ===== Kumpulkan dok per agreement_number (tetap seperti punyamu) =====
                from collections import defaultdict
                agg = defaultdict(list)
                for doc in paginated_documents:
                    try:
                        agreement_number = getattr(doc.consumer, 'agreement_number', None)
                        if not agreement_number:
                            continue
                        qs = ConsumerDocumentPF.objects.filter(agreement_number=agreement_number)
                        for consumer_doc in qs:
                            if not consumer_doc or not consumer_doc.file_url:
                                continue
                            file_path = consumer_doc.file_url.replace('/media/', '')
                            if not default_storage.exists(file_path):
                                continue
                            agg[agreement_number].append(file_path)
                    except Exception as e:
                        print(f"Error grouping docs: {e}")
                        continue

                # ✅ Jika tidak ada file valid → 204
                total_files = sum(len(v) for v in agg.values())
                if total_files == 0:
                    return Response(status=204)

                # ===== Buat ZIP (lanjutkan kode kamu seperti semula) =====
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as outer_zip:
                    for doc in paginated_documents:
                        try:
                            agreement_number = getattr(doc.consumer, 'agreement_number', None)
                            if not agreement_number or agreement_number not in agg:
                                continue

                            document_number = getattr(doc, 'document_number', 'unknown')
                            safe_inner_name = f"{document_number}_{agreement_number}".replace(" ", "_").replace("/", "_").lower()
                            inner_zip_bytes = io.BytesIO()

                            with zipfile.ZipFile(inner_zip_bytes, 'w', zipfile.ZIP_DEFLATED) as inner_zip:
                                safe_prefix = f"{document_number}_{agreement_number}".replace(" ", "_").replace("/", "_").lower()
                                for idx, file_path in enumerate(agg[agreement_number], start=1):
                                    try:
                                        with default_storage.open(file_path, 'rb') as f:
                                            content = f.read()

                                        ext = os.path.splitext(file_path)[1].lower() or ''
                                        if len(agg[agreement_number]) == 1:
                                            safe_file_name = f"{safe_prefix}{ext}"
                                        else:
                                            safe_file_name = f"{safe_prefix}_{idx}{ext}"

                                        inner_zip.writestr(safe_file_name, content)
                                    except Exception as e:
                                        print(f"Error adding file to inner ZIP ({file_path}): {e}")
                                        continue

                            inner_zip_bytes.seek(0)
                            outer_zip.writestr(f"{safe_inner_name}.zip", inner_zip_bytes.getvalue())
                            agg[agreement_number] = []
                        except Exception as e:
                            print(f"Error building inner ZIP for agreement {agreement_number}: {e}")
                            continue

                zip_buffer.seek(0)

                # ===== Penamaan file (tetap seperti semula, boleh pakai punyamu) =====
                from datetime import datetime as dt
                try:
                    if paginated_documents:
                        first_doc = paginated_documents[0]
                        notary_obj = getattr(first_doc, 'notary', None)
                        notary_str = getattr(notary_obj, 'firm_name', None) if notary_obj else None
                    else:
                        notary_str = None
                    if not notary_str:
                        notary_str = firm_name or "all"
                except Exception as e:
                    print(f"Error getting notary name: {e}")
                    notary_str = "all"

                notary_str = notary_str.replace(" ", "_").replace("/", "_")
                is_single_document = (len(paginated_documents) == 1)
                if is_single_document:
                    only_doc = paginated_documents[0]
                    agreement_number = getattr(getattr(only_doc, 'consumer', None), 'agreement_number', 'unknown')
                    document_number = getattr(only_doc, 'document_number', 'unknown')
                    filename = f"warkah_{document_number}_{agreement_number}.zip"
                else:
                    # Range pakai yang diminta user (offset+1 s/d end_index)
                    range_str = f"{offset + 1}-{end_index}"
                    filename = f"warkah_{notary_str}_{range_str}_{from_date}.zip"

                response = HttpResponse(
                    zip_buffer.getvalue(),
                    content_type='application/zip'
                )
                response['Content-Disposition'] = f'attachment; filename="{filename}"'
                return response

            except Exception as e:
                print(f"Critical error in ZIP download: {e}")
                return Response({"error": "Gagal membuat file ZIP."}, status=500)

        # ==== DOWNLOAD EXCEL ====
        if download_type == 'excel':
            try:
                # Import datetime with alias to avoid any shadowing
                from datetime import datetime as dt
                # Define download_date early to ensure it's always available
                #download_date = dt.now().strftime("%Y-%m-%d")
                # Initialize filename with a safe default value
                filename = f"warkah_documents_{document_date}.xlsx"
                
                # Apply pagination to the queryset based on offset and limit
                # This ensures we only include the requested range of data in the Excel file
                try:
                    # Get the total count of documents after filtering
                    total_count = documents.count()
                    
                    # Ensure offset is within valid range
                    if offset >= total_count:
                        offset = 0
                    
                    # Ensure limit is reasonable
                    if limit <= 0 or limit > 1000:
                        limit = min(50, total_count)
                    
                    # Apply pagination
                    paginated_documents = documents[offset:offset + limit]
                    
                    # If no documents match the pagination, return an empty list
                    if not paginated_documents:
                        paginated_documents = []
                except Exception as e:
                    print(f"Error applying pagination for Excel: {e}")
                    # Fallback to a safe default (first 50 documents or all if less)
                    paginated_documents = documents[:min(50, documents.count())]
                
                # Set up the Excel workbook
                wb = openpyxl.Workbook()
                ws = wb.active
                ws.title = "Dokumen Warkah"
                
                try:
                    # Get field names, excluding certain fields
                    excluded_fields = {'id', 'consumer_id', 'created_at', 'updated_at'}
                    legal_fields = [f.name for f in LegalDocumentPF._meta.fields if f.name not in excluded_fields and f.name != 'consumer']
                    consumer_fields = [f.name for f in change_of_fiduciary._meta.fields if f.name not in excluded_fields]

                    # Create header row
                    header = legal_fields + consumer_fields
                    ws.append([h.replace("_", " ").title() for h in header])
                except Exception as e:
                    print(f"Error setting up Excel headers: {e}")
                    # Fallback to basic headers if there's an error
                    ws.append(["Document Number", "Branch Code", "Consumer Name", "Agreement Number", "Date"])
                    legal_fields = ["document_number", "document_date"]
                    consumer_fields = ["branch_code", "debtor_name", "agreement_number"]

                # Add data rows
                row_count = 0
                for doc in paginated_documents:
                    try:
                        row = []

                        # LegalDocument fields
                        for field in legal_fields:
                            try:
                                val = getattr(doc, field, '')
                                if field == 'notary':
                                    val = doc.notary.firm_name if doc.notary else '-'
                                elif field == 'power_user':
                                    val = (
                                        doc.power_user.get_full_name()
                                        if doc.power_user and hasattr(doc.power_user, 'get_full_name')
                                        else getattr(doc.power_user, 'name', '-')
                                    ) if doc.power_user else '-'
                                row.append(val)
                            except Exception as e:
                                print(f"Error getting legal field {field}: {e}")
                                row.append('-')  # Use a placeholder for the error

                        # Consumer fields
                        consumer = getattr(doc, 'consumer', None)
                        for field in consumer_fields:
                            try:
                                if not consumer:
                                    row.append('')
                                    continue

                                val = getattr(consumer, field, '')

                                # Handle foreign keys
                                if field == 'branch':
                                    val = consumer.branch.branch_code if consumer.branch else ''
                                elif field == 'uploader':
                                    val = consumer.uploader.username if consumer.uploader else ''

                                # Convert model objects to string
                                if hasattr(val, '__class__') and hasattr(val, '_meta'):
                                    val = str(val)

                                row.append(val)
                            except Exception as e:
                                print(f"Error getting consumer field {field}: {e}")
                                row.append('-')  # Use a placeholder for the error

                        ws.append(row)
                        row_count += 1
                    except Exception as e:
                        print(f"Error processing document row: {e}")
                        # Continue to the next document if there's an error
                        continue

                # If no rows were added (all failed), add a message row
                if row_count == 0:
                    ws.append(["No data available or error processing data"])

                # Save the workbook to a stream
                stream = io.BytesIO()
                wb.save(stream)
                stream.seek(0)

                # Generate filename with more specific information if possible
                try:
                    notary_str = (firm_name or "all").replace(" ", "_").replace("/", "_")
                    
                    # Check if we're dealing with a single document
                    is_single_document = False
                    if agreement_number:
                        try:
                            # Use the paginated queryset to determine if we're dealing with a single document
                            doc_count = len(paginated_documents)
                            is_single_document = (doc_count == 1)
                        except Exception as e:
                            print(f"Error counting documents for Excel: {e}")
                            # If we can't count, but have agreement_number, assume it's a single document
                            is_single_document = True
                    
                    if is_single_document:
                        filename = f"warkah_{document_number}_{agreement_number}.xlsx"
                    else:
                        range_str = f"{offset + 1}-{offset + limit}"
                        filename = f"warkah_{notary_str}_{range_str}_{document_date}.xlsx"
                except Exception as e:
                    print(f"Error generating Excel filename: {e}")
                    # We already have a safe default filename initialized above

                # Return the response
                response = HttpResponse(
                    stream,
                    content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                )
                response['Content-Disposition'] = f'attachment; filename="{filename}"'
                return response
                
            except Exception as e:
                print(f"Critical error in Excel download: {e}")
                # Return an error response if the Excel generation fails completely
                return Response(
                    {"error": "Failed to generate Excel file. Please try again or contact support."},
                    status=500
                )

        # ==== DEFAULT JSON ====
        try:
            # For DataTables server-side processing, return data in the expected format
            if draw:
                try:
                    # Format data for DataTables
                    data = []
                    for doc in documents:
                        try:
                            consumer = getattr(doc, 'consumer', None)
                            data.append({
                                'branch_code': getattr(consumer.branch, 'branch_code', '-') if consumer and consumer.branch else '-',
                                'consumer': getattr(consumer, 'debtor_name', '-') if consumer else '-',
                                'agreement_number': getattr(consumer, 'agreement_number', '-') if consumer else '-',
                                'document_number': getattr(doc, 'document_number', '-') or '-'
                            })
                        except Exception as e:
                            print(f"Error processing document for JSON response: {e}")
                            # Add a placeholder row with error information
                            data.append({
                                'branch_code': '-',
                                'consumer': 'Error processing document',
                                'agreement_number': '-',
                                'document_number': '-'
                            })
                    
                    # Ensure we have valid values for the counts
                    try:
                        total = records_total
                        if not isinstance(total, int) or total < 0:
                            total = 0
                    except Exception:
                        total = 0
                        
                    try:
                        filtered = records_filtered
                        if not isinstance(filtered, int) or filtered < 0:
                            filtered = 0
                    except Exception:
                        filtered = 0
                    
                    return Response({
                        'draw': draw,  # Already converted to int earlier
                        'recordsTotal': total,
                        'recordsFiltered': filtered,
                        'data': data
                    })
                except Exception as e:
                    print(f"Error generating DataTables response: {e}")
                    # Return a minimal valid DataTables response
                    return Response({
                        'draw': draw,
                        'recordsTotal': 0,
                        'recordsFiltered': 0,
                        'data': [],
                        'error': 'Error processing data'
                    })
            else:
                # For backward compatibility with existing code
                try:
                    serializer = LegalDocumentSerializer(documents, many=True)
                    return Response(serializer.data)
                except Exception as e:
                    print(f"Error serializing documents: {e}")
                    return Response([], status=200)  # Return empty list instead of error
                    
        except Exception as e:
            print(f"Critical error in JSON response: {e}")
            # Return a generic error response
            return Response(
                {"error": "An unexpected error occurred while processing your request."},
                status=500
            )
    
class WarkahExcelUploadPFAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @class_permission_required("read-warkah-pf")
    def post(self, request):
        if 'file' not in request.FILES:
            return Response({"error": "No file was uploaded."}, status=status.HTTP_400_BAD_REQUEST)

        excel_file = request.FILES['file']
        file_name = excel_file.name
        if not (file_name.endswith('.xlsx') or file_name.endswith('.xls')):
            return Response({"error": "Invalid file format. Only .xlsx and .xls files are allowed."},status=status.HTTP_400_BAD_REQUEST)

        try:
            import io, os, zipfile
            from datetime import datetime as dt

            df = pd.read_excel(excel_file)
            if df.empty:
                return Response({"error": "The uploaded Excel file is empty."},status=status.HTTP_400_BAD_REQUEST)

            # --- Cari kolom No Perjanjian ---
            possible_columns = [
                'No Perjanjian', 'NoPerjanjian', 'no_perjanjian',
                'Agreement Number', 'AgreementNumber', 'agreement_number',
                'Nomor Perjanjian', 'NomorPerjanjian', 'nomor_perjanjian'
            ]
            agreement_column = next((col for col in possible_columns if col in df.columns),df.columns[0] if len(df.columns) > 0 else None)

            if not agreement_column:
                return Response({"error": "Could not find a column containing agreement numbers."},status=status.HTTP_400_BAD_REQUEST)

            agreement_numbers = df[agreement_column].dropna().astype(str).tolist()
            if not agreement_numbers:
                return Response({"error": "No agreement numbers found in the uploaded file."},status=status.HTTP_400_BAD_REQUEST)

            # --- Ambil data dokumen dari database ---
            documents = (
                LegalDocumentPF.objects
                .filter(consumer__agreement_number__in=agreement_numbers)
                .select_related('consumer')
                .all()
            )
            if _user_is_staff_cabang(request.user):
                user_branches = _user_branch_codes(request.user)
                if not user_branches:
                    return Response({"error": "Branch wajib diisi untuk user non-superadmin."}, status=403)
                documents = documents.filter(consumer__branch__branch_code__in=user_branches)
            if not documents:
                return Response({"error": "No matching records found for the provided agreement numbers."},status=status.HTTP_404_NOT_FOUND)

            for doc in documents:
                document_date = getattr(doc, 'document_date', None)
                if document_date:
                    document_date_str = document_date.strftime("%Y-%m-%d")
                else:
                    document_date_str = "unknown"

                outer_zip_name = f"warkah_{document_date_str}.zip"
            outer_zip_buffer = io.BytesIO()

            with zipfile.ZipFile(outer_zip_buffer, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as outer_zip:
                # Loop tiap dokumen
                for doc in documents:
                    try:
                        agreement_number = getattr(doc.consumer, 'agreement_number', None)
                        document_number = getattr(doc, 'document_number', 'unknown')
                        safe_name = f"{document_number}_{agreement_number}".replace(" ", "_").replace("/", "_").lower()

                        consumer_doc = ConsumerDocumentPF.objects.filter(agreement_number=agreement_number).first()
                        if not consumer_doc or not consumer_doc.file_url:
                            print(f"[SKIP] Tidak ada file untuk {agreement_number}")
                            continue

                        file_path = consumer_doc.file_url.replace('/media/', '')
                        if not default_storage.exists(file_path):
                            print(f"[SKIP] File tidak ditemukan: {file_path}")
                            continue

                        # --- Baca file dokumen ---
                        with default_storage.open(file_path, 'rb') as f:
                            content = f.read()
                            ext = os.path.splitext(file_path)[1].lower()

                            # --- Buat ZIP kecil untuk file ini ---
                            inner_buffer = io.BytesIO()
                            with zipfile.ZipFile(inner_buffer, 'w', compression=zipfile.ZIP_DEFLATED) as inner_zip:
                                inner_zip.writestr(f"{safe_name}{ext}", content)

                            inner_buffer.seek(0)
                            # --- Tambahkan ke ZIP besar ---
                            outer_zip.writestr(f"{safe_name}.zip", inner_buffer.getvalue())

                            print(f"[OK] Ditambahkan {safe_name}.zip ke outer zip")

                    except Exception as e:
                        print(f"[ERROR] Gagal proses dokumen {getattr(doc, 'id', None)}: {e}")
                        continue

            outer_zip_buffer.seek(0)
            response = HttpResponse(outer_zip_buffer.getvalue(), content_type='application/zip')
            response['Content-Disposition'] = f'attachment; filename="{outer_zip_name}"'
            return response

        except Exception as e:
            print(f"Error processing Excel file: {e}")
            return Response({"error": f"Error processing Excel file: {str(e)}"},status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class LegalDocumentViewSetPF(viewsets.ModelViewSet):
    queryset = LegalDocumentPF.objects.all()
    serializer_class = LegalDocumentSerializer

    def get_permissions(self):

        perms_map = {
            'list': 'view_legal_document',
            'retrieve': 'view_legal_document',
            'create': 'create_legal_document',
            'update': 'edit_legal_document',
            'partial_update': 'edit_legal_document',
            'destroy': 'delete_legal_document',
        }
        action = self.action
        required_perm = perms_map.get(action)

        if not required_perm:
            return super().get_permissions()

        if not user_has_permission(self.request.user, resolve_permission_alias(required_perm)):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("You do not have permission.")

        return super().get_permissions()
    
@login_required(login_url='')
@permission_required("read-warkah-pf")
def dokumen_pf_list_pf(request):
    query = request.GET.get('q', '')

    legal_docs = LegalDocumentPF.objects.select_related('consumer')
    if not _is_superadmin(request.user):
        user_branches = _user_branch_codes(request.user)
        if not user_branches:
            legal_docs = legal_docs.none()
        else:
            legal_docs = legal_docs.filter(consumer__branch__branch_code__in=user_branches)
    if query:
        legal_docs = legal_docs.filter(consumer__debtor_name__icontains=query)

    paginator = Paginator(legal_docs.order_by('-id'), 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'query': query,
    }
    return render(request, 'warkah/page-warkah-dokumen-pf.html', context)

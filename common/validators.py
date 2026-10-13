"""
File upload validators
"""
import os
from django.http import HttpResponse


# Allowed Excel file extensions
ALLOWED_EXCEL_EXT = {'.xlsx', '.xls', '.xlsm'}

# Allowed Excel MIME types
ALLOWED_EXCEL_MIME = {
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',  # .xlsx
    'application/vnd.ms-excel',  # .xls
    'application/vnd.ms-excel.sheet.macroEnabled.12',  # .xlsm
}

# Magic bytes for Excel files
EXCEL_MAGIC_BYTES = {
    b'PK\x03\x04': '.xlsx',  # XLSX (ZIP format)
    b'\xd0\xcf\x11\xe0': '.xls',  # XLS (OLE2 format)
}


def validate_excel_file(uploaded_file, max_size_mb=10):
    """
    Validate Excel file upload

    Args:
        uploaded_file: Django UploadedFile object
        max_size_mb: Maximum file size in MB

    Returns:
        tuple: (is_valid, error_message)
    """
    if not uploaded_file:
        return False, "No file uploaded"

    # 1. Check file extension
    file_name = getattr(uploaded_file, 'name', '')
    if not file_name:
        return False, "Invalid file name"

    ext = os.path.splitext(file_name)[1].lower()
    if ext not in ALLOWED_EXCEL_EXT:
        return False, f"Invalid file extension. Allowed: {', '.join(ALLOWED_EXCEL_EXT)}"

    # 2. Check MIME type
    mime_type = getattr(uploaded_file, 'content_type', '')
    if not mime_type or mime_type not in ALLOWED_EXCEL_MIME:
        return False, f"Invalid MIME type: {mime_type}. Allowed: Excel files only"

    # 3. Check file size
    file_size = getattr(uploaded_file, 'size', 0)
    max_size_bytes = max_size_mb * 1024 * 1024
    if file_size > max_size_bytes:
        return False, f"File too large. Maximum size: {max_size_mb}MB"

    # 4. Check magic bytes
    try:
        # Read first 4 bytes
        uploaded_file.seek(0)
        magic_bytes = uploaded_file.read(4)
        uploaded_file.seek(0)  # Reset file pointer

        is_valid_magic = False
        for magic, file_type in EXCEL_MAGIC_BYTES.items():
            if magic_bytes.startswith(magic):
                is_valid_magic = True
                break

        if not is_valid_magic:
            return False, "Invalid file format. File is not a valid Excel file"
    except Exception as e:
        return False, f"Error reading file: {str(e)}"

    return True, None


def validate_excel_file_or_error(uploaded_file, max_size_mb=10):
    """
    Validate Excel file and return HttpResponse error if invalid

    Args:
        uploaded_file: Django UploadedFile object
        max_size_mb: Maximum file size in MB

    Returns:
        HttpResponse or None: Error response if invalid, None if valid
    """
    is_valid, error_msg = validate_excel_file(uploaded_file, max_size_mb)

    if not is_valid:
        return HttpResponse(error_msg, status=400, content_type="text/plain; charset=utf-8")

    return None

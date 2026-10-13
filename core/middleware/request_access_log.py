import time
import logging
from functools import lru_cache
from django.utils.deprecation import MiddlewareMixin
from django.utils import timezone
from django.urls import resolve, Resolver404
from django.conf import settings
from django.db import transaction

from AdminMenu.auth_helpers import log_activity
from AdminMenu.models import Menu

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = getattr(settings, "ACCESS_LOG_DEBOUNCE_SECONDS", 1.5)

@lru_cache(maxsize=1)
def _safe_prefixes():
    lst = []
    # Penting: JANGAN tambahkan "/api/" di sini supaya API tidak mempengaruhi.
    if getattr(settings, "STATIC_URL", None):
        lst.append(settings.STATIC_URL)
    if getattr(settings, "MEDIA_URL", None):
        lst.append(settings.MEDIA_URL)
    return tuple(lst)

EXCLUDED_PREFIXES = _safe_prefixes()
SKIP_PATHS = set(getattr(settings, "ACCESS_LOG_SKIP_PATHS", {"/healthz", "/readiness", "/metrics"}))

def _client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or ""

class RequestAccessLogMiddleware(MiddlewareMixin):
    """
    HANYA mencatat akses MENU (page view):
    - User login
    - method GET
    - BUKAN AJAX
    - path cocok dengan Menu.link (normalisasi trailing slash)
    Tidak pernah log endpoint permission / API / action lain.
    """

    def process_request(self, request):
        request._req_start_ts = time.time()
        return None

    def process_response(self, request, response):
        try:
            # Bisa disetel dari view/dekorator untuk skip manual
            if getattr(request, "_skip_access_log", False):
                return response

            user = getattr(request, "user", None)
            path = (getattr(request, "path", "") or "").strip()
            method = getattr(request, "method", "GET").upper()

            # Hanya user login
            if not user or not user.is_authenticated:
                return response

            # ===============================
            # 1. LOG UPLOAD FILE (POST)
            # ===============================
            try:
                if method == "POST" and hasattr(request, "FILES") and request.FILES:
                    # Kalau mau dibatasi hanya beberapa path, bisa tambahkan filter di sini
                    # misal:
                    # if path not in ["/staff-cabang-import/", "/staff-cabang-import-oc/"]:
                    #     pass
                    for field_name, file_obj in request.FILES.items():
                        file_name = getattr(file_obj, "name", None)
                        file_size = getattr(file_obj, "size", None)
                        content_type = getattr(file_obj, "content_type", None)

                        ua = request.META.get("HTTP_USER_AGENT", "")[:500]
                        ref = request.META.get("HTTP_REFERER", "")[:500]
                        ip = _client_ip(request)

                        props_upload = {
                            "path": path,
                            "method": method,
                            "file_field": field_name,
                            "file_name": file_name,
                            "file_size": file_size,
                            "content_type": content_type,
                            "ip": ip,
                            "user_agent": ua,
                            "referer": ref,
                            "status": getattr(response, "status_code", None),
                            "ts": timezone.now().isoformat(),
                        }

                        description_upload = f"Upload file '{file_name}' ke {path}"

                        def _write_upload():
                            log_activity(
                                request,
                                log_name="Upload File",
                                description=description_upload,
                                subject_type="Upload",
                                subject_id=None,
                                properties=props_upload
                            )

                        try:
                            transaction.on_commit(_write_upload)
                        except Exception:
                            _write_upload()
            except Exception:
                logger.exception("Gagal mencatat log upload file")

            # ===============================
            # 2. LOG MENU GET (PAGE VIEW)
            # ===============================
            # Hanya GET (page view)
            if method != "GET":
                return response

            # Skip ajax
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return response

            # Skip path umum (static/media/healthcheck)
            if not path or any(path.startswith(p) for p in EXCLUDED_PREFIXES) or path in SKIP_PATHS:
                return response

            # Debounce GET per path (hindari spam refresh)
            sess = getattr(request, "session", None)
            if sess is not None and DEBOUNCE_SECONDS:
                last = sess.get("_log_last_menu", {})
                last_ts = last.get(path, 0)
                now_ts = time.time()
                if (now_ts - last_ts) < float(DEBOUNCE_SECONDS):
                    return response
                last[path] = now_ts
                sess["_log_last_menu"] = last

            # Hanya log kalau path ini terdaftar sebagai Menu.link
            normalized_paths = {path, path.rstrip('/'), (path.rstrip('/') + '/')}
            menu = Menu.objects.filter(
                link__in=list(normalized_paths)
            ).only('id', 'name', 'link').first()
            if not menu:
                # Jika tidak ketemu di tabel Menu, JANGAN dicatat
                return response

            # Label menu
            menu_name = getattr(menu, 'name', None) or 'Unknown Menu'
            description = f"Akses halaman {menu_name}"

            # Info tambahan
            status_code = getattr(response, "status_code", None)
            ua = request.META.get("HTTP_USER_AGENT", "")[:500]
            ref = request.META.get("HTTP_REFERER", "")[:500]
            ip = _client_ip(request)
            dur_ms = None
            try:
                if hasattr(request, "_req_start_ts"):
                    dur_ms = int((time.time() - request._req_start_ts) * 1000)
            except Exception:
                pass

            props = {
                "menu": menu_name,
                "path": path,
                "method": method,
                "status": status_code,
                "ip": ip,
                "user_agent": ua,
                "referer": ref,
                "duration_ms": dur_ms,
                "ts": timezone.now().isoformat()
            }

            def _write():
                log_activity(
                    request,
                    log_name="Access Menu",
                    description=description,
                    subject_type="Menu",
                    subject_id=getattr(menu, "id", None),
                    properties=props
                )

            try:
                transaction.on_commit(_write)
            except Exception:
                _write()

        except Exception:
            logger.exception("Gagal mencatat access menu / upload file")

        return response
# accounts/signals.py
import logging
from django.dispatch import receiver
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.utils import timezone
from AdminMenu.auth_helpers import log_activity

logger = logging.getLogger(__name__)

def _ip_from_meta(request):
    if not request: 
        return ""
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or ""

@receiver(user_logged_in)
def on_user_logged_in(sender, request, user, **kwargs):
    try:
        props = {
            "ip": _ip_from_meta(request),
            "user_agent": (request.META.get("HTTP_USER_AGENT","")[:500] if request else ""),
            "ts": timezone.now().isoformat()
        }
        log_activity(request, "Auth", "User Login", subject_type="Auth", subject_id=user.id, properties=props)
    except Exception:
        logger.exception("Gagal log user_logged_in")

@receiver(user_logged_out)
def on_user_logged_out(sender, request, user, **kwargs):
    try:
        props = {
            "ip": _ip_from_meta(request),
            "user_agent": (request.META.get("HTTP_USER_AGENT","")[:500] if request else ""),
            "ts": timezone.now().isoformat()
        }
        # request bisa None saat logout via backend tertentu—antisipasi
        log_activity(request, "Auth", "User Logout", subject_type="Auth", subject_id=(user.id if user else None), properties=props)
    except Exception:
        logger.exception("Gagal log user_logged_out")

@receiver(user_login_failed)
def on_user_login_failed(sender, credentials, request, **kwargs):
    try:
        props = {
            "username": credentials.get("username"),
            "ip": _ip_from_meta(request),
            "user_agent": (request.META.get("HTTP_USER_AGENT","")[:500] if request else ""),
            "ts": timezone.now().isoformat()
        }
        # request bisa None → log_activity handle None user
        log_activity(request, "Auth", "User Login Failed", subject_type="Auth", subject_id=None, properties=props)
    except Exception:
        logger.exception("Gagal log user_login_failed")
"""Settings VPS Mizuho: akses via IP tanpa HTTPS (testing). Hapus override ini setelah domain + HTTPS siap."""
from .settings import *  # noqa: F401,F403

SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# SMTP belum diisi -> kode OTP ditulis ke log container (docker compose logs web)
if not EMAIL_HOST:  # noqa: F405
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

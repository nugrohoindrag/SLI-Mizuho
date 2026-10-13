from django.shortcuts import redirect
from django.conf import settings
from django.utils.deprecation import MiddlewareMixin
from django.http import JsonResponse
from django.core.cache import cache
from rest_framework_simplejwt.tokens import UntypedToken, RefreshToken
from rest_framework_simplejwt.exceptions import TokenError, InvalidToken
import jwt

# AdminUser/middleware.py
EXEMPT_PATHS = [
    '/login', '/login/', '/logout', '/logout/',
    '/static/', '/media/', '/favicon.ico',
    '/admin/login/', 
    '/api/login/',               # <— tambahkan ini
    '/api/logout/',              # (opsional) kalau ada
    '/api/get-csrf-token/',      # (opsional) kalau ada
    '/api/token/refresh/',
]
FORCE_PASSWORD_CHANGE_EXEMPT = [
    '/profile/',
    '/api/user/profile/',
    '/api/user/profile/update/',
    '/api/user/change-password/',
]


CACHE_KEY_FMT = "jwt:access_by_refresh:{jti}"

def _access_cookie_max_age():
    try:
        lifetime = settings.SIMPLE_JWT.get('ACCESS_TOKEN_LIFETIME')
        if lifetime:
            return int(lifetime.total_seconds())
    except Exception:
        pass
    return 1800

class JWTTokenRedirectMiddleware(MiddlewareMixin):
    def process_request(self, request):
        path = request.path or '/'

        # **Short-circuit**: exempt path → skip semua logika
        if any(path.startswith(p) for p in EXEMPT_PATHS):
            return None

        # Skip OPTIONS/HEAD
        if request.method in ('OPTIONS', 'HEAD'):
            return None

        # Kalau sudah ada Authorization, biarkan view yang handle
        if request.META.get('HTTP_AUTHORIZATION'):
            return None

        # Deteksi permintaan HTML
        accept = (request.headers.get('Accept') or '').lower()
        is_xhr = (request.headers.get('X-Requested-With') == 'XMLHttpRequest')
        wants_html = ('text/html' in accept) and not is_xhr

        access_token = request.COOKIES.get('access_token')
        refresh_token = request.COOKIES.get('refresh_token')

        def unauthorized():
            if wants_html:
                resp = redirect(settings.LOGIN_URL)
                resp.delete_cookie('access_token', path='/')
                return resp
            else:
                resp = JsonResponse({'detail': 'Unauthorized'}, status=401)
                resp.delete_cookie('access_token', path='/')
                return resp

        def _set_auth_header(token: str):
            request.META['HTTP_AUTHORIZATION'] = f'Bearer {token}'

        if access_token:
            try:
                UntypedToken(access_token)
                _set_auth_header(access_token)
            except (TokenError, jwt.ExpiredSignatureError, jwt.DecodeError, InvalidToken):
                if refresh_token:
                    new_access = self.refresh_access_token(refresh_token)
                    if new_access:
                        request._refresh_access_token = new_access
                        _set_auth_header(new_access)
                        return None
                return unauthorized()
        else:
            return unauthorized()

        return None

    def refresh_access_token(self, refresh_token: str):
        try:
            refresh = RefreshToken(refresh_token)
            jti = refresh['jti']

            cached = cache.get(CACHE_KEY_FMT.format(jti=jti))
            if cached:
                return cached

            try:
                from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
                if BlacklistedToken.objects.filter(token__jti=jti).exists():
                    return None
            except Exception:
                pass

            new_access_token = str(refresh.access_token)
            exp_ts = int(refresh.access_token['exp'])
            now_ts = int(refresh.access_token.current_time)
            ttl = max(1, exp_ts - now_ts - 5)
            cache.set(CACHE_KEY_FMT.format(jti=jti), new_access_token, ttl)

            return new_access_token
        except Exception:
            return None

    def process_response(self, request, response):
        new_access = getattr(request, '_refresh_access_token', None)
        if new_access:
            response.set_cookie(
                'access_token',
                new_access,
                path='/',
                httponly=True,
                secure=not settings.DEBUG,
                samesite='Lax',
                max_age=_access_cookie_max_age(),
            )
        return response

class ForcePasswordChangeMiddleware(MiddlewareMixin):
    def process_request(self, request):
        path = request.path or '/'

        # Skip unauthenticated or exempt paths
        if any(path.startswith(p) for p in EXEMPT_PATHS):
            return None
        if any(path.startswith(p) for p in FORCE_PASSWORD_CHANGE_EXEMPT):
            return None

        if request.method in ('OPTIONS', 'HEAD'):
            return None

        if not request.user.is_authenticated:
            return None

        if not request.session.get('force_password_change'):
            return None

        accept = (request.headers.get('Accept') or '').lower()
        is_xhr = (request.headers.get('X-Requested-With') == 'XMLHttpRequest')
        wants_html = ('text/html' in accept) and not is_xhr

        if wants_html:
            return redirect('/profile/')

        return JsonResponse(
            {'detail': 'Password must be changed', 'force_password_change': True},
            status=403,
        )

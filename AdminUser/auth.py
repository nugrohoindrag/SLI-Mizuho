from rest_framework_simplejwt.authentication import JWTAuthentication

EXEMPT_AUTH_PATHS = [
    '/login', '/login/',
    '/logout', '/logout/',
    '/static/', '/media/', '/favicon.ico',
    '/admin/login/', '/api/token/refresh/',
]

class SkipPathJWTAuthentication(JWTAuthentication):
    """
    JWTAuthentication tapi skip path tertentu agar tidak parsing token.
    """
    def authenticate(self, request):
        path = request.path or '/'
        if any(path.startswith(p) for p in EXEMPT_AUTH_PATHS):
            return None
        return super().authenticate(request)
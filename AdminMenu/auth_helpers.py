from AdminRole.models import RoleUser, RolePermission
from accessloguser.models import ActivityLogUser
from AdminPermission.models import Permission
from AdminMenu.models import Menu
from django.shortcuts import render
from functools import wraps
from django.utils.timezone import now
from django.http import HttpResponseForbidden
from rest_framework import status
import re
from rest_framework.response import Response
import logging
from django.db import transaction

logger = logging.getLogger(__name__)

ROLE_MAP = {
    "superadmin": {"names": ["superadministrator"]},
    "ho": {"names": ["HO"]},
    "operator": {"names": ["operator"]},
    "staff_cabang": {"names": ["staff_cabang"]},
    "notaris": {"names": ["Notaris"]},
    "warkah": {"names": ["Warkah"]},
    "finance": {"names": ["Finance"]},
    "printing": {"names": ["Printing"]},
}

def role_names(role_key):
    role_obj = ROLE_MAP.get(role_key, {})
    return role_obj.get("names", [role_key])

def user_has_role(user, role_key):
    return user_has_role_names(user, role_names(role_key))

def resolve_permission_alias(alias_name):
    if '-' in alias_name:
        return alias_name if Permission.objects.filter(name=alias_name).exists() else alias_name

    match = re.match(r"^(view|edit|create|delete)_(.+)$", alias_name)
    if match:
        verb, obj = match.groups()
        verb_map = {'view': 'read', 'edit': 'update'}
        real_name = f"{verb_map.get(verb, verb)}-{obj.replace('_', '-')}"
        if Permission.objects.filter(name=real_name).exists():
            return real_name
    return alias_name

# Ambil semua role_id dari user
def get_user_role_ids(user):

    if not user or not user.is_authenticated:
        return []

    return RoleUser.objects.filter(user=user).values_list('role_id', flat=True)

# Ambil semua permission name yang dimiliki user melalui role
def get_user_permissions(user):
    role_ids = get_user_role_ids(user)
    if not role_ids:
        return Permission.objects.none()

    return Permission.objects.filter(
        id__in=RolePermission.objects.filter(
            role_id__in=role_ids
        ).values_list('permission_id', flat=True)
    ).values_list('name', flat=True).distinct()
    
def class_permission_required(permission_name):
    def decorator(view_method):
        @wraps(view_method)
        def _wrapped_view(self, request, *args, **kwargs):
            actual_permission = resolve_permission_alias(permission_name)
            if request.user.is_superuser or user_has_permission(request.user, actual_permission):
                return view_method(self, request, *args, **kwargs)
            return render(request, '403.html', status=403)

        return _wrapped_view
    return decorator

def function_permission_required(permission_name):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            actual_permission = resolve_permission_alias(permission_name)

            if request.user.is_superuser or user_has_permission(request.user, actual_permission):
                return view_func(request, *args, **kwargs)

            # ❗ Ini pengganti render(request, '403.html')
            return render(request, '403.html', status=403)

        return _wrapped_view
    return decorator

# Cek apakah user memiliki permission tertentu
def user_has_permission(user, permission_name):
    print(f"[CHECK] user: {user.username} → permission_name: {permission_name}")

    if user.is_superuser or user.username.lower() == 'admin':
        return True

    role_ids = get_user_role_ids(user)
    if not role_ids:
        return False

    exists = RolePermission.objects.filter(
        role_id__in=role_ids,
        permission__name=permission_name
    ).exists()
    print(f"[RESULT] exists: {exists}")
    return exists

# Cek apakah user memiliki salah satu dari beberapa permission
def user_has_permissions(user, permission_names):
    if user.is_superuser or user.username.lower() == 'admin':
        return True

    role_ids = get_user_role_ids(user)
    if not role_ids:
        return False

    return RolePermission.objects.filter(
        role_id__in=role_ids,
        permission__name__in=permission_names
    ).exists()

# Ambil daftar menu yang bisa diakses user berdasarkan permission via role
def get_user_menus(user):
    if user.is_superuser or user.username.lower() == 'admin':
        return Menu.objects.select_related('parent').prefetch_related('permissions', 'children').all()

    role_ids = get_user_role_ids(user)
    if not role_ids:
        return Menu.objects.none()

    permission_ids = RolePermission.objects.filter(
        role_id__in=role_ids
    ).values_list('permission_id', flat=True)

    return Menu.objects.filter(
        permissions__id__in=permission_ids
    ).distinct().select_related('parent').prefetch_related('permissions', 'children')

# Cek apakah user memiliki salah satu role berdasarkan ID
def user_has_role_ids(user, allowed_role_ids):
    if user.is_superuser or user.username.lower() == 'admin':
        return True

    user_role_ids = set(get_user_role_ids(user))
    return bool(user_role_ids.intersection(set(allowed_role_ids)))

# Cek apakah user memiliki salah satu role berdasarkan nama (lebih readable)
def user_has_role_names(user, allowed_role_names):
    return RoleUser.objects.filter(
        user=user,
        role__name__in=allowed_role_names
    ).exists()

# Ambil nama-nama role user
def get_user_role_names(user):
    return RoleUser.objects.filter(user=user).values_list('role__name', flat=True)

def permission_required(permission_name):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            actual_permission = resolve_permission_alias(permission_name)

            print(f"[DEBUG] Checking permission: {actual_permission}")
            print(f"[DEBUG] User permissions: {list(get_user_permissions(request.user))}")

            if request.user.is_superuser or request.user.username.lower() == 'admin':
                return view_func(request, *args, **kwargs)

            if not user_has_permission(request.user, actual_permission):
                return render(request, "403.html", status=403)
            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator

# 🔐 Decorator: role_required
def role_required(allowed_role_ids):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not user_has_role_ids(request.user, allowed_role_ids):
                return HttpResponseForbidden("Forbidden: role not allowed.")
            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator

def log_activity(request, log_name, description, subject_type=None, subject_id=None, properties=None):
    try:
        ActivityLogUser.objects.create(
            log_name=log_name,
            description=description,
            subject_type=subject_type or '-',
            subject_id=subject_id,
            causer=request.user if request.user.is_authenticated else None,
            causer_type='User' if request.user.is_authenticated else 'System',
            properties=properties or {}
        )
        logger.info("Activity Logged: %s - %s", log_name, description)
    except Exception:
        logger.exception("Gagal mencatat log aktivitas")
        
def log_action_cbv(action_name, subject_type="Object", methods=("POST","PUT","PATCH","DELETE"), subject_id_getter=None):
    """
    subject_id_getter: callable(self, request, *args, **kwargs) -> subject_id
    """
    def class_decorator(cls):
        orig_dispatch = cls.dispatch

        @wraps(orig_dispatch)
        def new_dispatch(self, request, *args, **kwargs):
            response = orig_dispatch(self, request, *args, **kwargs)

            try:
                if request.user.is_authenticated and request.method.upper() in methods:
                    sid = subject_id_getter(self, request, *args, **kwargs) if callable(subject_id_getter) else kwargs.get("pk")
                    props = {
                        "path": request.path,
                        "method": request.method,
                        "status": getattr(response, "status_code", None)
                    }
                    def _write():
                        log_activity(
                            request,
                            log_name="Action Log",
                            description=action_name,
                            subject_type=subject_type,
                            subject_id=sid,
                            properties=props
                        )
                    try:
                        transaction.on_commit(_write)
                    except Exception:
                        _write()
            except Exception:
                pass

            return response

        cls.dispatch = new_dispatch
        return cls
    return class_decorator

def log_action_fbv(action_name, subject_type="Object"):
    """
    Pakai di FBV apa pun (POST/PUT/PATCH/DELETE) untuk melog aksi + status code.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            response = view_func(request, *args, **kwargs)
            try:
                if request.user.is_authenticated and request.method.upper() in ("POST","PUT","PATCH","DELETE"):
                    sid = kwargs.get("pk") or kwargs.get("id")
                    props = {
                        "path": request.path,
                        "method": request.method,
                        "status": getattr(response, "status_code", None),
                    }
                    def _write():
                        log_activity(
                            request,
                            log_name="Action Log",
                            description=action_name,
                            subject_type=subject_type,
                            subject_id=sid,
                            properties=props
                        )
                    try:
                        transaction.on_commit(_write)
                    except Exception:
                        _write()
            except Exception:
                pass
            return response
        return _wrapped
    return decorator

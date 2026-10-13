from django.db.models import Q
from rest_framework.response import Response

from AdminMenu.auth_helpers import get_user_role_names
from common.const.role_map import ROLE_KEYS, has_role
from .models import UserBranch


def user_is_ho(user, role_names=None):
    if getattr(user, "is_superuser", False):
        return True
    if role_names is None:
        role_names = list(get_user_role_names(user))
    return has_role(role_names, ROLE_KEYS["HO"])


def user_can_view_all_branches(user, role_names=None):
    """Return the only cross-application branch bypass introduced by the MVP."""
    return user_is_ho(user, role_names=role_names)


def user_is_branch_restricted(user):
    """Scope staff_cabang to assignments, with HO taking precedence."""
    role_names = list(get_user_role_names(user))
    return (
        has_role(role_names, ROLE_KEYS["STAFF_CABANG"])
        and not user_is_ho(user, role_names=role_names)
    )


def get_user_branch_codes(user):
    if not user or not getattr(user, "is_authenticated", False):
        return []

    if user_is_branch_restricted(user):
        codes = list(
            UserBranch.objects
            .filter(user=user)
            .values_list("branch_id", flat=True)
            .distinct()
        )
        return [str(code).strip() for code in codes if str(code or "").strip()]

    # Preserve the pre-multi-branch behavior for every role other than
    # staff_cabang: at most one legacy users.branch_code is visible.
    legacy_code = get_primary_user_branch_code(user)
    if not legacy_code:
        return []
    clean_code = str(legacy_code).strip()
    return [clean_code] if clean_code else []


def get_primary_user_branch_code(user):
    branch = getattr(user, "branch", None)
    if branch:
        code = getattr(branch, "branch_code", None) or getattr(branch, "pk", None)
        if code:
            return code
    return getattr(user, "branch_code", None) or getattr(user, "branch_id", None)


def branch_scope_q(lookup, branch_codes):
    if not branch_codes:
        return Q(pk__in=[])
    if len(branch_codes) == 1:
        return Q(**{lookup: branch_codes[0]})
    return Q(**{f"{lookup}__in": branch_codes})


def filter_queryset_by_user_branches(qs, user, lookup="branch__branch_code"):
    if not user_is_branch_restricted(user):
        return qs
    branch_codes = get_user_branch_codes(user)
    if not branch_codes:
        return qs.none()
    return qs.filter(branch_scope_q(lookup, branch_codes))


def require_user_branches(user, detail="Branch wajib diisi untuk user staff_cabang."):
    if not user_is_branch_restricted(user):
        return [], None
    branch_codes = get_user_branch_codes(user)
    if not branch_codes:
        return branch_codes, Response({"detail": detail}, status=403)
    return branch_codes, None

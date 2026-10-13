from AdminMenu.auth_helpers import user_has_permission, resolve_permission_alias

def check_permission(user, alias):
    return user_has_permission(user, resolve_permission_alias(alias))
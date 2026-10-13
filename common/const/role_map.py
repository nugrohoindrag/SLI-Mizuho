ROLE_MAP = {
    "superadmin": {"name": "superadministrator", "aliases": ["superadministrator"]},
    "rpa": {"name": "RPA", "aliases": ["rpa"]},
    "admin": {"name": "administrator", "aliases": ["administrator", "admin"]},
    "ho": {"name": "HO", "aliases": ["ho", "head office", "head-office", "head_office"]},
    "operator": {"name": "operator", "aliases": ["operator"]},
    "staff_cabang": {"name": "staff_cabang", "aliases": ["staff_cabang"]},
    "notaris": {"name": "Notaris", "aliases": ["notaris"]},
    "warkah": {"name": "Warkah", "aliases": ["warkah"]},
    "finance": {"name": "Finance", "aliases": ["finance"]},
    "printing": {"name": "Printing", "aliases": ["printing"]},
}

ROLE_KEYS = {
    "SUPERADMIN": "superadmin",
    "ADMIN": "admin",
    "HO": "ho",
    "OPERATOR": "operator",
    "STAFF_CABANG": "staff_cabang",
    "NOTARIS": "notaris",
    "WARKAH": "warkah",
    "FINANCE": "finance",
    "PRINTING": "printing",
    "RPA": "rpa",
}


def role_aliases(role_key):
    role_obj = ROLE_MAP.get(role_key, {})
    aliases = role_obj.get("aliases", [])
    name = role_obj.get("name")
    if name:
        aliases = list(dict.fromkeys(aliases + [name]))
    return aliases


def role_names(role_key):
    return [name.lower() for name in role_aliases(role_key)]


def has_role(role_names_list, role_key):
    normalized = {str(name).strip().lower() for name in role_names_list or []}
    return bool(normalized.intersection(set(role_names(role_key))))

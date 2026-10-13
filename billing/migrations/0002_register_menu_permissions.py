"""
Daftarkan menu Billing & History Billing, permission read-billing / upload-billing,
dan hak akses role (requirement §10.3): HO, Finance, superadministrator. staff_cabang tidak.
"""
from django.db import migrations

PERMISSIONS = (
    ("read-billing", "Read Billing", "Lihat tagihan klien, history pembayaran & export"),
    ("upload-billing", "Upload Billing", "Upload Excel pembayaran dari klien"),
)
ROLES = ("ho", "finance", "superadministrator")
MENUS = (
    # (nama, link, icon)
    ("Billing", "/billing/", "fa-solid fa-file-invoice-dollar"),
    ("History Billing", "/billing/history/", "fa-solid fa-clock-rotate-left"),
)
ROOT_NAME = "reguler"  # sejajar dengan menu Finance & Invoice


def register(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        for name, display, desc in PERMISSIONS:
            cursor.execute(
                """
                INSERT INTO permissions (name, display_name, description, created_at, updated_at)
                SELECT %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE name = %s)
                """,
                [name, display, desc, name],
            )
            cursor.execute(
                """
                INSERT INTO role_permissions (role_id, permission_id)
                SELECT roles.id, permissions.id
                FROM roles CROSS JOIN permissions
                WHERE LOWER(roles.name) = ANY(%s) AND permissions.name = %s
                ON CONFLICT (role_id, permission_id) DO NOTHING
                """,
                [list(ROLES), name],
            )

        for menu_name, link, icon in MENUS:
            cursor.execute(
                """
                INSERT INTO menus (menu_name, menu_link, menu_icon_css, menu_level,
                                   root_id, created_at, updated_at, is_active)
                SELECT %s, %s, %s, 1, root.id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, TRUE
                FROM root
                WHERE LOWER(root.root_name) = %s
                  AND NOT EXISTS (SELECT 1 FROM menus WHERE menu_link = %s)
                ORDER BY root.id
                LIMIT 1
                """,
                [menu_name, link, icon, ROOT_NAME, link],
            )
            cursor.execute(
                """
                INSERT INTO menu_permission (menu_id, permission_id)
                SELECT menus.id, permissions.id
                FROM menus CROSS JOIN permissions
                WHERE menus.menu_link = %s AND permissions.name = 'read-billing'
                ON CONFLICT (menu_id, permission_id) DO NOTHING
                """,
                [link],
            )


def unregister(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("DELETE FROM menus WHERE menu_link = ANY(%s)", [[link for _, link, _ in MENUS]])
        names = [name for name, _, _ in PERMISSIONS]
        cursor.execute(
            "DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE name = ANY(%s))",
            [names],
        )
        cursor.execute("DELETE FROM permissions WHERE name = ANY(%s)", [names])


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0001_initial"),
        ("AdminMenu", "0001_initial"),
        ("AdminPermission", "0001_initial"),
        ("AdminRole", "0001_initial"),
    ]

    operations = [migrations.RunPython(register, unregister)]

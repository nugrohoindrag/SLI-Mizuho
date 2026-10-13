from django.db import migrations


MENU_LINK = "/admin/pnbp-config/"


def register_menu(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO menus (
                menu_name, menu_link, menu_icon_css, menu_level,
                root_id, created_at, updated_at, is_active
            )
            SELECT
                'Konfigurasi PNBP', %s, 'fa-solid fa-money-bill-transfer', 1,
                root.id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, TRUE
            FROM root
            WHERE LOWER(root.root_name) = 'admin'
              AND NOT EXISTS (SELECT 1 FROM menus WHERE menu_link = %s)
            ORDER BY root.id
            LIMIT 1
            """,
            [MENU_LINK, MENU_LINK],
        )
        cursor.execute(
            """
            INSERT INTO menu_permission (menu_id, permission_id)
            SELECT menus.id, permissions.id
            FROM menus
            CROSS JOIN permissions
            WHERE menus.menu_link = %s
              AND permissions.name = 'read-settings'
            ON CONFLICT (menu_id, permission_id) DO NOTHING
            """,
            [MENU_LINK],
        )
        cursor.execute(
            """
            INSERT INTO role_permissions (role_id, permission_id)
            SELECT roles.id, permissions.id
            FROM roles
            CROSS JOIN permissions
            WHERE LOWER(roles.name) IN ('administrator', 'superadministrator')
              AND permissions.name = 'read-settings'
            ON CONFLICT (role_id, permission_id) DO NOTHING
            """
        )


def unregister_menu(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("DELETE FROM menus WHERE menu_link = %s", [MENU_LINK])


class Migration(migrations.Migration):
    dependencies = [
        ("AdminPnbp", "0001_initial"),
        ("AdminMenu", "0001_initial"),
        ("AdminPermission", "0001_initial"),
        ("AdminRole", "0001_initial"),
    ]

    operations = [migrations.RunPython(register_menu, unregister_menu)]

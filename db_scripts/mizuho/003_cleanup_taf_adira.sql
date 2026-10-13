-- Pembersihan sisa klien lain (requirement §13). Jalankan sekali di database Mizuho. Aman diulang.

BEGIN;

-- 1. *-adira_dashboard menjadi kunci tampil menu Dashboard untuk beberapa role.
--    Jangan dihapus: ganti nama saja (id tetap, sehingga role_permissions & menu_permission tidak berubah).
UPDATE permissions
   SET name         = replace(name, 'adira_dashboard', 'client_dashboard'),
       display_name = initcap(split_part(name, '-', 1)) || ' Client Dashboard',
       updated_at   = now()
 WHERE name LIKE '%-adira_dashboard';

-- 2. *-konsumen_adira tidak diberikan ke role mana pun; hanya tersisa di kolom lama menus.permission.
UPDATE menus
   SET permission = NULL
 WHERE permission IN (SELECT id FROM permissions WHERE name LIKE '%-konsumen_adira');
DELETE FROM menu_permission
 WHERE permission_id IN (SELECT id FROM permissions WHERE name LIKE '%-konsumen_adira');
DELETE FROM role_permissions
 WHERE permission_id IN (SELECT id FROM permissions WHERE name LIKE '%-konsumen_adira');
DELETE FROM permissions WHERE name LIKE '%-konsumen_adira';

-- 3. Penerima email tagihan Adira. Penerima tagihan Mizuho ditambahkan setelah alamatnya diketahui:
--    INSERT INTO common_codes (code_key, code_name, code_group) VALUES ('PE1', '<email>', 'PENERIMA_EMAIL_TAGIHAN');
DELETE FROM common_codes
 WHERE code_group = 'PENERIMA_EMAIL_TAGIHAN' AND code_name ILIKE '%@adira.co.id';

COMMIT;

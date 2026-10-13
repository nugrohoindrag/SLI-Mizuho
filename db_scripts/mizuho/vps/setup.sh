#!/usr/bin/env bash
# Setup / update instance Mizuho di VPS. Jalankan sebagai root:  bash setup.sh
# Seed DB (skema + master) hanya dimuat bila database masih kosong; file seed di /opt/efidusia-mizuho/seed/.
set -euo pipefail

BASE=/opt/efidusia-mizuho
REPO=https://github.com/nugrohoindrag/SLI-Mizuho.git
cd "$BASE"

# 1. kode
if [ -d app/.git ]; then git -C app pull --ff-only; else git clone --depth 1 "$REPO" app; fi

# 2. konfigurasi deploy dari repo (versi terbaru) + .env dengan secret acak (sekali saja)
cp app/db_scripts/mizuho/vps/{docker-compose.yml,nginx.conf,settings_vps.py} deploy/ 2>/dev/null || true
cd deploy
if [ ! -f .env ]; then
  sed -e "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -base64 48 | tr -d '\n/+=')|" \
      -e "s|^DB_PASSWORD=.*|DB_PASSWORD=$(openssl rand -hex 24)|" env.example > .env
  chmod 600 .env
fi
set -a; . ./.env; set +a

# 3. build & database
docker compose build web
docker compose up -d db
until docker compose exec -T db pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; do sleep 2; done

psql_db() { docker compose exec -T db psql -v ON_ERROR_STOP=1 -q -U "$DB_USER" -d "$DB_NAME" "$@"; }
TABLES=$(psql_db -At -c "select count(*) from information_schema.tables where table_schema='public'")
if [ "$TABLES" = "0" ]; then
  echo ">> seed database (skema + master)"
  psql_db < ../seed/01_schema.sql
  psql_db < ../seed/03_master.sql
  psql_db -c "\copy users from stdin csv header" < ../seed/02_users.csv
  psql_db -c "\copy role_user from stdin csv header" < ../seed/02_role_user.csv
  psql_db <<'SQL'
DO $$
DECLARE r record; mx bigint;
BEGIN
  FOR r IN SELECT c.table_name, c.column_name,
                  pg_get_serial_sequence(quote_ident(c.table_name), c.column_name) AS seq
           FROM information_schema.columns c
           WHERE c.table_schema = 'public'
             AND pg_get_serial_sequence(quote_ident(c.table_name), c.column_name) IS NOT NULL
  LOOP
    EXECUTE format('SELECT max(%I) FROM %I', r.column_name, r.table_name) INTO mx;
    IF mx IS NOT NULL THEN PERFORM setval(r.seq, mx); END IF;
  END LOOP;
END $$;
SQL
fi

# 4. migrate + konfigurasi Mizuho (semua idempoten)
docker compose run --rm web python manage.py migrate --noinput
for f in ../app/db_scripts/mizuho/0*.sql; do psql_db < "$f"; done

# 5. jalan
docker compose up -d
docker compose exec -T -u root web chown -R appuser:appuser /app/media /app/logs 2>/dev/null || true
docker compose ps

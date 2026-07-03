#!/bin/bash
# 00-a-create-role.sh — Complete DB init for local Docker Compose
#
# Creates the healthv10_app role, loads the Home Edition schema, and
# loads the app-role setup (grants + indexes).

set -e

echo "=== Pre-init: Creating healthv10_app role ==="

psql --username "$POSTGRES_USER" --dbname "healthv10" <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'healthv10_app') THEN
            CREATE ROLE healthv10_app WITH LOGIN PASSWORD 'Password2026';
            RAISE NOTICE 'Created healthv10_app role (placeholder)';
        END IF;
    END
    \$\$;
EOSQL

echo "=== Pre-init: Loading Home Edition schema ==="

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "healthv10" \
    -f /schema/02-home_schema.sql

echo "=== Pre-init: Loading app-role setup (grants + indexes) ==="

psql -v ON_ERROR_STOP=1 \
     -v app_db_password="${APP_DB_PASSWORD:-password}" \
     --username "$POSTGRES_USER" --dbname "healthv10" \
    -f /schema/03-app-role-setup.sql

echo "=== Pre-init: Schema and app role loaded ==="

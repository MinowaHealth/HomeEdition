#!/bin/bash
# local-init-db.sh — Post-init script for local Docker Compose
#
# Runs after the schema and app role are loaded by 00-a-create-role.sh.
# Mounted as 01-local-init.sh inside docker-entrypoint-initdb.d/.
#
# Purpose:
#   1. Reset healthv10_app password to match APP_DB_PASSWORD env var
#   2. Create test user (test@example.com / Password2026)

set -e

echo "=== Local init: setting up dev environment ==="

# Pre-computed Argon2id hash of 'Password2026'
TEST_USER_HASH='$argon2id$v=19$m=65536,t=3,p=4$OU5iYWEtkC7kROYlkpsM5g$IiDiazkVxuHnhEKAFJBnBQlMujfrx2crgTVOTABLXM0'
TEST_USER_ID='11111111-1111-1111-1111-111111111111'

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "healthv10" <<EOSQL

-- 1. Reset healthv10_app password
ALTER ROLE healthv10_app WITH PASSWORD '${APP_DB_PASSWORD}';

-- 2. Create test user (skip if already exists)
INSERT INTO users (tenant_id, id, email, display_name, password_hash, created_at, updated_at)
VALUES (1, '${TEST_USER_ID}', 'test@example.com', 'Test User', '${TEST_USER_HASH}', NOW(), NOW())
ON CONFLICT DO NOTHING;

INSERT INTO user_preferences (tenant_id, user_id, created_at, updated_at)
VALUES (1, '${TEST_USER_ID}', NOW(), NOW())
ON CONFLICT DO NOTHING;

EOSQL

echo "=== Local init complete ==="
echo "  User app:     test@example.com / Password2026"

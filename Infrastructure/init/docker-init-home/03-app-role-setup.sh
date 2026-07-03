#!/bin/bash
# Wrapper to pass APP_DB_PASSWORD env var into the app-role setup SQL.
# PostgreSQL's docker-entrypoint runs .sh files with bash and .sql files
# with psql directly — but .sql files can't read env vars.
# This wrapper uses psql -v to inject the password as a psql variable.

set -e

APP_DB_PWD="${APP_DB_PASSWORD:-password}"

echo "Setting up application role: healthv10_app, grants, indexes..."
psql -v ON_ERROR_STOP=1 \
     -v app_db_password="$APP_DB_PWD" \
     --username "$POSTGRES_USER" \
     --dbname healthv10 \
     -f /docker-entrypoint-initdb.d/role/app-role-setup.sql

echo "App role setup complete."

#!/bin/bash
# scripts/restore.sh — Restore a pg_dumpall backup to this system
#
# Works regardless of the current state: fresh system, existing data,
# botched prior restore. Nukes the data volume, starts a bare postgres
# (no init scripts), restores the dump, hands back to docker compose.
#
# Usage:
#   bash restore.sh /path/to/healthv10-YYYY-MM-DD.sql.gz
#
# The backup must have been created with:
#   bash backup.sh   (or: docker exec hb-local-postgres pg_dumpall -U postgres | gzip > file.gz)
#
# Stop application services first.

set -uo pipefail
# Note: NOT set -e — psql returns non-zero on harmless errors during
# restore (duplicate keys, already-exists). We check errors ourselves.

DUMP="${1:-}"
REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"

if [[ -z "$DUMP" ]]; then
    echo "Usage: bash restore.sh /path/to/backup.sql.gz"
    exit 1
fi

# Resolve to absolute path BEFORE we cd anywhere
DUMP="$(cd "$(dirname "$DUMP")" && pwd)/$(basename "$DUMP")"

if [[ ! -f "$DUMP" ]]; then
    echo "ERROR: File not found: $DUMP"
    exit 1
fi

# Single canonical appliance stack.
COMPOSE_FILE="$REPO_ROOT/HowToDeploy/docker-compose.local.yml"
ENV_FILE="$REPO_ROOT/local.env"
if [[ ! -f "$COMPOSE_FILE" ]]; then
    echo "ERROR: $COMPOSE_FILE not found"
    exit 1
fi

cd "$REPO_ROOT"
source "$ENV_FILE"
DC=(docker compose --project-directory . -f "$COMPOSE_FILE" --env-file "$ENV_FILE")
PG=hb-local-postgres

echo "=========================================="
echo "Full Database Restore"
echo "=========================================="
echo "  Backup: $DUMP"
echo "  This will DESTROY all existing data."
echo ""
read -rp "  Continue? [y/N] " confirm
[[ "${confirm}" =~ ^[Yy]$ ]] || exit 0
echo ""

# ── Step 1: Kill postgres, wipe the data volume ─────────────
echo "[1/5] Stopping $PG and wiping data volume..."
"${DC[@]}" stop "$PG" 2>/dev/null || true
docker rm -f "$PG" 2>/dev/null || true

# Volume name depends on compose project — find it dynamically
VOL=$(docker volume ls -q | grep "hb-local-pgdata" | head -1)
if [[ -n "$VOL" ]]; then
    docker volume rm "$VOL" || { echo "ERROR: Could not remove $VOL — is another container using it?"; exit 1; }
    echo "  Removed: $VOL"
else
    echo "  No existing volume found"
fi

# ── Step 2: Create the network + volume via compose ─────────
echo "[2/5] Ensuring Docker network and volume exist..."
"${DC[@]}" up --no-start "$PG" 2>/dev/null || true
docker rm -f "$PG" 2>/dev/null || true
# Compose created the network + a fresh empty volume. Get its name.
VOL=$(docker volume ls -q | grep "hb-local-pgdata" | head -1)
if [[ -z "$VOL" ]]; then
    echo "  ERROR: Volume not created"
    exit 1
fi
echo "  Volume: $VOL"

# ── Step 3: Start bare pgvector (no init scripts) ───────────
echo "[3/5] Starting bare PostgreSQL..."
# docker run directly — no POSTGRES_DB, no initdb mount.
# Entrypoint creates an empty cluster with just the postgres database.
docker run -d \
    --name pgvector-restore \
    -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
    -e POSTGRES_USER="${POSTGRES_USER:-postgres}" \
    -v "${VOL}:/var/lib/postgresql" \
    pgvector/pgvector:pg18 \
    > /dev/null

echo "  Waiting for PostgreSQL..."
for i in $(seq 1 30); do
    if docker exec pgvector-restore pg_isready -U postgres > /dev/null 2>&1; then
        echo "  Ready"
        break
    fi
    if [[ $i -eq 30 ]]; then
        echo "  ERROR: PostgreSQL did not start"
        docker logs pgvector-restore --tail 20
        exit 1
    fi
    sleep 2
done

# ── Step 4: Restore the dump ────────────────────────────────
echo "[4/5] Restoring (this may take a while)..."
ERRORS=$(mktemp)

if [[ "$DUMP" == *.gz ]]; then
    gunzip -c "$DUMP" | docker exec -i pgvector-restore psql -U postgres -o /dev/null 2>"$ERRORS" || true
else
    cat "$DUMP" | docker exec -i pgvector-restore psql -U postgres -o /dev/null 2>"$ERRORS" || true
fi

# Show only real errors (not harmless restore noise)
REAL_ERRORS=$(grep "^ERROR:" "$ERRORS" \
    | grep -v "already exists" \
    | grep -v "duplicate key" \
    | grep -v "current transaction is aborted" \
    | grep -v "role .* already exists" \
    || true)

if [[ -n "$REAL_ERRORS" ]]; then
    echo "  Errors during restore:"
    echo "$REAL_ERRORS" | head -20
    echo ""
    echo "  (Full error log: $ERRORS)"
else
    echo "  Clean restore — no errors"
    rm -f "$ERRORS"
fi

# Reset passwords to match THIS system's local.env
# (pg_dumpall carries the source system's passwords)
docker exec pgvector-restore psql -U postgres -c \
    "ALTER ROLE postgres WITH PASSWORD '${POSTGRES_PASSWORD}';" > /dev/null 2>&1 || true
docker exec pgvector-restore psql -U postgres -c \
    "ALTER ROLE healthv10_app WITH PASSWORD '${APP_DB_PASSWORD}';" > /dev/null 2>&1 || true
echo "  Passwords synced to local.env"

# ── Step 5: Hand off to docker compose ──────────────────────
echo "[5/5] Switching to docker compose..."
docker stop pgvector-restore > /dev/null 2>&1 || true
docker rm pgvector-restore > /dev/null 2>&1 || true

"${DC[@]}" up -d "$PG"
for i in $(seq 1 30); do
    STATUS=$(docker inspect "$PG" --format='{{.State.Health.Status}}' 2>/dev/null || echo "starting")
    if [[ "$STATUS" == "healthy" ]]; then
        echo "  Healthy"
        break
    fi
    if [[ $i -eq 30 ]]; then
        echo "  WARNING: healthcheck timed out"
    fi
    sleep 2
done

# ── Verify ──────────────────────────────────────────────────
echo ""
echo "=========================================="
echo "Verification"
echo "=========================================="
TABLES=$(docker exec "$PG" psql -U postgres -d healthv10 -tAc \
    "SELECT count(*) FROM pg_tables WHERE schemaname='public';" 2>/dev/null || echo "0")
USERS=$(docker exec "$PG" psql -U postgres -d healthv10 -tAc \
    "SELECT count(*) FROM users;" 2>/dev/null || echo "0")
ROLES=$(docker exec "$PG" psql -U postgres -tAc \
    "SELECT count(*) FROM pg_roles WHERE rolname='healthv10_app';" 2>/dev/null || echo "0")

echo "  Tables:   $TABLES"
echo "  Users:    $USERS"
echo "  App role: $([ "$ROLES" = "1" ] && echo "exists" || echo "MISSING")"
echo ""

if [[ "$TABLES" -gt 80 && "$ROLES" = "1" ]]; then
    echo "Restore successful. Start application services now."
else
    echo "WARNING: Check counts above."
fi

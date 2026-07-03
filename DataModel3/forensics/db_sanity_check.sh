#!/usr/bin/env bash
# db_sanity_check.sh — Post-restore sanity check for healthv10
#
# Usage:
#   ./DataModel3/forensics/db_sanity_check.sh                       # local Docker
#   ./DataModel3/forensics/db_sanity_check.sh remote user@home-box  # remote via ssh
#
# Date: 2026-04-13

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SQL_FILE="$SCRIPT_DIR/db_sanity_check.sql"

if [ ! -f "$SQL_FILE" ]; then
    echo "ERROR: $SQL_FILE not found"
    exit 1
fi

run_psql() {
    local db="$1"
    local sql="$2"
    if [ "${MODE:-local}" = "remote" ]; then
        echo "$sql" | ssh "${HOST}" "docker exec -i pgvector psql -U postgres -d $db"
    else
        echo "$sql" | docker exec -i pgvector psql -U postgres -d "$db"
    fi
}

run_psql_file() {
    local db="$1"
    local file="$2"
    if [ "${MODE:-local}" = "remote" ]; then
        cat "$file" | ssh "${HOST}" "docker exec -i pgvector psql -U postgres -d $db"
    else
        cat "$file" | docker exec -i pgvector psql -U postgres -d "$db"
    fi
}

MODE="${1:-local}"
HOST="${2:-}"

echo ""
echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║  STEP 1: CLUSTER OVERVIEW                                         ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""
echo "Mode: $MODE  Host: ${HOST:-localhost}"
echo ""

# Step 1: List databases
run_psql postgres "
SELECT datname AS database,
       pg_size_pretty(pg_database_size(datname)) AS size
FROM pg_database
WHERE NOT datistemplate
ORDER BY datname;
"

# Check if healthv10 exists
if ! run_psql postgres "SELECT 1 FROM pg_database WHERE datname = 'healthv10';" 2>/dev/null | grep -q "1"; then
    echo ""
    echo "╔══════════════════════════════════════════════════════════════════════╗"
    echo "║  ✗ DATABASE 'healthv10' DOES NOT EXIST                             ║"
    echo "║                                                                    ║"
    echo "║  The restore likely went into the wrong database.                  ║"
    echo "║  Check where tables landed:                                        ║"
    echo "║                                                                    ║"
    echo "║    docker exec -i pgvector psql -U postgres -d postgres \\          ║"
    echo "║      -c \"SELECT schemaname, COUNT(*) FROM pg_stat_user_tables      ║"
    echo "║          GROUP BY schemaname;\"                                     ║"
    echo "╚══════════════════════════════════════════════════════════════════════╝"
    echo ""

    # Also check the postgres database for misplaced tables
    echo "Checking 'postgres' database for misplaced tables..."
    run_psql postgres "
    SELECT schemaname, COUNT(*) AS tables
    FROM pg_stat_user_tables
    GROUP BY schemaname
    ORDER BY schemaname;
    "
    exit 1
fi

echo ""
echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║  STEP 2: HEALTHV10 DETAILED CHECK                                 ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"

# Step 2: Run the full sanity check SQL against healthv10
run_psql_file healthv10 "$SQL_FILE"

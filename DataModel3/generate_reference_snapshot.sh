#!/bin/bash
# generate_reference_snapshot.sh
#
# Applies the schema SQL source-of-truth to a throwaway pgvector container,
# runs export-schema-snapshot.sql against it, and writes the output to
# DataModel3/schema-reference.txt.
#
# The resulting reference snapshot is the "what prod should look like if it
# matches the schema SQL" artifact, intended to be diffed against a real prod
# snapshot by compare_full.py.
#
# Regenerate whenever 02-home_schema.sql, app-role-setup.sql, or
# export-schema-snapshot.sql changes. Commit the reference snapshot alongside.
#
# Requires: Docker with network access to pull pgvector/pgvector:pg18.
# Runtime: ~30s (container pull cached after first run).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SCHEMA_SQL="$REPO_ROOT/Infrastructure/init/docker-init-home/02-home_schema.sql"
ROLE_SQL="$REPO_ROOT/Infrastructure/init/docker-init-home/role/app-role-setup.sql"
SNAPSHOT_SQL="$SCRIPT_DIR/export-schema-snapshot.sql"
OUT="$SCRIPT_DIR/schema-reference.txt"

CONTAINER="schema-ref-$$"
IMAGE="pgvector/pgvector:pg18"

for f in "$SCHEMA_SQL" "$ROLE_SQL" "$SNAPSHOT_SQL"; do
    if [ ! -f "$f" ]; then
        echo "Missing required file: $f" >&2
        exit 2
    fi
done

cleanup() {
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[1/4] Pulling latest $IMAGE and starting throwaway Postgres as $CONTAINER"
docker pull "$IMAGE" >/dev/null
docker run -d --name "$CONTAINER" \
    -e POSTGRES_PASSWORD=refpw \
    -e POSTGRES_DB=healthv10 \
    "$IMAGE" >/dev/null

echo "[2/4] Waiting for Postgres readiness"
# pg_isready returns success as soon as the postmaster accepts connections,
# which can be before the entrypoint has finished creating the healthv10
# database from POSTGRES_DB. Verify the DB actually exists.
for _ in $(seq 1 60); do
    if docker exec "$CONTAINER" psql -U postgres -d healthv10 -c "SELECT 1" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
if ! docker exec "$CONTAINER" psql -U postgres -d healthv10 -c "SELECT 1" >/dev/null 2>&1; then
    echo "Database 'healthv10' not reachable within 60s" >&2
    docker logs "$CONTAINER" >&2 || true
    exit 3
fi

echo "[3/4] Applying schema + app role"
docker exec -i "$CONTAINER" psql -U postgres -d healthv10 -v ON_ERROR_STOP=1 -q \
    < "$SCHEMA_SQL" >/dev/null
docker exec -i "$CONTAINER" psql -U postgres -d healthv10 -v ON_ERROR_STOP=1 \
    -v app_db_password=refpw -q < "$ROLE_SQL" >/dev/null

echo "[4/4] Running snapshot SQL → $OUT"
docker exec -i "$CONTAINER" psql -U postgres -d healthv10 < "$SNAPSHOT_SQL" > "$OUT"

LINES=$(wc -l < "$OUT" | tr -d ' ')
echo "Done. Reference snapshot written to $OUT ($LINES lines)."

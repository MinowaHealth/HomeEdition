#!/bin/bash
set -e
# update.sh — Pull the latest code and rebuild the webapp on the appliance.
#
# Targets the single canonical appliance stack
# (HowToDeploy/docker-compose.local.yml). Run from anywhere in the repo:
#   cd UserApp && ./update.sh

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

DC=(docker compose --project-directory . -f HowToDeploy/docker-compose.local.yml --env-file local.env)

echo "=== Pulling latest release (main is always the most recent release) ==="
git pull --ff-only

# Bake the release version into the container as APP_VERSION
# (surfaced at /api/v1/healthz). VERSION at the repo root is the single
# source of truth and matches the database schema_version marker.
if [ -f VERSION ]; then
    APP_VERSION=$(cat VERSION)
else
    APP_VERSION=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
fi
export APP_VERSION
echo "Building with APP_VERSION=$APP_VERSION"

echo ""
echo "=== Rebuilding and restarting webapp + usermcp ==="
"${DC[@]}" build webapp usermcp
"${DC[@]}" up -d webapp usermcp

echo ""
echo "=== Post-update checks ==="
sleep 3
curl -s http://localhost/api/v1/healthz | python3 -c "import json,sys; d=json.load(sys.stdin); print('healthz: version=' + str(d.get('version')) + ' db_ok=' + str(d['checks']['database']['ok']))" \
    || echo "WARN: healthz not responding yet — check: ${DC[*]} logs --tail=30 webapp"

# Schema releases ship an idempotent apply script (see RELEASING.md).
# Re-running an already-applied one is safe.
if ls scripts/apply_*.py >/dev/null 2>&1; then
    echo ""
    echo "If this release changed the schema, run its apply script (idempotent):"
    ls scripts/apply_*.py | sed 's|^|  .venv/bin/python |'
fi
echo "Done."

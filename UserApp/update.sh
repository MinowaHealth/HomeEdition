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

echo "=== Pulling latest code ==="
git pull --ff-only

# Bake the current commit into the container as APP_VERSION
# (surfaced at /api/v1/healthz).
if APP_VERSION=$(git rev-parse --short HEAD 2>/dev/null); then
    export APP_VERSION
    echo "Building with APP_VERSION=$APP_VERSION"
else
    echo "Warning: could not read git SHA; building with APP_VERSION=unknown"
fi

echo ""
echo "=== Rebuilding and restarting webapp ==="
"${DC[@]}" build webapp
"${DC[@]}" up -d webapp
echo "Done."

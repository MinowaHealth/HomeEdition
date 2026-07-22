#!/bin/bash
set -e
# regen.sh — Rebuild and restart the webapp container after local code changes.
#
# Targets the single canonical appliance stack
# (HowToDeploy/docker-compose.local.yml). Works from anywhere in the repo.
#
# Usage: cd UserApp && ./regen.sh

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

DC=(docker compose --project-directory . -f HowToDeploy/docker-compose.local.yml --env-file local.env)

# Release version for /api/v1/healthz (repo-root VERSION file)
if [ -f VERSION ]; then
    APP_VERSION=$(cat VERSION)
    export APP_VERSION
fi

echo "=== regen: UserApp webapp ==="
echo "Rebuilding webapp..."
"${DC[@]}" stop webapp
"${DC[@]}" rm -f webapp
"${DC[@]}" build --no-cache webapp
"${DC[@]}" up -d --no-deps webapp

# Smoke test
echo ""
echo "Waiting for webapp to start..."
RETRIES=0
MAX_RETRIES=15
while [ $RETRIES -lt $MAX_RETRIES ]; do
    HTTP_CODE=$(curl -so /dev/null -w '%{http_code}' http://localhost:80/login 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" != "000" ]; then
        break
    fi
    RETRIES=$((RETRIES + 1))
    sleep 2
done

echo ""
echo "=== Post-restart status ==="
"${DC[@]}" ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
echo ""
if [ "$HTTP_CODE" = "200" ]; then
    echo "PASS: webapp responded with HTTP $HTTP_CODE"
elif [ "$HTTP_CODE" = "000" ]; then
    echo "FAIL: webapp not responding after 30s"
    echo "  Check logs: ${DC[*]} logs --tail=30 webapp"
    exit 1
else
    echo "WARN: webapp responded with HTTP $HTTP_CODE"
    echo "  Check logs: ${DC[*]} logs --tail=30 webapp"
fi

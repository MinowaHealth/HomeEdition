#!/bin/bash
set -e
# regen.sh — Rebuild and restart the UserMCP container after code changes.
#
# Targets the single canonical appliance stack
# (HowToDeploy/docker-compose.local.yml). Works from anywhere in the repo.
#
# Usage: cd UserMCP && ./regen.sh

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

DC=(docker compose --project-directory . -f HowToDeploy/docker-compose.local.yml --env-file local.env)

echo "=== regen: UserMCP ==="
echo "Rebuilding usermcp..."
"${DC[@]}" stop usermcp
"${DC[@]}" rm -f usermcp
"${DC[@]}" build --no-cache usermcp
"${DC[@]}" up -d --no-deps usermcp

# Smoke test
echo ""
echo "Waiting for usermcp to start..."
RETRIES=0
MAX_RETRIES=15
while [ $RETRIES -lt $MAX_RETRIES ]; do
    HTTP_CODE=$(curl -so /dev/null -w '%{http_code}' http://localhost:13282/health 2>/dev/null || echo "000")
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
    echo "PASS: usermcp responded with HTTP $HTTP_CODE"
elif [ "$HTTP_CODE" = "000" ]; then
    echo "FAIL: usermcp not responding after 30s"
    echo "  Check logs: ${DC[*]} logs --tail=30 usermcp"
    exit 1
else
    echo "WARN: usermcp responded with HTTP $HTTP_CODE"
    echo "  Check logs: ${DC[*]} logs --tail=30 usermcp"
fi

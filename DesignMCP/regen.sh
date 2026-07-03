#!/bin/bash
set -e
# regen.sh — Rebuild and restart the DesignMCP container after code changes.
#
# Usage: cd DesignMCP && ./regen.sh

echo "=== regen: DesignMCP ==="
echo "Rebuilding designmcp..."
docker compose stop designmcp
docker compose rm -f designmcp
docker compose build --no-cache designmcp
docker compose up -d --no-deps designmcp

# Smoke test
echo ""
echo "Waiting for designmcp to start..."
RETRIES=0
MAX_RETRIES=15
while [ $RETRIES -lt $MAX_RETRIES ]; do
    HTTP_CODE=$(curl -so /dev/null -w '%{http_code}' http://localhost:33282/health 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" != "000" ]; then
        break
    fi
    RETRIES=$((RETRIES + 1))
    sleep 2
done

echo ""
echo "=== Post-restart status ==="
docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
echo ""
if [ "$HTTP_CODE" = "200" ]; then
    echo "PASS: designmcp responded with HTTP $HTTP_CODE"
elif [ "$HTTP_CODE" = "000" ]; then
    echo "FAIL: designmcp not responding after 30s"
    echo "  Check logs: docker compose logs --tail=30 designmcp"
    exit 1
else
    echo "WARN: designmcp responded with HTTP $HTTP_CODE"
    echo "  Check logs: docker compose logs --tail=30 designmcp"
fi

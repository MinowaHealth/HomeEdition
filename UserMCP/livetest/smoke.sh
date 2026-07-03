#!/bin/bash
# UserMCP smoke test — exercises all 12 v0.5.0 tools against a live stack.
#
# Usage:
#   UserMCP/livetest/smoke.sh                      # default: rodrigo@borgia.family
#   UserMCP/livetest/smoke.sh lucrezia@borgia.family
#
# Env overrides:
#   USERAPP_URL    default http://localhost
#   MCP_URL        default http://localhost:13282
#   MCP_PASSWORD   default password (Mac-dev seed password)
#
# Exit codes:
#   0 — all tools OK (DEGRADED still counts as pass)
#   1 — one or more tools ERROR
#   2 — setup failure (couldn't log in or mint key)

set -eu

EMAIL="${1:-rodrigo@borgia.family}"
USERAPP_URL="${USERAPP_URL:-http://localhost}"
MCP_URL="${MCP_URL:-http://localhost:13282}"
MCP_PASSWORD="${MCP_PASSWORD:-password}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "UserMCP smoke test"
echo "  user:    $EMAIL"
echo "  userapp: $USERAPP_URL"
echo "  mcp:     $MCP_URL"
echo ""

# --- 1. Login to UserApp, get session token ---
LOGIN_JSON=$(curl -sf -X POST "$USERAPP_URL/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"$EMAIL\",\"password\":\"$MCP_PASSWORD\"}") \
    || { echo "FAIL: could not log in as $EMAIL at $USERAPP_URL"; exit 2; }

SESSION=$(echo "$LOGIN_JSON" | python3 -c "import sys,json;print(json.load(sys.stdin).get('token',''))")
if [ -z "$SESSION" ]; then
    echo "FAIL: login succeeded but no session token in response"; exit 2
fi

# --- 2. Mint a permanent API key for this run ---
KEY_JSON=$(curl -sf -X POST "$USERAPP_URL/api/v1/api-keys" \
    -H "Authorization: Bearer $SESSION" \
    -H "Content-Type: application/json" \
    -d "{\"label\":\"livetest-smoke-$(date +%s)\"}") \
    || { echo "FAIL: could not mint API key"; exit 2; }

MCP_API_KEY=$(echo "$KEY_JSON" | python3 -c "import sys,json;print(json.load(sys.stdin).get('key',''))")
KEY_ID=$(echo "$KEY_JSON" | python3 -c "import sys,json;print(json.load(sys.stdin).get('id',''))")

if [ -z "$MCP_API_KEY" ] || [ -z "$KEY_ID" ]; then
    echo "FAIL: API key response missing key/id. Body: $KEY_JSON"; exit 2
fi

cleanup() {
    curl -sf -X DELETE "$USERAPP_URL/api/v1/api-keys/$KEY_ID" \
        -H "Authorization: Bearer $MCP_API_KEY" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# --- 3. Run the tool sweep ---
export MCP_API_KEY MCP_URL
set +e
python3 "$SCRIPT_DIR/mcp_smoke.py"
RC=$?
set -e

exit $RC

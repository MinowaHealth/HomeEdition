#!/bin/bash
# Create a permanent API key for UserMCP and print Claude Desktop config.
#
# Usage:
#   ./get-token.sh                    # local (localhost)
#   ./get-token.sh http://host:port   # another LAN host

set -e

MODE="${1:-local}"

case "$MODE" in
    local)   BASE_URL="http://localhost" ;;
    http*)   BASE_URL="$MODE" ;;
    *)       echo "Usage: $0 [local|URL]"; exit 1 ;;
esac

# Prompt for credentials (defaults for dev)
read -p "Email [test@example.com]: " EMAIL
EMAIL="${EMAIL:-test@example.com}"
read -sp "Password [Password2026]: " PASSWORD
PASSWORD="${PASSWORD:-Password2026}"
echo ""

echo ""
echo "Logging into $BASE_URL ..."

RESPONSE=$(curl -s -f -X POST "$BASE_URL/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\": \"$EMAIL\", \"password\": \"$PASSWORD\"}") || {
    echo "Login failed — could not reach $BASE_URL/login"
    echo "Check that the webapp is running and the URL is correct."
    exit 1
}

# Extract session token from login response
SESSION=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('token',''))" 2>/dev/null) || true

if [ -z "$SESSION" ]; then
    echo "Login failed. Response:"
    echo "  $RESPONSE"
    exit 1
fi

echo "Login successful. Creating API key..."

# Create a permanent API key
KEY_RESPONSE=$(curl -s -f -X POST "$BASE_URL/api/v1/api-keys" \
    -H "Authorization: Bearer $SESSION" \
    -H "Content-Type: application/json" \
    -d '{"label": "Claude Desktop"}') || {
    echo "API key creation failed — could not reach $BASE_URL/api/v1/api-keys"
    exit 1
}

API_KEY=$(echo "$KEY_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('key',''))" 2>/dev/null) || true

if [ -z "$API_KEY" ]; then
    echo "API key creation failed. Response:"
    echo "  $KEY_RESPONSE"
    exit 1
fi

KEY_ID=$(echo "$KEY_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null)

echo ""
echo "API key created (does not expire):"
echo "  $API_KEY"
echo ""
echo "Key ID (for revocation): $KEY_ID"
echo "  Revoke with: curl -X DELETE $BASE_URL/api/v1/api-keys/$KEY_ID -H 'Authorization: Bearer $API_KEY'"
echo ""
echo "Claude Desktop config snippet — paste into mcpServers in:"
echo "  ~/Library/Application Support/Claude/claude_desktop_config.json"
echo ""

if [ "${MODE:0:4}" = "http" ]; then
    SSE_URL="${BASE_URL}/sse"
    NAME="usermcp-host"
else
    SSE_URL="http://localhost:13282/sse"
    NAME="usermcp-local"
fi

cat <<EOF
    "$NAME": {
        "command": "npx",
        "args": [
            "-y", "supergateway",
            "--sse", "$SSE_URL",
            "--header", "authorization:Bearer $API_KEY"
        ]
    }
EOF

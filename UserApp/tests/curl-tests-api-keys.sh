#!/bin/bash
# ============================================================================
# curl-tests-api-keys.sh - API Key CRUD + Auth tests for Minowa v10
# 2026-03-08 15:30 PST
#
# Usage:
#   ./tests/curl-tests-api-keys.sh                  # localhost
#   ./tests/curl-tests-api-keys.sh https://localhost
#
# Prerequisites:
#   - API running (docker compose up -d)
#   - Test user exists (auto-created by setup.sh)
#   - Migration applied: scripts/migrations/add_api_key_support.sql
# ============================================================================
set -euo pipefail

BASE="${1:-http://localhost:80}"
EMAIL="test@example.com"
PASSWORD="Password2026"
PASS=0
FAIL=0
FAILURES=""

# Colors
if [[ -z "${NO_COLOR:-}" ]]; then
    GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[0;33m'; CYAN='\033[0;36m'; NC='\033[0m'
else
    GREEN=''; RED=''; YELLOW=''; CYAN=''; NC=''
fi

# ---- Helpers ----------------------------------------------------------------

check_timed() {
    # check_timed <name> <expected_status> <max_seconds> <method> <path> [extra_curl_args...]
    local name="$1"; shift
    local expected="$1"; shift
    local max_time="$1"; shift
    local method="$1"; shift
    local path="$1"; shift

    local url="${BASE}${path}"
    local body time_total status

    # Build curl args
    local curl_args=(-s -w "\n%{http_code}\n%{time_total}" -X "$method")
    curl_args+=("$@")
    curl_args+=("$url")

    local raw
    raw=$(curl "${curl_args[@]}" 2>/dev/null) || true
    time_total=$(echo "$raw" | tail -1)
    status=$(echo "$raw" | tail -2 | head -1)
    body=$(echo "$raw" | sed -e '$d' -e '$d')

    # Check status
    local status_ok=false
    if [[ "$status" == "$expected" ]]; then
        status_ok=true
    fi

    # Check timing
    local time_ok=false
    if (( $(echo "$time_total < $max_time" | bc -l) )); then
        time_ok=true
    fi

    if $status_ok && $time_ok; then
        printf "${GREEN}  PASS${NC}  %-45s %s  %.3fs (< %.1fs)\n" "$name" "$status" "$time_total" "$max_time"
        PASS=$((PASS + 1))
    elif $status_ok; then
        printf "${YELLOW}  SLOW${NC}  %-45s %s  %.3fs (> %.1fs)\n" "$name" "$status" "$time_total" "$max_time"
        FAIL=$((FAIL + 1))
        FAILURES="${FAILURES}\n  - ${name}: ${time_total}s > ${max_time}s (SLOW)"
    else
        printf "${RED}  FAIL${NC}  %-45s got %s (expected %s)  %.3fs\n" "$name" "$status" "$expected" "$time_total"
        FAIL=$((FAIL + 1))
        FAILURES="${FAILURES}\n  - ${name}: got ${status}, expected ${expected}"
    fi

    # Export body for callers that need to parse the response
    LAST_BODY="$body"
}

extract_json() {
    # extract_json <json_string> <key>
    echo "$1" | python3 -c "import sys,json; print(json.load(sys.stdin).get('$2',''))" 2>/dev/null || echo ""
}

extract_json_array_field() {
    # extract_json_array_field <json_string> <index> <key>
    echo "$1" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[$2].get('$3','') if len(d)>$2 else '')" 2>/dev/null || echo ""
}

# ---- Login ------------------------------------------------------------------

echo ""
printf "${CYAN}== API Keys: Logging in as ${EMAIL} ==${NC}\n"

LOGIN_RESP=$(curl -s "${BASE}/api/v1/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASSWORD}\"}")

TOKEN=$(extract_json "$LOGIN_RESP" "token")

if [[ -z "$TOKEN" ]]; then
    printf "${RED}Login failed. Response: ${LOGIN_RESP}${NC}\n"
    echo "Make sure the API is running and test user exists (./tests/setup.sh)"
    exit 1
fi

printf "${GREEN}Got session token: ${TOKEN:0:12}...${NC}\n"

# ---- Create API Key ---------------------------------------------------------

echo ""
printf "${CYAN}== Create API Key ==${NC}\n"

check_timed "create_api_key" 201 2.0 POST /api/v1/api-keys \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{"label": "Test MCP Key"}'

API_KEY=$(extract_json "$LAST_BODY" "key")
API_KEY_ID=$(extract_json "$LAST_BODY" "id")
API_KEY_PREFIX=$(extract_json "$LAST_BODY" "key_prefix")

if [[ -z "$API_KEY" || ! "$API_KEY" == hbk_* ]]; then
    printf "${RED}Failed to create API key. Body: ${LAST_BODY}${NC}\n"
    exit 1
fi

printf "${GREEN}Created key: ${API_KEY_PREFIX}... (id: ${API_KEY_ID:0:8}...)${NC}\n"

# ---- List API Keys ----------------------------------------------------------

echo ""
printf "${CYAN}== List API Keys ==${NC}\n"

check_timed "list_api_keys" 200 0.5 GET /api/v1/api-keys \
    -H "Authorization: Bearer ${TOKEN}"

LIST_PREFIX=$(extract_json_array_field "$LAST_BODY" 0 "key_prefix")
if [[ "$LIST_PREFIX" != "$API_KEY_PREFIX" ]]; then
    printf "${RED}  List returned wrong prefix: ${LIST_PREFIX} (expected ${API_KEY_PREFIX})${NC}\n"
    FAIL=$((FAIL + 1))
    FAILURES="${FAILURES}\n  - list_prefix_mismatch: ${LIST_PREFIX} != ${API_KEY_PREFIX}"
else
    printf "${GREEN}  Verified prefix in list: ${LIST_PREFIX}${NC}\n"
fi

# ---- Authenticate with API Key ---------------------------------------------

echo ""
printf "${CYAN}== Auth with API Key ==${NC}\n"

check_timed "auth_with_api_key" 200 0.5 GET /api/v1/session \
    -H "Authorization: Bearer ${API_KEY}"

check_timed "auth_health_inputs" 200 1.0 GET /api/v1/health-inputs \
    -H "Authorization: Bearer ${API_KEY}"

check_timed "auth_stacks" 200 1.0 GET /api/v1/stacks \
    -H "Authorization: Bearer ${API_KEY}"

# ---- Invalid API Key --------------------------------------------------------

echo ""
printf "${CYAN}== Invalid API Key ==${NC}\n"

check_timed "invalid_key_rejected" 401 0.5 GET /api/v1/session \
    -H "Authorization: Bearer hbk_00000000000000000000000000000000"

check_timed "bad_prefix_rejected" 401 0.5 GET /api/v1/session \
    -H "Authorization: Bearer hbk_tooshort"

check_timed "no_auth_rejected" 401 0.5 GET /api/v1/session

# ---- Create up to limit ----------------------------------------------------

echo ""
printf "${CYAN}== Key Limit Enforcement ==${NC}\n"

# Create 4 more keys (we already have 1)
for i in 2 3 4 5; do
    check_timed "create_key_${i}" 201 2.0 POST /api/v1/api-keys \
        -H "Authorization: Bearer ${TOKEN}" \
        -H "Content-Type: application/json" \
        -d "{\"label\": \"Limit test key ${i}\"}"
done

# 6th key should fail with 409 (per-user key cap reached)
check_timed "create_key_over_limit" 409 1.0 POST /api/v1/api-keys \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{"label": "Should fail"}'

# ---- Revoke -----------------------------------------------------------------

echo ""
printf "${CYAN}== Revoke API Key ==${NC}\n"

check_timed "revoke_api_key" 200 1.0 DELETE "/api/v1/api-keys/${API_KEY_ID}" \
    -H "Authorization: Bearer ${TOKEN}"

# Verify revoked key no longer works
check_timed "revoked_key_rejected" 401 0.5 GET /api/v1/session \
    -H "Authorization: Bearer ${API_KEY}"

# Verify revoke of non-existent key
check_timed "revoke_missing_key" 404 0.5 DELETE "/api/v1/api-keys/00000000-0000-0000-0000-000000000000" \
    -H "Authorization: Bearer ${TOKEN}"

# ---- Cleanup: revoke remaining test keys ------------------------------------

echo ""
printf "${CYAN}== Cleanup ==${NC}\n"

KEYS_RESP=$(curl -s -H "Authorization: Bearer ${TOKEN}" "${BASE}/api/v1/api-keys")
KEY_IDS=$(echo "$KEYS_RESP" | python3 -c "
import sys, json
keys = json.load(sys.stdin)
for k in keys:
    if 'Limit test' in k.get('label', '') or 'Test MCP' in k.get('label', ''):
        print(k['id'])
" 2>/dev/null || echo "")

CLEANED=0
for kid in $KEY_IDS; do
    curl -s -X DELETE -H "Authorization: Bearer ${TOKEN}" "${BASE}/api/v1/api-keys/${kid}" > /dev/null 2>&1
    CLEANED=$((CLEANED + 1))
done
printf "${GREEN}  Cleaned up ${CLEANED} test keys${NC}\n"

# ---- Summary ----------------------------------------------------------------

echo ""
echo "============================================"
printf "  ${GREEN}PASS: ${PASS}${NC}  ${RED}FAIL: ${FAIL}${NC}\n"
if [[ -n "$FAILURES" ]]; then
    printf "\n${RED}Failures:${NC}"
    printf "$FAILURES\n"
fi
echo "============================================"

exit $FAIL

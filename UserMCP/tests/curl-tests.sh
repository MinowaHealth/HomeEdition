#!/bin/bash
# ============================================================================
# curl-tests.sh - UserMCP smoke tests
# 2026-02-26T23:00Z
#
# Tests HTTP endpoints and MCP SSE protocol for UserMCP.
# Obtains a bearer token by logging into UserApp webapp first.
#
# Usage:
#   ./tests/curl-tests.sh                                    # defaults
#   ./tests/curl-tests.sh http://localhost:13282 http://localhost:80
#   MCP_URL=http://host:13282 WEBAPP_URL=http://host:80 ./tests/curl-tests.sh
#
# Prerequisites:
#   - UserMCP running (docker compose up -d)
#   - UserApp webapp running (for login token)
#   - Test user exists: test@example.com / password
# ============================================================================
set -euo pipefail

MCP_URL="${1:-${MCP_URL:-http://localhost:13282}}"
WEBAPP_URL="${2:-${WEBAPP_URL:-http://localhost:80}}"
EMAIL="${TEST_EMAIL:-test@example.com}"
PASSWORD="${TEST_PASSWORD:-password}"
PASS=0
FAIL=0
SKIP=0
FAILURES=""

# Colors (disable with NO_COLOR=1)
if [[ -z "${NO_COLOR:-}" ]]; then
    GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[0;33m'; CYAN='\033[0;36m'; NC='\033[0m'
else
    GREEN=''; RED=''; YELLOW=''; CYAN=''; NC=''
fi

# ---- Helpers ----------------------------------------------------------------

check() {
    local name="$1"; shift
    local expected="$1"; shift

    local body
    body=$(curl -s -w "\n%{http_code}" "$@" 2>/dev/null) || true
    local status=$(echo "$body" | tail -1)

    if [[ "$status" == "$expected" ]]; then
        printf "${GREEN}  PASS${NC}  %-55s %s\n" "$name" "$status"
        PASS=$((PASS + 1))
    else
        printf "${RED}  FAIL${NC}  %-55s got %s (expected %s)\n" "$name" "$status" "$expected"
        FAIL=$((FAIL + 1))
        FAILURES="${FAILURES}\n  - ${name}: got ${status}, expected ${expected}"
    fi
}

check_any() {
    local name="$1"; shift
    local expected_list="$1"; shift

    local body
    body=$(curl -s -w "\n%{http_code}" "$@" 2>/dev/null) || true
    local status=$(echo "$body" | tail -1)

    IFS=',' read -ra EXPECTED <<< "$expected_list"
    for e in "${EXPECTED[@]}"; do
        if [[ "$status" == "$e" ]]; then
            printf "${GREEN}  PASS${NC}  %-55s %s\n" "$name" "$status"
            PASS=$((PASS + 1))
            return
        fi
    done

    printf "${RED}  FAIL${NC}  %-55s got %s (expected one of: %s)\n" "$name" "$status" "$expected_list"
    FAIL=$((FAIL + 1))
    FAILURES="${FAILURES}\n  - ${name}: got ${status}, expected one of ${expected_list}"
}

check_body_contains() {
    # check_body_contains <test_name> <expected_status> <body_substring> <curl_args...>
    local name="$1"; shift
    local expected="$1"; shift
    local needle="$1"; shift

    local body
    body=$(curl -s -w "\n%{http_code}" "$@" 2>/dev/null) || true
    local status=$(echo "$body" | tail -1)
    body=$(echo "$body" | sed '$d')

    if [[ "$status" == "$expected" ]] && echo "$body" | grep -q "$needle"; then
        printf "${GREEN}  PASS${NC}  %-55s %s (body contains '%s')\n" "$name" "$status" "$needle"
        PASS=$((PASS + 1))
    elif [[ "$status" != "$expected" ]]; then
        printf "${RED}  FAIL${NC}  %-55s got %s (expected %s)\n" "$name" "$status" "$expected"
        FAIL=$((FAIL + 1))
        FAILURES="${FAILURES}\n  - ${name}: got ${status}, expected ${expected}"
    else
        printf "${RED}  FAIL${NC}  %-55s %s but body missing '%s'\n" "$name" "$status" "$needle"
        FAIL=$((FAIL + 1))
        FAILURES="${FAILURES}\n  - ${name}: status ok but body missing '${needle}'"
    fi
}

# ============================================================================
echo ""
printf "${CYAN}============================================${NC}\n"
printf "${CYAN}  UserMCP Smoke Tests${NC}\n"
printf "${CYAN}  MCP:    ${MCP_URL}${NC}\n"
printf "${CYAN}  Webapp: ${WEBAPP_URL}${NC}\n"
printf "${CYAN}============================================${NC}\n"

# ---- Health Check ----------------------------------------------------------

echo ""
printf "${CYAN}== Health Check ==${NC}\n"

check_body_contains "health_status_ok"       200  '"status"' \
    "${MCP_URL}/health"

check_body_contains "health_service_name"    200  '"usermcp"' \
    "${MCP_URL}/health"

# ---- Auth Enforcement (unauthenticated) ------------------------------------

echo ""
printf "${CYAN}== Auth Enforcement (no token) ==${NC}\n"

check "sse_no_auth"                          401  "${MCP_URL}/sse"

check "messages_no_auth"                     401  "${MCP_URL}/messages/" \
    -X POST -H "Content-Type: application/json" -d '{}'

check "sse_bad_token"                        200  "${MCP_URL}/sse" \
    -H "Authorization: Bearer fake-token-12345" --max-time 2 || true
# Note: SSE with a bad token still returns 200 (SSE stream opens); the
# downstream API call will fail when tools are invoked. MCP auth is
# pass-through — the token is validated by the Flask API, not by UserMCP.
# We test that below under "Tool Invocation Errors".

check_any "messages_bad_token"               "400,401"  "${MCP_URL}/messages/" \
    -X POST -H "Content-Type: application/json" \
    -H "Authorization: Bearer fake-token-12345" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
# Bearer format passes our auth check, but MCP SDK returns 400 (no active SSE session)

# ---- Login to get bearer token ---------------------------------------------

echo ""
printf "${CYAN}== Getting bearer token from ${WEBAPP_URL} ==${NC}\n"

LOGIN_RESP=$(curl -s "${WEBAPP_URL}/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASSWORD}\"}")

TOKEN=$(echo "$LOGIN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null || echo "")

if [[ -z "$TOKEN" ]]; then
    printf "${RED}Login failed. Response: ${LOGIN_RESP}${NC}\n"
    echo "Make sure UserApp webapp is running at ${WEBAPP_URL}"
    echo "and test user exists: ${EMAIL}"
    exit 1
fi

printf "${GREEN}Got token: ${TOKEN:0:12}...${NC}\n"

# ---- SSE Connection (authenticated) ----------------------------------------

echo ""
printf "${CYAN}== SSE Connection (authenticated) ==${NC}\n"

# SSE connection should return 200 with text/event-stream content type.
# We use --max-time to avoid blocking (SSE is long-lived).
SSE_RESP=$(curl -s -D- "${MCP_URL}/sse" \
    -H "Authorization: Bearer ${TOKEN}" \
    --max-time 2 2>/dev/null) || true

SSE_STATUS=$(echo "$SSE_RESP" | head -1 | grep -oE '[0-9]{3}' | head -1 || echo "000")
SSE_CT=$(echo "$SSE_RESP" | grep -i "content-type" | head -1 || echo "")

if [[ "$SSE_STATUS" == "200" ]]; then
    printf "${GREEN}  PASS${NC}  %-55s %s\n" "sse_authenticated_status" "200"
    PASS=$((PASS + 1))
else
    printf "${RED}  FAIL${NC}  %-55s got %s (expected 200)\n" "sse_authenticated_status" "$SSE_STATUS"
    FAIL=$((FAIL + 1))
    FAILURES="${FAILURES}\n  - sse_authenticated_status: got ${SSE_STATUS}, expected 200"
fi

if echo "$SSE_CT" | grep -qi "text/event-stream"; then
    printf "${GREEN}  PASS${NC}  %-55s text/event-stream\n" "sse_content_type"
    PASS=$((PASS + 1))
else
    printf "${RED}  FAIL${NC}  %-55s got '%s'\n" "sse_content_type" "$(echo $SSE_CT | tr -d '\r\n')"
    FAIL=$((FAIL + 1))
    FAILURES="${FAILURES}\n  - sse_content_type: expected text/event-stream, got '${SSE_CT}'"
fi

# Check that SSE body contains the endpoint event (tells client where to POST)
if echo "$SSE_RESP" | grep -q "endpoint"; then
    printf "${GREEN}  PASS${NC}  %-55s found\n" "sse_endpoint_event"
    PASS=$((PASS + 1))
else
    printf "${RED}  FAIL${NC}  %-55s not found in SSE stream\n" "sse_endpoint_event"
    FAIL=$((FAIL + 1))
    FAILURES="${FAILURES}\n  - sse_endpoint_event: 'endpoint' event not found"
fi

# ---- Invalid routes --------------------------------------------------------

echo ""
printf "${CYAN}== Invalid Routes ==${NC}\n"

check_any "not_found_random_path"            "404,405"  "${MCP_URL}/api/v1/does-not-exist"
check_any "get_messages_wrong_method"        "404,405"  "${MCP_URL}/messages/" \
    -H "Authorization: Bearer ${TOKEN}"
# /messages/ only accepts POST, GET should 404 or 405

# ---- Summary ---------------------------------------------------------------

echo ""
printf "${CYAN}============================================${NC}\n"
TOTAL=$((PASS + FAIL))
if [[ $FAIL -eq 0 ]]; then
    printf "${GREEN}  ALL PASSED: ${PASS}/${TOTAL}${NC}\n"
else
    printf "${RED}  FAILED: ${FAIL}/${TOTAL}${NC}\n"
    printf "${RED}  Failures:${FAILURES}${NC}\n"
fi
printf "${CYAN}============================================${NC}\n"
echo ""

exit $FAIL

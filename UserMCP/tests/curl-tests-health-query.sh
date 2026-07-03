#!/bin/bash
# ============================================================================
# curl-tests-health-query.sh - Tests for health-query endpoint (Flask API)
# 2026-03-08 16:00 PST
#
# Tests the Flask API health-query endpoint that UserMCP routes through.
# This validates the backend, not the MCP protocol (SSE is hard to curl).
#
# Usage:
#   ./tests/curl-tests-health-query.sh                  # localhost
#   ./tests/curl-tests-health-query.sh https://localhost
#
# Prerequisites:
#   - Flask API running
#   - Test user exists with some health data
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
    local name="$1"; shift
    local expected="$1"; shift
    local max_time="$1"; shift
    local method="$1"; shift
    local path="$1"; shift

    local url="${BASE}${path}"
    local curl_args=(-s -w "\n%{http_code}\n%{time_total}" -X "$method")
    curl_args+=("$@")
    curl_args+=("$url")

    local raw
    raw=$(curl "${curl_args[@]}" 2>/dev/null) || true
    local time_total=$(echo "$raw" | tail -1)
    local status=$(echo "$raw" | tail -2 | head -1)
    LAST_BODY=$(echo "$raw" | sed -e '$d' -e '$d')

    local status_ok=false
    [[ "$status" == "$expected" ]] && status_ok=true

    local time_ok=false
    (( $(echo "$time_total < $max_time" | bc -l) )) && time_ok=true

    if $status_ok && $time_ok; then
        printf "${GREEN}  PASS${NC}  %-50s %s  %.3fs (< %.1fs)\n" "$name" "$status" "$time_total" "$max_time"
        PASS=$((PASS + 1))
    elif $status_ok; then
        printf "${YELLOW}  SLOW${NC}  %-50s %s  %.3fs (> %.1fs)\n" "$name" "$status" "$time_total" "$max_time"
        FAIL=$((FAIL + 1))
        FAILURES="${FAILURES}\n  - ${name}: ${time_total}s > ${max_time}s (SLOW)"
    else
        printf "${RED}  FAIL${NC}  %-50s got %s (expected %s)  %.3fs\n" "$name" "$status" "$expected" "$time_total"
        FAIL=$((FAIL + 1))
        FAILURES="${FAILURES}\n  - ${name}: got ${status}, expected ${expected}"
    fi
}

# ---- Login ------------------------------------------------------------------

echo ""
printf "${CYAN}== Health Query: Logging in as ${EMAIL} ==${NC}\n"

LOGIN_RESP=$(curl -s "${BASE}/api/v1/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASSWORD}\"}")

TOKEN=$(echo "$LOGIN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null || echo "")

if [[ -z "$TOKEN" ]]; then
    printf "${RED}Login failed. Response: ${LOGIN_RESP}${NC}\n"
    exit 1
fi

printf "${GREEN}Got token: ${TOKEN:0:12}...${NC}\n"

# Use recent date range (last 7 days) for queries
END_DATE=$(date +%Y-%m-%d)
START_DATE=$(date -v-7d +%Y-%m-%d 2>/dev/null || date -d '7 days ago' +%Y-%m-%d)

echo ""
printf "${CYAN}== Health Query: Date range ${START_DATE} to ${END_DATE} ==${NC}\n"

# ---- health-query with various kinds ----------------------------------------

echo ""
printf "${CYAN}== Health Query: All Kinds ==${NC}\n"

for kind in heart_rate steps food medication blood_pressure weight temperature blood_oxygen sleep blood_glucose respiratory_rate; do
    check_timed "hq_${kind}" 200 3.0 POST /api/v1/health-query \
        -H "Authorization: Bearer ${TOKEN}" \
        -H "Content-Type: application/json" \
        -d "{\"kind\":\"${kind}\",\"start\":\"${START_DATE}T00:00:00\",\"end\":\"${END_DATE}T23:59:59\"}"
done

# ---- Error cases -------------------------------------------------------------

echo ""
printf "${CYAN}== Health Query: Error Cases ==${NC}\n"

check_timed "hq_no_auth" 401 0.5 POST /api/v1/health-query \
    -H "Content-Type: application/json" \
    -d '{"kind":"heart_rate","start":"2026-02-20T00:00:00","end":"2026-02-25T23:59:59"}'

check_timed "hq_missing_kind" 400 0.5 POST /api/v1/health-query \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{"start":"2026-02-20T00:00:00","end":"2026-02-25T23:59:59"}'

check_timed "hq_invalid_kind" 400 0.5 POST /api/v1/health-query \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{"kind":"nonexistent","start":"2026-02-20T00:00:00","end":"2026-02-25T23:59:59"}'

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

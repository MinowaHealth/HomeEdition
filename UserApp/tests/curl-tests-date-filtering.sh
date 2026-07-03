#!/bin/bash
# ============================================================================
# curl-tests-date-filtering.sh - Date filtering tests for listing endpoints
# 2026-03-08 16:30 PST
#
# Tests optional start_date/end_date query params on listing endpoints.
#
# Usage:
#   ./tests/curl-tests-date-filtering.sh
#   ./tests/curl-tests-date-filtering.sh https://localhost
# ============================================================================
set -euo pipefail

BASE="${1:-http://localhost:80}"
EMAIL="test@example.com"
PASSWORD="Password2026"
PASS=0
FAIL=0
FAILURES=""

if [[ -z "${NO_COLOR:-}" ]]; then
    GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[0;33m'; CYAN='\033[0;36m'; NC='\033[0m'
else
    GREEN=''; RED=''; YELLOW=''; CYAN=''; NC=''
fi

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
printf "${CYAN}== Date Filtering: Logging in as ${EMAIL} ==${NC}\n"

LOGIN_RESP=$(curl -s "${BASE}/api/v1/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASSWORD}\"}")

TOKEN=$(echo "$LOGIN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null || echo "")

if [[ -z "$TOKEN" ]]; then
    printf "${RED}Login failed. Response: ${LOGIN_RESP}${NC}\n"
    exit 1
fi

printf "${GREEN}Got token: ${TOKEN:0:12}...${NC}\n"

# ---- health-input-log -------------------------------------------------------

echo ""
printf "${CYAN}== health-input-log date filtering ==${NC}\n"

check_timed "hil_no_dates" 200 1.0 GET /api/v1/health-input-log \
    -H "Authorization: Bearer ${TOKEN}"

check_timed "hil_with_dates" 200 1.0 GET \
    "/api/v1/health-input-log?start_date=2026-01-01&end_date=2026-01-31" \
    -H "Authorization: Bearer ${TOKEN}"

check_timed "hil_start_only" 200 1.0 GET \
    "/api/v1/health-input-log?start_date=2026-01-01" \
    -H "Authorization: Bearer ${TOKEN}"

check_timed "hil_bad_date" 400 0.5 GET \
    "/api/v1/health-input-log?start_date=not-a-date" \
    -H "Authorization: Bearer ${TOKEN}"

check_timed "hil_start_after_end" 400 0.5 GET \
    "/api/v1/health-input-log?start_date=2026-03-01&end_date=2026-01-01" \
    -H "Authorization: Bearer ${TOKEN}"

# ---- food-log ---------------------------------------------------------------

echo ""
printf "${CYAN}== food-log date filtering ==${NC}\n"

check_timed "food_no_dates" 200 1.0 GET /api/v1/food-log \
    -H "Authorization: Bearer ${TOKEN}"

check_timed "food_with_dates" 200 1.0 GET \
    "/api/v1/food-log?start_date=2026-01-01&end_date=2026-01-31" \
    -H "Authorization: Bearer ${TOKEN}"

check_timed "food_bad_date" 400 0.5 GET \
    "/api/v1/food-log?start_date=2026/01/01" \
    -H "Authorization: Bearer ${TOKEN}"

# ---- blood-pressure ---------------------------------------------------------

echo ""
printf "${CYAN}== blood-pressure date filtering ==${NC}\n"

check_timed "bp_no_dates" 200 1.0 GET /api/v1/blood-pressure \
    -H "Authorization: Bearer ${TOKEN}"

check_timed "bp_with_dates" 200 1.0 GET \
    "/api/v1/blood-pressure?start_date=2026-01-01&end_date=2026-01-31" \
    -H "Authorization: Bearer ${TOKEN}"

check_timed "bp_bad_date" 400 0.5 GET \
    "/api/v1/blood-pressure?end_date=invalid" \
    -H "Authorization: Bearer ${TOKEN}"

# ---- temperature ------------------------------------------------------------

echo ""
printf "${CYAN}== temperature date filtering ==${NC}\n"

check_timed "temp_no_dates" 200 1.0 GET /api/v1/temperature \
    -H "Authorization: Bearer ${TOKEN}"

check_timed "temp_with_dates" 200 1.0 GET \
    "/api/v1/temperature?start_date=2026-01-01&end_date=2026-01-31" \
    -H "Authorization: Bearer ${TOKEN}"

# ---- weight -----------------------------------------------------------------

echo ""
printf "${CYAN}== weight date filtering ==${NC}\n"

check_timed "weight_no_dates" 200 1.0 GET /api/v1/weight \
    -H "Authorization: Bearer ${TOKEN}"

check_timed "weight_with_dates" 200 1.0 GET \
    "/api/v1/weight?start_date=2026-01-01&end_date=2026-01-31" \
    -H "Authorization: Bearer ${TOKEN}"

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

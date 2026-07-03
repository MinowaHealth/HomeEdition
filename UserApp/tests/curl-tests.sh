#!/bin/bash
# ============================================================================
# curl-tests.sh - Simple API smoke tests for Minowa v10
# 2026-02-26 14:45 PST
#
# Usage:
#   ./tests/curl-tests.sh                  # run all tests against localhost
#   ./tests/curl-tests.sh https://localhost   # custom base URL
#   ./tests/curl-tests.sh localhost test_food  # run only tests matching pattern
#
# Prerequisites:
#   - API running (docker compose up -d)
#   - Test user exists (auto-created by setup.sh, or run ./tests/setup.sh)
# ============================================================================
set -euo pipefail

BASE="${1:-http://localhost:80}"
FILTER="${2:-}"
EMAIL="test@example.com"
PASSWORD="Password2026"
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
    # check <test_name> <expected_status> <curl_args...>
    local name="$1"; shift
    local expected="$1"; shift

    # Apply filter if set
    if [[ -n "$FILTER" && "$name" != *"$FILTER"* ]]; then
        SKIP=$((SKIP + 1))
        return
    fi

    local status
    local body
    body=$(curl -s -w "\n%{http_code}" "$@" 2>/dev/null) || true
    status=$(echo "$body" | tail -1)
    body=$(echo "$body" | sed '$d')

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
    # check_any <test_name> <expected1,expected2> <curl_args...>
    local name="$1"; shift
    local expected_list="$1"; shift

    if [[ -n "$FILTER" && "$name" != *"$FILTER"* ]]; then
        SKIP=$((SKIP + 1))
        return
    fi

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

# ---- Login ------------------------------------------------------------------

echo ""
printf "${CYAN}== Logging in as ${EMAIL} ==${NC}\n"

LOGIN_RESP=$(curl -s "${BASE}/api/v1/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASSWORD}\"}")

TOKEN=$(echo "$LOGIN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null || echo "")

if [[ -z "$TOKEN" ]]; then
    printf "${RED}Login failed. Response: ${LOGIN_RESP}${NC}\n"
    echo "Make sure the API is running and test user exists (./tests/setup.sh)"
    exit 1
fi

printf "${GREEN}Got token: ${TOKEN:0:12}...${NC}\n"
AUTH="-H \"Authorization: Bearer ${TOKEN}\""

# Shorthand for authenticated requests
A() { curl -s -H "Authorization: Bearer ${TOKEN}" "$@"; }
AJ() { curl -s -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" "$@"; }

# ---- Auth (no token required) -----------------------------------------------

echo ""
printf "${CYAN}== Auth: Unauthenticated ==${NC}\n"

check "unauth_health_inputs"         401  "${BASE}/api/v1/health-inputs"
check "unauth_stacks"                401  "${BASE}/api/v1/stacks"
check "unauth_food_items"            401  "${BASE}/api/v1/food-items"
check "unauth_session"               401  "${BASE}/api/v1/session"
check "invalid_bearer"               401  "${BASE}/api/v1/health-inputs" -H "Authorization: Bearer fake-token-12345"
check "malformed_auth_header"        401  "${BASE}/api/v1/health-inputs" -H "Authorization: InvalidScheme xyz"
check "empty_auth_header"            401  "${BASE}/api/v1/health-inputs" -H "Authorization: "
check "bad_login_creds"              401  "${BASE}/api/v1/login" -X POST -H "Content-Type: application/json" -d '{"email":"nobody@test.com","password":"wrong"}'
check "login_missing_fields"         400  "${BASE}/api/v1/login" -X POST -H "Content-Type: application/json" -d '{}'

# ---- Auth (token required) ---------------------------------------------------

echo ""
printf "${CYAN}== Auth: Authenticated ==${NC}\n"

check "get_session"                  200  "${BASE}/api/v1/session" -H "Authorization: Bearer ${TOKEN}"
check "get_config"                   200  "${BASE}/api/v1/config" -H "Authorization: Bearer ${TOKEN}"
check "get_mcp_config"               200  "${BASE}/api/v1/mcp-config" -H "Authorization: Bearer ${TOKEN}"
check "change_pw_missing_fields"     400  "${BASE}/api/v1/change-password" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" -d '{}'
check "change_pw_wrong_current"      401  "${BASE}/api/v1/change-password" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" -d '{"current_password":"WrongPass","new_password":"NewPass123!"}'

# ---- Health Inputs -----------------------------------------------------------

echo ""
printf "${CYAN}== Health Inputs ==${NC}\n"

check "list_health_inputs"           200  "${BASE}/api/v1/health-inputs" -H "Authorization: Bearer ${TOKEN}"
check "create_health_input"          201  "${BASE}/api/v1/health-inputs" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d '{"name":"Curl Test Vitamin","input_type":"supplement","default_dosage":"500mg"}'
check "create_hi_missing_name"       400  "${BASE}/api/v1/health-inputs" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d '{"input_type":"supplement"}'
check "create_hi_missing_type"       400  "${BASE}/api/v1/health-inputs" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d '{"name":"No Type"}'

# Create-then-delete test
HI_RESP=$(AJ "${BASE}/api/v1/health-inputs" -X POST -d '{"name":"Delete Me HI","input_type":"medication"}')
HI_ID=$(echo "$HI_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")
if [[ -n "$HI_ID" ]]; then
    check "delete_health_input"      200  "${BASE}/api/v1/health-inputs/${HI_ID}" -X DELETE -H "Authorization: Bearer ${TOKEN}"
else
    printf "${YELLOW}  SKIP${NC}  delete_health_input (create failed)\n"
    SKIP=$((SKIP + 1))
fi

# ---- Stacks ------------------------------------------------------------------

echo ""
printf "${CYAN}== Stacks ==${NC}\n"

check "list_stacks"                  200  "${BASE}/api/v1/stacks" -H "Authorization: Bearer ${TOKEN}"
check "create_stack"                 201  "${BASE}/api/v1/stacks" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d '{"name":"Curl Test Stack","description":"test"}'

# ---- Timeframes --------------------------------------------------------------

echo ""
printf "${CYAN}== Timeframes ==${NC}\n"

check "list_timeframes"              200  "${BASE}/api/v1/timeframes" -H "Authorization: Bearer ${TOKEN}"

# ---- Food Items --------------------------------------------------------------

echo ""
printf "${CYAN}== Food Items ==${NC}\n"

check "list_food_items"              200  "${BASE}/api/v1/food-items" -H "Authorization: Bearer ${TOKEN}"
check "create_food_item"             201  "${BASE}/api/v1/food-items" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d '{"name":"Curl Test Food","calories":150}'
check "create_food_missing_name"     400  "${BASE}/api/v1/food-items" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d '{"calories":100}'

# Create-then-delete
FI_RESP=$(AJ "${BASE}/api/v1/food-items" -X POST -d '{"name":"Delete Me Food","calories":50}')
FI_ID=$(echo "$FI_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")
if [[ -n "$FI_ID" ]]; then
    check "delete_food_item"         200  "${BASE}/api/v1/food-items/${FI_ID}" -X DELETE -H "Authorization: Bearer ${TOKEN}"
else
    printf "${YELLOW}  SKIP${NC}  delete_food_item (create failed)\n"
    SKIP=$((SKIP + 1))
fi

# ---- Meals -------------------------------------------------------------------

echo ""
printf "${CYAN}== Meals ==${NC}\n"

check "list_meals"                   200  "${BASE}/api/v1/meals" -H "Authorization: Bearer ${TOKEN}"
check "create_meal"                  201  "${BASE}/api/v1/meals" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d '{"name":"Curl Test Meal","description":"test"}'

# ---- Vitals ------------------------------------------------------------------

echo ""
printf "${CYAN}== Vitals ==${NC}\n"

NOW=$(python3 -c "from datetime import datetime; print(datetime.now().isoformat())")

check "list_blood_pressure"          200  "${BASE}/api/v1/blood-pressure" -H "Authorization: Bearer ${TOKEN}"
check_any "create_blood_pressure"    "200,201"  "${BASE}/api/v1/blood-pressure" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d "{\"systolic\":120,\"diastolic\":80,\"pulse\":72,\"timestamp\":\"${NOW}\"}"

check "list_weight"                  200  "${BASE}/api/v1/weight" -H "Authorization: Bearer ${TOKEN}"
check_any "create_weight"            "200,201"  "${BASE}/api/v1/weight" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d "{\"weight\":165.5,\"unit\":\"lbs\",\"timestamp\":\"${NOW}\"}"

check "list_temperature"             200  "${BASE}/api/v1/temperature" -H "Authorization: Bearer ${TOKEN}"
check_any "create_temperature"       "200,201"  "${BASE}/api/v1/temperature" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d "{\"temperature\":98.6,\"unit\":\"F\",\"timestamp\":\"${NOW}\"}"

check "list_observations"            200  "${BASE}/api/v1/observations" -H "Authorization: Bearer ${TOKEN}"

# ---- Logging -----------------------------------------------------------------

echo ""
printf "${CYAN}== Logging ==${NC}\n"

check "list_health_input_log"        200  "${BASE}/api/v1/health-input-log" -H "Authorization: Bearer ${TOKEN}"
check "list_all_logs"                200  "${BASE}/api/v1/all-logs" -H "Authorization: Bearer ${TOKEN}"
check "list_all_logs_dated"          200  "${BASE}/api/v1/all-logs?date=$(date +%Y-%m-%d)" -H "Authorization: Bearer ${TOKEN}"
check "list_food_log"                200  "${BASE}/api/v1/food-log" -H "Authorization: Bearer ${TOKEN}"
check "list_log_promotions"          200  "${BASE}/api/v1/log-promotions" -H "Authorization: Bearer ${TOKEN}"

# Freeform health input log (201 when freeform columns are migrated; 400 on legacy schema)
check_any "log_health_input_freeform" "201,400"  "${BASE}/api/v1/log-health-input" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d "{\"timestamp\":\"${NOW}\",\"free_text\":\"Ibuprofen\",\"free_dosage\":\"400mg\"}"

# Freeform food log (201 when freeform columns are migrated; 400 on legacy schema)
check_any "log_food_item_freeform" "201,400"  "${BASE}/api/v1/log-food-item" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d "{\"timestamp\":\"${NOW}\",\"free_text\":\"Grilled chicken with rice\"}"

# Log stack with non-existent UUID - app silently succeeds (logs nothing)
check "log_stack_bad_id"             404  "${BASE}/api/v1/log-stack" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d "{\"stack_id\":\"00000000-0000-0000-0000-000000000000\",\"timestamp\":\"${NOW}\"}"

# Log meal with non-existent UUID - app silently succeeds
check "log_meal_bad_id"              404  "${BASE}/api/v1/log-meal" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d "{\"meal_id\":\"00000000-0000-0000-0000-000000000000\",\"timestamp\":\"${NOW}\"}"

# Missing required fields
check "log_stack_no_stack_id"        400  "${BASE}/api/v1/log-stack" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d "{\"timestamp\":\"${NOW}\"}"

# ---- Analytics ---------------------------------------------------------------

echo ""
printf "${CYAN}== Analytics ==${NC}\n"

check "your_week"                    200  "${BASE}/api/v1/your-week" -H "Authorization: Bearer ${TOKEN}"
check "sleep_heatmap"                200  "${BASE}/api/v1/sleep-heatmap" -H "Authorization: Bearer ${TOKEN}"
check "stress_heatmap"               200  "${BASE}/api/v1/stress-heatmap" -H "Authorization: Bearer ${TOKEN}"
check "lab_results"                  200  "${BASE}/api/v1/lab-results" -H "Authorization: Bearer ${TOKEN}"
check "diagnostics_table_counts"     200  "${BASE}/api/v1/diagnostics/table-counts" -H "Authorization: Bearer ${TOKEN}"

# Health query
check "health_query_steps"           200  "${BASE}/api/v1/health-query" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d '{"kind":"steps","start":"2026-01-01T00:00:00","end":"2026-01-31T23:59:59"}'
check "health_query_heart_rate"      200  "${BASE}/api/v1/health-query" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d '{"kind":"heart_rate","start":"2026-01-01T00:00:00","end":"2026-01-31T23:59:59"}'
check "health_query_bp"              200  "${BASE}/api/v1/health-query" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d '{"kind":"blood_pressure","start":"2026-01-01T00:00:00","end":"2026-01-31T23:59:59"}'
check "health_query_food"            200  "${BASE}/api/v1/health-query" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d '{"kind":"food","start":"2026-01-01T00:00:00","end":"2026-01-31T23:59:59"}'
check "health_query_missing_kind"    400  "${BASE}/api/v1/health-query" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d '{}'
check "health_query_invalid_kind"    400  "${BASE}/api/v1/health-query" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d '{"kind":"invalid_type","start":"2026-01-01T00:00:00","end":"2026-01-31T23:59:59"}'

# ---- Integrations ------------------------------------------------------------

echo ""
printf "${CYAN}== Integrations ==${NC}\n"

check "garmin_status"                200  "${BASE}/api/v1/garmin/status" -H "Authorization: Bearer ${TOKEN}"
check "garmin_jobs"                  200  "${BASE}/api/v1/garmin/jobs" -H "Authorization: Bearer ${TOKEN}"
check "healthkit_jobs"               200  "${BASE}/api/v1/healthkit/jobs" -H "Authorization: Bearer ${TOKEN}"

# ---- Providers ---------------------------------------------------------------

echo ""
printf "${CYAN}== Providers ==${NC}\n"

check "list_providers"               200  "${BASE}/api/v1/providers" -H "Authorization: Bearer ${TOKEN}"
check "search_providers"             200  "${BASE}/api/v1/providers/available" -H "Authorization: Bearer ${TOKEN}"
check "grant_provider_no_id"         400  "${BASE}/api/v1/providers/grant" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d '{}'
check "revoke_provider_no_id"        400  "${BASE}/api/v1/providers/revoke" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d '{}'
check "get_provider_bad_id"          404  "${BASE}/api/v1/providers/00000000-0000-0000-0000-000000000000" -H "Authorization: Bearer ${TOKEN}"

# ---- 2FA (read-only, don't enable) ------------------------------------------

echo ""
printf "${CYAN}== 2FA (status only) ==${NC}\n"

check "2fa_status"                   200  "${BASE}/api/v1/2fa/status" -H "Authorization: Bearer ${TOKEN}"

# ---- Feedback ----------------------------------------------------------------

echo ""
printf "${CYAN}== Feedback ==${NC}\n"

check "list_feedback"                200  "${BASE}/api/v1/feedback" -H "Authorization: Bearer ${TOKEN}"
check "create_feedback"              201  "${BASE}/api/v1/feedback" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d '{"feedback":"Curl test feedback","feedback_type":"general"}'

# ============================================================================
# v2 API Routes — Parity + Embedding Tests
# ============================================================================

# ---- Ollama probe (skip server-side embedding tests if down) ----------------

OLLAMA_UP=0
OLLAMA_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 \
    "${BASE}/api/v2/semantic-search" \
    -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d '{"query":"probe","tables":["health_inputs"]}' 2>/dev/null) || true

if [[ "$OLLAMA_STATUS" == "200" ]]; then
    OLLAMA_UP=1
    printf "${GREEN}  Ollama reachable — running all v2 embedding tests${NC}\n"
else
    printf "${YELLOW}  Ollama unavailable (got $OLLAMA_STATUS) — skipping server-side embedding tests${NC}\n"
fi

# ---- v2 Auth (parity with v1) -----------------------------------------------

echo ""
printf "${CYAN}== v2: Auth ==${NC}\n"

check "v2_unauth_health_inputs"      401  "${BASE}/api/v2/health-inputs"
check "v2_unauth_food_items"         401  "${BASE}/api/v2/food-items"
check "v2_login"                     200  "${BASE}/api/v2/login" -X POST -H "Content-Type: application/json" \
    -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASSWORD}\"}"
check "v2_get_session"               200  "${BASE}/api/v2/session" -H "Authorization: Bearer ${TOKEN}"
check "v2_get_config"                200  "${BASE}/api/v2/config" -H "Authorization: Bearer ${TOKEN}"
check "v2_get_mcp_config"            200  "${BASE}/api/v2/mcp-config" -H "Authorization: Bearer ${TOKEN}"
check "v2_2fa_status"                200  "${BASE}/api/v2/2fa/status" -H "Authorization: Bearer ${TOKEN}"

# ---- v2 Health Inputs (parity) -----------------------------------------------

echo ""
printf "${CYAN}== v2: Health Inputs ==${NC}\n"

check "v2_list_health_inputs"        200  "${BASE}/api/v2/health-inputs" -H "Authorization: Bearer ${TOKEN}"

# v2 create without embedding (triggers server-side Ollama call)
if [[ "$OLLAMA_UP" -eq 1 ]]; then
    check "v2_create_health_input"       201  "${BASE}/api/v2/health-inputs" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
        -d '{"name":"v2 Test Vitamin","input_type":"supplement","default_dosage":"500mg"}'
else
    printf "${YELLOW}  SKIP${NC}  %-55s (no Ollama)\n" "v2_create_health_input"
    SKIP=$((SKIP + 1))
fi
# Validation tests don't trigger embedding (fail before that point)
check "v2_create_hi_missing_name"    400  "${BASE}/api/v2/health-inputs" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d '{"input_type":"supplement"}'
check "v2_create_hi_missing_type"    400  "${BASE}/api/v2/health-inputs" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d '{"name":"No Type"}'

# v2 create WITH client embedding (768-dim zero vector as placeholder)
ZERO_VEC=$(python3 -c "print('[' + ','.join(['0.0']*768) + ']')")
V2_HI_RESP=$(AJ "${BASE}/api/v2/health-inputs" -X POST \
    -d "{\"name\":\"v2 Embedded Vitamin\",\"input_type\":\"supplement\",\"embedding\":${ZERO_VEC}}")
V2_HI_ID=$(echo "$V2_HI_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || echo "")
V2_HI_EMBEDDED=$(echo "$V2_HI_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('embedded_by','none'))" 2>/dev/null || echo "none")

if [[ -n "$V2_HI_ID" ]]; then
    printf "${GREEN}  PASS${NC}  %-55s 201 embedded_by=%s\n" "v2_create_hi_with_embedding" "$V2_HI_EMBEDDED"
    PASS=$((PASS + 1))

    # v2 update with embedding
    check "v2_update_hi_with_embedding"  200  "${BASE}/api/v2/health-inputs/${V2_HI_ID}" -X PUT -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
        -d "{\"name\":\"v2 Updated Embedded\",\"input_type\":\"supplement\",\"embedding\":${ZERO_VEC}}"

    # Clean up
    check "v2_delete_health_input"       200  "${BASE}/api/v2/health-inputs/${V2_HI_ID}" -X DELETE -H "Authorization: Bearer ${TOKEN}"
else
    printf "${RED}  FAIL${NC}  %-55s create failed\n" "v2_create_hi_with_embedding"
    FAIL=$((FAIL + 1))
    FAILURES="${FAILURES}\n  - v2_create_hi_with_embedding: create failed"
fi

# ---- v2 Food Items (parity + embedding) --------------------------------------

echo ""
printf "${CYAN}== v2: Food Items ==${NC}\n"

check "v2_list_food_items"           200  "${BASE}/api/v2/food-items" -H "Authorization: Bearer ${TOKEN}"
if [[ "$OLLAMA_UP" -eq 1 ]]; then
    check "v2_create_food_item"          201  "${BASE}/api/v2/food-items" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
        -d '{"name":"v2 Test Food","calories":150}'
else
    printf "${YELLOW}  SKIP${NC}  %-55s (no Ollama)\n" "v2_create_food_item"
    SKIP=$((SKIP + 1))
fi
check "v2_create_food_missing_name"  400  "${BASE}/api/v2/food-items" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d '{"calories":100}'

# v2 create with client embedding
V2_FI_RESP=$(AJ "${BASE}/api/v2/food-items" -X POST \
    -d "{\"name\":\"v2 Embedded Food\",\"calories\":200,\"embedding\":${ZERO_VEC}}")
V2_FI_ID=$(echo "$V2_FI_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || echo "")
V2_FI_EMBEDDED=$(echo "$V2_FI_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('embedded_by','none'))" 2>/dev/null || echo "none")

if [[ -n "$V2_FI_ID" ]]; then
    printf "${GREEN}  PASS${NC}  %-55s 201 embedded_by=%s\n" "v2_create_food_with_embedding" "$V2_FI_EMBEDDED"
    PASS=$((PASS + 1))

    check "v2_update_food_with_embedding"  200  "${BASE}/api/v2/food-items/${V2_FI_ID}" -X PUT -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
        -d "{\"name\":\"v2 Updated Embedded Food\",\"calories\":250,\"embedding\":${ZERO_VEC}}"

    check "v2_delete_food_item"            200  "${BASE}/api/v2/food-items/${V2_FI_ID}" -X DELETE -H "Authorization: Bearer ${TOKEN}"
else
    printf "${RED}  FAIL${NC}  %-55s create failed\n" "v2_create_food_with_embedding"
    FAIL=$((FAIL + 1))
    FAILURES="${FAILURES}\n  - v2_create_food_with_embedding: create failed"
fi

# ---- v2 Vitals (parity + embedding) ------------------------------------------

echo ""
printf "${CYAN}== v2: Vitals ==${NC}\n"

check "v2_list_blood_pressure"       200  "${BASE}/api/v2/blood-pressure" -H "Authorization: Bearer ${TOKEN}"
check "v2_list_weight"               200  "${BASE}/api/v2/weight" -H "Authorization: Bearer ${TOKEN}"
check "v2_list_temperature"          200  "${BASE}/api/v2/temperature" -H "Authorization: Bearer ${TOKEN}"
check "v2_list_observations"         200  "${BASE}/api/v2/observations" -H "Authorization: Bearer ${TOKEN}"

# v2 observation with client embedding
V2NOW=$(python3 -c "from datetime import datetime; print(datetime.now().isoformat())")
V2_OBS_RESP=$(AJ "${BASE}/api/v2/observations" -X POST \
    -d "{\"observation\":\"v2 embedded test obs\",\"timestamp\":\"${V2NOW}\",\"embedding\":${ZERO_VEC}}")
V2_OBS_ID=$(echo "$V2_OBS_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || echo "")
V2_OBS_EMBEDDED=$(echo "$V2_OBS_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('embedded_by','none'))" 2>/dev/null || echo "none")

if [[ -n "$V2_OBS_ID" ]]; then
    printf "${GREEN}  PASS${NC}  %-55s 201 embedded_by=%s\n" "v2_create_obs_with_embedding" "$V2_OBS_EMBEDDED"
    PASS=$((PASS + 1))

    check "v2_update_obs_with_embedding"   200  "${BASE}/api/v2/observations/${V2_OBS_ID}" -X PUT -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
        -d "{\"observation\":\"v2 updated embedded obs\",\"timestamp\":\"${V2NOW}\",\"embedding\":${ZERO_VEC}}"

    check "v2_delete_observation"          200  "${BASE}/api/v2/observations/${V2_OBS_ID}" -X DELETE -H "Authorization: Bearer ${TOKEN}"
else
    printf "${RED}  FAIL${NC}  %-55s create failed\n" "v2_create_obs_with_embedding"
    FAIL=$((FAIL + 1))
    FAILURES="${FAILURES}\n  - v2_create_obs_with_embedding: create failed"
fi

# ---- v2 Thin Wrappers (parity) -----------------------------------------------

echo ""
printf "${CYAN}== v2: Stacks, Timeframes, Meals ==${NC}\n"

check "v2_list_stacks"               200  "${BASE}/api/v2/stacks" -H "Authorization: Bearer ${TOKEN}"
check "v2_list_timeframes"           200  "${BASE}/api/v2/timeframes" -H "Authorization: Bearer ${TOKEN}"
check "v2_list_meals"                200  "${BASE}/api/v2/meals" -H "Authorization: Bearer ${TOKEN}"

echo ""
printf "${CYAN}== v2: Logging ==${NC}\n"

check "v2_list_health_input_log"     200  "${BASE}/api/v2/health-input-log" -H "Authorization: Bearer ${TOKEN}"
check "v2_list_all_logs"             200  "${BASE}/api/v2/all-logs" -H "Authorization: Bearer ${TOKEN}"
check "v2_list_food_log"             200  "${BASE}/api/v2/food-log" -H "Authorization: Bearer ${TOKEN}"
check "v2_list_log_promotions"       200  "${BASE}/api/v2/log-promotions" -H "Authorization: Bearer ${TOKEN}"

echo ""
printf "${CYAN}== v2: Analytics ==${NC}\n"

check "v2_your_week"                 200  "${BASE}/api/v2/your-week" -H "Authorization: Bearer ${TOKEN}"
check "v2_sleep_heatmap"             200  "${BASE}/api/v2/sleep-heatmap" -H "Authorization: Bearer ${TOKEN}"
check "v2_stress_heatmap"            200  "${BASE}/api/v2/stress-heatmap" -H "Authorization: Bearer ${TOKEN}"
check "v2_lab_results"               200  "${BASE}/api/v2/lab-results" -H "Authorization: Bearer ${TOKEN}"
check "v2_table_counts"              200  "${BASE}/api/v2/diagnostics/table-counts" -H "Authorization: Bearer ${TOKEN}"

echo ""
printf "${CYAN}== v2: Integrations ==${NC}\n"

check "v2_garmin_status"             200  "${BASE}/api/v2/garmin/status" -H "Authorization: Bearer ${TOKEN}"
check "v2_garmin_jobs"               200  "${BASE}/api/v2/garmin/jobs" -H "Authorization: Bearer ${TOKEN}"
check "v2_healthkit_jobs"            200  "${BASE}/api/v2/healthkit/jobs" -H "Authorization: Bearer ${TOKEN}"

echo ""
printf "${CYAN}== v2: Providers ==${NC}\n"

check "v2_list_providers"            200  "${BASE}/api/v2/providers" -H "Authorization: Bearer ${TOKEN}"
check "v2_search_providers"          200  "${BASE}/api/v2/providers/available" -H "Authorization: Bearer ${TOKEN}"

echo ""
printf "${CYAN}== v2: Feedback ==${NC}\n"

check "v2_list_feedback"             200  "${BASE}/api/v2/feedback" -H "Authorization: Bearer ${TOKEN}"
check "v2_create_feedback"           201  "${BASE}/api/v2/feedback" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d '{"feedback":"v2 curl test feedback","feedback_type":"general"}'

echo ""
printf "${CYAN}== v2: Embeddings ==${NC}\n"

# Semantic search already probed at top of v2 section (Ollama check)
if [[ "$OLLAMA_UP" -eq 1 ]]; then
    check "v2_semantic_search"       200  "${BASE}/api/v2/semantic-search" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
        -d '{"query":"test search","tables":["health_inputs"]}'
else
    printf "${YELLOW}  SKIP${NC}  %-55s (no Ollama)\n" "v2_semantic_search"
    SKIP=$((SKIP + 1))
fi

# ---- v2: Mobile Events -------------------------------------------------------

echo ""
printf "${CYAN}== v2: Mobile Events ==${NC}\n"

check "v2_mobile_event_unauth"          401  "${BASE}/api/v2/mobile-events" \
    -X POST -H "Content-Type: application/json" \
    -d '{"event_text":"unauth test"}'

check "v2_mobile_event_missing_text"    400  "${BASE}/api/v2/mobile-events" \
    -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d '{"device_type":"ios 18.0","screen":"Today"}'

if [[ "$OLLAMA_UP" -eq 1 ]]; then
    ME_RESP=$(AJ "${BASE}/api/v2/mobile-events" -X POST \
        -d '{"event_text":"User tapped sync button","device_type":"ios 18.0","screen":"SettingsSync"}')
    ME_ID=$(echo "$ME_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || echo "")
    ME_EMB=$(echo "$ME_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('embedded_by','none'))" 2>/dev/null || echo "none")

    if [[ -n "$ME_ID" ]]; then
        printf "${GREEN}  PASS${NC}  %-55s 201 (embedded_by=%s)\n" "v2_create_mobile_event" "$ME_EMB"
        PASS=$((PASS + 1))
    else
        printf "${RED}  FAIL${NC}  %-55s create failed\n" "v2_create_mobile_event"
        FAIL=$((FAIL + 1))
        FAILURES="${FAILURES}\n  - v2_create_mobile_event: create failed"
    fi
else
    printf "${YELLOW}  SKIP${NC}  %-55s (no Ollama)\n" "v2_create_mobile_event"
    SKIP=$((SKIP + 1))
fi

# ---- Summary -----------------------------------------------------------------

echo ""
echo "============================================"
TOTAL=$((PASS + FAIL))
printf "  ${GREEN}PASS: ${PASS}${NC}  ${RED}FAIL: ${FAIL}${NC}  SKIP: ${SKIP}  TOTAL: ${TOTAL}\n"
if [[ -n "$FAILURES" ]]; then
    printf "\n${RED}Failures:${NC}"
    printf "$FAILURES\n"
fi
echo "============================================"

exit $FAIL


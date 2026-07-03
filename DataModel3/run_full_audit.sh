#!/usr/bin/env bash
# run_full_audit.sh — Orchestrator for the three-step database audit chain.
#
# Date: 2026-04-28
# Doctrine: DataModel3/CodeQueryAudit.md
#
# Runs the three deterministic audits in the documented order, captures each
# step's pass/fail, and prints a rollup table at the end. Exit code is non-zero
# if any step failed or could not run when it was supposed to.
#
# Usage:
#   ./DataModel3/run_full_audit.sh                    # skip step 1 (no snapshot)
#   ./DataModel3/run_full_audit.sh <prod-snapshot>    # full chain
#
# Pre-conditions:
#   - .venv exists at repo root (audit scripts are Python)
#   - Local Docker stack (pgvector) running on 127.0.0.1:5432, for step 1
#   - POSTGRES_PASSWORD in env, or readable from Infrastructure/.env, for step 1
#
# Each step's full output streams to stdout as it runs. The rollup table prints
# at the end so the verdict is at the bottom and per-step detail is above it.

set -uo pipefail

cd "$(dirname "$0")/.."

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
    echo "run_full_audit: $PY not found — set up the repo .venv first." >&2
    exit 2
fi

# Pull POSTGRES_PASSWORD from Infrastructure/.env if not already in env.
if [ -z "${POSTGRES_PASSWORD:-}" ] && [ -f Infrastructure/.env ]; then
    set -a
    # shellcheck disable=SC1091
    source Infrastructure/.env
    set +a
fi

PROD_SNAPSHOT="${1:-}"

step1_status="SKIP (no <prod-snapshot> arg)"
step2_status="?"
step3_status="?"

# ---- Step 1: schema-vs-prod drift ----
echo
echo "==== Step 1/4: compare_full.py (schema-vs-prod drift) ===="
if [ -n "$PROD_SNAPSHOT" ]; then
    if [ ! -f "$PROD_SNAPSHOT" ]; then
        echo "Snapshot not found: $PROD_SNAPSHOT" >&2
        step1_status="ERROR (snapshot not found)"
    elif "$PY" DataModel3/compare_full.py "$PROD_SNAPSHOT"; then
        step1_status="PASS"
    else
        step1_status="FAIL"
    fi
fi

# ---- Step 2: unused_columns_audit ----
echo
echo "==== Step 2/3: unused_columns_audit.py (schema -> code reachability) ===="
if "$PY" DataModel3/unused_columns_audit.py; then
    step2_status="PASS"
else
    step2_status="FAIL"
fi

# ---- Step 3: code_query_audit per service ----
echo
echo "==== Step 3/3: code_query_audit.py (code -> schema legality) ===="
SERVICES=(UserApp UserMCP)
failed_services=()
for svc in "${SERVICES[@]}"; do
    echo "  - $svc"
    if ! "$PY" DataModel3/code_query_audit.py --service "$svc" --json >/dev/null; then
        failed_services+=("$svc")
    fi
done
if [ ${#failed_services[@]} -eq 0 ]; then
    step3_status="PASS (${#SERVICES[@]} services)"
else
    step3_status="FAIL: ${failed_services[*]}"
fi

# ---- Rollup ----
echo
echo "============================================================"
echo "Three-step audit chain — rollup"
echo "============================================================"
printf '%-25s  %s\n' "Step" "Status"
printf '%-25s  %s\n' "----" "------"
printf '%-25s  %s\n' "1. compare_full"          "$step1_status"
printf '%-25s  %s\n' "2. unused_columns_audit"  "$step2_status"
printf '%-25s  %s\n' "3. code_query_audit"      "$step3_status"
echo

# ---- Exit code ----
exit_code=0
case "$step1_status" in FAIL*|ERROR*) exit_code=1 ;; esac
case "$step2_status" in FAIL*) exit_code=1 ;; esac
case "$step3_status" in FAIL*) exit_code=1 ;; esac

exit "$exit_code"

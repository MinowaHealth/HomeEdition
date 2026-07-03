#!/usr/bin/env bash
# Pre-commit wrapper for DataModel3/code_query_audit.py.
#
# Runs the Code SQL Usage Audit against every service and blocks the commit
# if any service has errors. Warnings and info findings do NOT block.
#
# Doctrine: DataModel3/CodeQueryAudit.md
# Reports:  DataModel3/CodeQueryAudit-<service>.md (generated on demand;
#           gitignored; this wrapper does not write them).

set -uo pipefail

# Run from repo root regardless of where pre-commit invoked us.
cd "$(dirname "$0")/.."

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
    echo "code-query-audit: $PY not found — set up the repo .venv first." >&2
    exit 2
fi

SERVICES=(UserApp UserMCP)

failed=()
for svc in "${SERVICES[@]}"; do
    if ! out=$("$PY" DataModel3/code_query_audit.py --service "$svc" --json 2>&1); then
        # Exit 1 = errors found; Exit 2 = setup error. Both fail the hook.
        errs=$(printf '%s' "$out" | grep -o '"errors": *[0-9]*' | grep -o '[0-9]*' | head -1)
        failed+=("${svc}=${errs:-?}")
    fi
done

if [ ${#failed[@]} -gt 0 ]; then
    echo
    echo "Code SQL Usage Audit — commit blocked"
    echo "-------------------------------------"
    for f in "${failed[@]}"; do
        echo "  $f errors"
    done
    echo
    echo "To see the offending callsites for a service, run:"
    echo "    .venv/bin/python DataModel3/code_query_audit.py --service <NAME>"
    echo
    echo "Doctrine: DataModel3/CodeQueryAudit.md"
    echo "Errors block commits. Warnings (e.g. SELECT *) do not."
    exit 1
fi

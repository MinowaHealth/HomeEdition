#!/usr/bin/env bash
# Six Schemathesis fuzz profiles for the UserApp API.
#
# Selection: $PROFILE  — A | B | C | E | F | G
# Ring:      $RING     — 1 (local, unbounded) | 2 (LAN host, bounded)
# Target:   $BASE_URL  — http://localhost  OR  http://<lan-host>
# Auth:     $FUZZ_KEY  — hbk_… (or empty for the unauthenticated profiles)
# Spec:     $SPEC      — defaults to ../../APIDocumentation/openapi.yaml
# Reports:  $REPORT_DIR — defaults to ./fuzz-reports/ring${RING}/${PROFILE}/
#
# Profile cheatsheet:
#   A  read-only smoke         unauth   GETs only
#   B  authenticated CRUD      auth     POST/PUT/PATCH/DELETE on stable surface
#   C  negative & boundary     auth     vitals POSTs only, max examples
#   E  stateful (OpenAPI links) auth    phases=stateful
#   F  destructive churn       auth     bulk POST/DELETE on fuzz user surface
#   G  stress-to-failure       auth     RING 1 ONLY — everything, unbounded
#
# Ring 1 (local) runs all six. Ring 2 (LAN host) runs A, B, C only.

set -euo pipefail

PROFILE="${PROFILE:?PROFILE required: A|B|C|E|F|G}"
RING="${RING:?RING required: 1 or 2}"
BASE_URL="${BASE_URL:?BASE_URL required}"
SPEC="${SPEC:-$(cd "$(dirname "$0")/../../APIDocumentation" && pwd)/openapi.yaml}"
REPORT_DIR="${REPORT_DIR:-$(pwd)/fuzz-reports/ring${RING}/${PROFILE}}"
FUZZ_KEY="${FUZZ_KEY:-}"

# Safety guard — this is a home/LAN tool; never fuzz a public host.
if [[ "$BASE_URL" == https://* && "$BASE_URL" != *"localhost"* ]]; then
  echo "ERROR: refusing to fuzz a public host ($BASE_URL). Home Edition is local-only." >&2
  exit 1
fi

if [[ "$RING" != "1" && "$RING" != "2" ]]; then
  echo "ERROR: RING must be 1 or 2 (got '$RING')" >&2
  exit 2
fi

if [[ "$RING" == "2" && ("$PROFILE" == "E" || "$PROFILE" == "F" || "$PROFILE" == "G") ]]; then
  echo "ERROR: profile $PROFILE is ring-1 only. Ring 2 runs A/B/C." >&2
  exit 2
fi

mkdir -p "$REPORT_DIR"

# Common args every profile uses. NOTE: --phases is intentionally NOT here;
# each profile picks its own. Profiles A/B/C/F/G use coverage+fuzzing
# (the property-based smoke); E uses stateful (it's the link-chain runner).
# Letting --phases default would run all four phases, and any profile whose
# method/path filters exclude all link-defining ops errors out with
# "Missing Open API links" — a Schemathesis v4 hard error.
COMMON_ARGS=(
  "$SPEC"
  "--url=$BASE_URL"
  "--header=User-Agent:HomeEdition-Fuzz/1.0"
  "--report=junit"
  "--report=ndjson"
  "--report-dir=$REPORT_DIR"
)

# Ring-2 client-side throttle keeps the fuzzer's load gentle on the
# appliance (the app itself does no rate limiting). Ring 1 sends unbounded
# load by design — the whole point is to see the failure modes.
if [[ "$RING" == "2" ]]; then
  COMMON_ARGS+=("--rate-limit=20/s")
fi

# Auth header for profiles that exercise authenticated endpoints.
auth_args() {
  if [[ -z "$FUZZ_KEY" ]]; then
    echo "ERROR: FUZZ_KEY env var required for profile $PROFILE (auth needed)." >&2
    exit 3
  fi
  printf -- "--header=Authorization:Bearer %s" "$FUZZ_KEY"
}

case "$PROFILE" in
  # ============================================================ Profile A
  # Read-only smoke. Unauthenticated GETs only. Catches 500-on-no-auth
  # (should be 401), response-shape drift, content-type drift.
  A)
    if [[ "$RING" == "1" ]]; then
      MAX_EXAMPLES=200; WORKERS=8
    else
      MAX_EXAMPLES=50;  WORKERS=4
    fi
    set -x
    schemathesis run "${COMMON_ARGS[@]}" \
      --include-method=GET \
      --phases=coverage,fuzzing \
      --max-examples="$MAX_EXAMPLES" \
      --workers="$WORKERS"
    ;;

  # ============================================================ Profile B
  # Authenticated CRUD across the stable mutation surface. Catches wrong-
  # field-name drift, status-code drift (500 vs 400/422), missing required
  # fields in 201 responses.
  B)
    if [[ "$RING" == "1" ]]; then
      MAX_EXAMPLES=500; WORKERS=8
    else
      MAX_EXAMPLES=100; WORKERS=4
    fi
    set -x
    schemathesis run "${COMMON_ARGS[@]}" \
      "$(auth_args)" \
      --exclude-method=GET \
      --include-path-regex='^/api/v1/(observations|health-inputs|blood-pressure|temperature|weight|health-metrics|api-keys)' \
      --phases=coverage,fuzzing \
      --max-examples="$MAX_EXAMPLES" \
      --workers="$WORKERS"
    ;;

  # ============================================================ Profile C
  # Negative & boundary. Vitals POSTs only — Hypothesis explores boundary
  # values on its own at high example counts. Catches systolic 39/40/41,
  # pulse 19/20/250/251, temp edges, type confusion.
  C)
    if [[ "$RING" == "1" ]]; then
      MAX_EXAMPLES=1000; WORKERS=8
    else
      MAX_EXAMPLES=200;  WORKERS=4
    fi
    set -x
    schemathesis run "${COMMON_ARGS[@]}" \
      "$(auth_args)" \
      --include-method=POST \
      --include-path-regex='^/api/v1/(blood-pressure|temperature|weight)$' \
      --phases=coverage,fuzzing \
      --max-examples="$MAX_EXAMPLES" \
      --workers="$WORKERS"
    ;;

  # ============================================================ Profile E
  # Stateful — drives the OpenAPI links we just added. Schemathesis
  # follows create→update→delete chains. Ring 1 only.
  E)
    set -x
    schemathesis run "${COMMON_ARGS[@]}" \
      "$(auth_args)" \
      --phases=stateful \
      --max-examples=500 \
      --workers=8
    ;;

  # ============================================================ Profile F
  # Destructive churn. Bulk POST then DELETE on the fuzz user. Surfaces
  # pgvector embedding daemon backpressure, statement-timeout 503s under
  # load, lock contention. Ring 1 only.
  F)
    set -x
    schemathesis run "${COMMON_ARGS[@]}" \
      "$(auth_args)" \
      --include-method=POST \
      --include-method=DELETE \
      --include-path-regex='^/api/v1/(observations|health-inputs)' \
      --phases=coverage,fuzzing \
      --max-examples=1000 \
      --workers=16
    ;;

  # ============================================================ Profile G
  # Stress-to-failure. Ring 1 ONLY. No rate cap, max workers, max examples.
  # Run monitor.sh in parallel to capture the failure modes; this script
  # only generates the load. Expect non-zero exit when something breaks —
  # the trap in the runbook converts that into a "what broke" report.
  G)
    set -x
    schemathesis run "${COMMON_ARGS[@]}" \
      "$(auth_args)" \
      --phases=coverage,fuzzing \
      --max-examples=10000 \
      --workers=64
    ;;

  *)
    echo "ERROR: unknown profile '$PROFILE' (use A|B|C|E|F|G)" >&2
    exit 2
    ;;
esac

#!/usr/bin/env bash
# Ring-1 instrumentation collector. Spawned in parallel with profile G
# (stress-to-failure). Samples docker stats, pg_stat_activity, disk,
# gunicorn thread count, and ERROR log lines at 1-second intervals into a
# timestamped artifact directory.
#
# Usage:
#   ./monitor.sh                                # default config, writes ./fuzz-failure-modes/<ts>/
#   OUT_DIR=/some/path ./monitor.sh             # explicit output directory
#   INTERVAL=2 ./monitor.sh                     # slower sampling
#   PG_CONTAINER=pgvector ./monitor.sh          # rename if compose chose a different container
#
# Stop with Ctrl-C or `kill $(cat $OUT_DIR/monitor.pid)`. The script flushes
# pending samples to disk on signal and writes a SUMMARY.md.
#
# Assumes the runner can shell into the host running the docker-compose stack
# (or is running on it). The PG_HOST env lets it be pointed at a remote host
# over the wire. (The app does no rate limiting, so there is no rate-limit
# store or container to probe.)

set -uo pipefail

OUT_DIR="${OUT_DIR:-$(pwd)/fuzz-failure-modes/$(date -u +%Y%m%dT%H%M%SZ)}"
INTERVAL="${INTERVAL:-1}"

# Docker container names — override if compose uses different names.
PG_CONTAINER="${PG_CONTAINER:-pgvector}"
WEBAPP_CONTAINER="${WEBAPP_CONTAINER:-webapp}"

# Direct-connection fallbacks if docker exec isn't available (e.g. remote
# monitor host). Empty means "use docker exec".
PG_HOST="${PG_HOST:-}"
PG_PORT="${PG_PORT:-5432}"
PG_USER="${PG_USER:-postgres}"
PG_DB="${PG_DB:-healthv10}"

mkdir -p "$OUT_DIR"
echo $$ > "$OUT_DIR/monitor.pid"

# --- helpers ----------------------------------------------------------------

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }

pg_exec() {
  # $1 = SQL. Falls back to direct psql if PG_HOST is set.
  if [[ -n "$PG_HOST" ]]; then
    PGPASSWORD="${PG_PASSWORD:-}" psql -h "$PG_HOST" -p "$PG_PORT" \
      -U "$PG_USER" -d "$PG_DB" -At -c "$1" 2>/dev/null
  else
    docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -At -c "$1" 2>/dev/null
  fi
}

# --- sample collectors ------------------------------------------------------
# Each writes one row per sample to its own TSV. Header on first line.

declare -A FILES=(
  [docker_stats]="$OUT_DIR/docker_stats.tsv"
  [pg_conns]="$OUT_DIR/pg_connections.tsv"
  [pg_locks]="$OUT_DIR/pg_locks.tsv"
  [sessions]="$OUT_DIR/sessions_count.tsv"
  [disk]="$OUT_DIR/disk.tsv"
  [webapp_threads]="$OUT_DIR/webapp_threads.tsv"
  [errors]="$OUT_DIR/error_log_tail.txt"
)

printf "timestamp\tcontainer\tcpu_pct\tmem_usage\tmem_pct\tpids\n" > "${FILES[docker_stats]}"
printf "timestamp\tactive\twaiting\ttotal\n" > "${FILES[pg_conns]}"
printf "timestamp\tlock_count\tlocks_over_100ms\n" > "${FILES[pg_locks]}"
printf "timestamp\tsessions\n" > "${FILES[sessions]}"
printf "timestamp\tfilesystem\tuse_pct\tavail\n" > "${FILES[disk]}"
printf "timestamp\tworker_pid\tthread_count\n" > "${FILES[webapp_threads]}"

sample_docker() {
  local ts="$1"
  docker stats --no-stream --format \
    '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.PIDs}}' \
    2>/dev/null | while IFS=$'\t' read -r name cpu memu memp pids; do
      printf "%s\t%s\t%s\t%s\t%s\t%s\n" "$ts" "$name" "$cpu" "$memu" "$memp" "$pids" \
        >> "${FILES[docker_stats]}"
    done
}

sample_pg_conns() {
  local ts="$1"
  local active waiting total
  active=$(pg_exec "SELECT count(*) FROM pg_stat_activity WHERE state='active';")
  waiting=$(pg_exec "SELECT count(*) FROM pg_stat_activity WHERE wait_event IS NOT NULL;")
  total=$(pg_exec "SELECT count(*) FROM pg_stat_activity;")
  printf "%s\t%s\t%s\t%s\n" "$ts" "${active:-0}" "${waiting:-0}" "${total:-0}" \
    >> "${FILES[pg_conns]}"
}

sample_pg_locks() {
  local ts="$1"
  local lock_count slow_locks
  lock_count=$(pg_exec "SELECT count(*) FROM pg_locks WHERE NOT granted;")
  slow_locks=$(pg_exec "
    SELECT count(*) FROM pg_stat_activity
    WHERE wait_event_type='Lock'
      AND state='active'
      AND now() - state_change > interval '100 milliseconds';
  ")
  printf "%s\t%s\t%s\n" "$ts" "${lock_count:-0}" "${slow_locks:-0}" \
    >> "${FILES[pg_locks]}"
}

sample_sessions() {
  local ts="$1"
  local count
  count=$(pg_exec "SELECT count(*) FROM sessions;")
  printf "%s\t%s\n" "$ts" "${count:-0}" >> "${FILES[sessions]}"
}

sample_disk() {
  local ts="$1"
  # Host disk — only useful if the monitor is on the same host as the docker
  # data root. If running remote, this captures the monitor host's disk
  # which is usually not what we want; flag the caveat in the SUMMARY.
  df -h --output=source,pcent,avail / /var/lib/docker 2>/dev/null \
    | tail -n +2 | while read -r fs pct avail; do
        printf "%s\t%s\t%s\t%s\n" "$ts" "$fs" "$pct" "$avail" >> "${FILES[disk]}"
      done
}

sample_threads() {
  local ts="$1"
  # Count Linux tasks (kernel-level threads) per gunicorn worker pid inside
  # the webapp container. The pgvector observation-embed daemon spawns one
  # daemon thread per POST /observations — this is the leak detector.
  docker exec "$WEBAPP_CONTAINER" sh -c '
    for pid in $(pgrep -f "gunicorn: worker"); do
      n=$(ls /proc/$pid/task 2>/dev/null | wc -l)
      echo "${pid}\t${n}"
    done
  ' 2>/dev/null | while IFS=$'\t' read -r pid n; do
      printf "%s\t%s\t%s\n" "$ts" "$pid" "$n" >> "${FILES[webapp_threads]}"
    done
}

tail_errors() {
  # Background tail of docker logs filtered to ERROR/CRITICAL lines. Started
  # once; runs for the lifetime of the monitor. Output is timestamped on
  # ingest so we don't depend on the app's log format.
  (
    docker logs -f --since 0s "$WEBAPP_CONTAINER" 2>&1 \
      | grep --line-buffered -E 'ERROR|CRITICAL|Traceback|QUERY_TIMEOUT' \
      | while IFS= read -r line; do
          printf "[%s] %s\n" "$(stamp)" "$line"
        done
  ) >> "${FILES[errors]}" &
  echo $! > "$OUT_DIR/errors_tail.pid"
}

# --- shutdown handler ------------------------------------------------------

write_summary() {
  local end_ts; end_ts=$(stamp)
  cat > "$OUT_DIR/SUMMARY.md" <<EOF
# Fuzz Failure-Mode Capture — ${end_ts}

**Target**: \`${WEBAPP_CONTAINER}\` (webapp), \`${PG_CONTAINER}\` (Postgres)
**Sample interval**: ${INTERVAL}s
**Started**: $(head -1 "${FILES[pg_conns]}" 2>/dev/null && tail -1 "${FILES[pg_conns]}" | awk '{print $1}' || echo "n/a")
**Ended**: ${end_ts}

## Captured artifacts

| File | Purpose | Lines |
|---|---|---|
| \`docker_stats.tsv\` | CPU / mem / PIDs per container | $(wc -l < "${FILES[docker_stats]}" 2>/dev/null || echo 0) |
| \`pg_connections.tsv\` | Active / waiting / total pg_stat_activity | $(wc -l < "${FILES[pg_conns]}" 2>/dev/null || echo 0) |
| \`pg_locks.tsv\` | Lock counts and >100ms lock waits | $(wc -l < "${FILES[pg_locks]}" 2>/dev/null || echo 0) |
| \`sessions_count.tsv\` | \`sessions\` table row count over time | $(wc -l < "${FILES[sessions]}" 2>/dev/null || echo 0) |
| \`disk.tsv\` | Disk pct / available on / and /var/lib/docker | $(wc -l < "${FILES[disk]}" 2>/dev/null || echo 0) |
| \`webapp_threads.tsv\` | Linux task count per gunicorn worker | $(wc -l < "${FILES[webapp_threads]}" 2>/dev/null || echo 0) |
| \`error_log_tail.txt\` | webapp ERROR/CRITICAL/Traceback lines | $(wc -l < "${FILES[errors]}" 2>/dev/null || echo 0) |

## Quick-look failure indicators

$(if [[ -s "${FILES[errors]}" ]]; then
    echo "- **ERROR log lines captured**: $(wc -l < "${FILES[errors]}")"
  else
    echo "- No ERROR/CRITICAL/Traceback lines during run."
  fi)

$(awk -F'\t' 'NR>1 && $4 > peak { peak=$4 } END { if (peak>0) print "- Peak pg_stat_activity row count: " peak }' "${FILES[pg_conns]}" 2>/dev/null)

$(awk -F'\t' 'NR>1 && $2+0 > peak { peak=$2+0 } END { if (peak>0) printf "- Peak sessions count: %d\n", peak }' "${FILES[sessions]}" 2>/dev/null)

$(awk -F'\t' 'NR>1 && $3+0 > peak { peak=$3+0 } END { if (peak>0) printf "- Peak gunicorn worker thread count: %d\n", peak }' "${FILES[webapp_threads]}" 2>/dev/null)

## Interpretation pointers

- **Thread count growing monotonically**: pgvector observation-embed daemons not draining. See plan failure mode #4.
- **pg_stat_activity approaching max_connections**: pool exhaustion. Failure mode #2.
- **slow_locks > 0 sustained**: lock contention. Failure mode #10.
- **sessions count growing unbounded**: session-table bloat. Failure mode #6.
- **disk Use% approaching 100 on /var/lib/docker**: log / embedding fill. Failure mode #7.

Cross-reference against \`fuzz-reports/ring1/G/\` for the request-level view.
EOF
}

cleanup() {
  echo
  echo "[monitor] stopping (caught signal)" >&2
  if [[ -f "$OUT_DIR/errors_tail.pid" ]]; then
    kill "$(cat "$OUT_DIR/errors_tail.pid")" 2>/dev/null || true
  fi
  write_summary
  echo "[monitor] artifacts: $OUT_DIR" >&2
  exit 0
}

trap cleanup INT TERM EXIT

# --- main loop -------------------------------------------------------------

echo "[monitor] writing to $OUT_DIR (sample interval ${INTERVAL}s, Ctrl-C to stop)" >&2

tail_errors

while true; do
  ts=$(stamp)
  sample_docker      "$ts"
  sample_pg_conns    "$ts"
  sample_pg_locks    "$ts"
  sample_sessions    "$ts"
  sample_disk        "$ts"
  sample_threads     "$ts"
  sleep "$INTERVAL"
done

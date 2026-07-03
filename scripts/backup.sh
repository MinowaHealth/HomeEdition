#!/bin/bash
# scripts/backup.sh — Full PostgreSQL cluster backup
#
# Dumps everything: roles, databases, schema, data, RLS policies, grants.
# Run on any machine with access to the pgvector container.
#
# Usage:
#   bash backup.sh                    # writes to ~/healthv10-YYYY-MM-DD.sql.gz
#   bash backup.sh /path/to/out.gz    # writes to specified path

set -euo pipefail

OUT="${1:-$HOME/healthv10-$(date +%Y-%m-%d).sql.gz}"

echo "Backing up entire PostgreSQL cluster..."
docker exec pgvector pg_dumpall -U postgres | gzip > "$OUT"

SIZE=$(du -h "$OUT" | cut -f1)
echo "Done: $OUT ($SIZE)"

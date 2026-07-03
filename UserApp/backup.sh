#!/bin/bash
#
# backup.sh - Backup healthv10 database
#
# Creates timestamped SQL dumps in ./backups/
#

set -e

BACKUP_DIR="./backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/healthv10_${TIMESTAMP}.sql"

# Create backup directory if needed
mkdir -p "$BACKUP_DIR"

echo "Backing up healthv10 database..."

# Dump database using docker
docker exec pgvector pg_dump -U postgres healthv10 > "$BACKUP_FILE"

# Compress the backup
gzip "$BACKUP_FILE"

echo "Backup created: ${BACKUP_FILE}.gz"

# Show backup size
ls -lh "${BACKUP_FILE}.gz"

# List recent backups
echo ""
echo "Recent backups:"
ls -lt "$BACKUP_DIR"/*.gz 2>/dev/null | head -5 || echo "  (none)"

echo ""
echo "To restore: gunzip -c ${BACKUP_FILE}.gz | docker exec -i pgvector psql -U postgres healthv10"

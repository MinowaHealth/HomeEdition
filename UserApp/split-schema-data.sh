#!/bin/bash
set -e
# Split schema SQL file into schema-only and data-only versions
# Usage: ./split-schema-data.sh

INPUT_FILE="../Infrastructure/init/docker-init-home/02-home_schema.sql"
SCHEMA_FILE="../Infrastructure/init/docker-init-home/02-home_schema.sql.schema-only"
DATA_FILE="../Infrastructure/init/docker-init-home/02-home_data.sql"

echo "Splitting $INPUT_FILE..."

# Extract everything EXCEPT COPY sections (schema only)
# Skip from COPY lines to their \. end marker
awk '
  /^COPY / { in_copy = 1; next }
  /^\\.$/ && in_copy { in_copy = 0; next }
  !in_copy { print }
' "$INPUT_FILE" > "$SCHEMA_FILE"
echo "Created $SCHEMA_FILE (schema only)"

# Extract COPY sections only (data only)
# Include COPY statement through its \. end marker
awk '
  /^COPY / { in_copy = 1 }
  { if (in_copy) print }
  /^\\.$/ && in_copy { in_copy = 0 }
' "$INPUT_FILE" > "$DATA_FILE"
echo "Created $DATA_FILE (data only)"

echo ""
echo "Next steps:"
echo "1. Update 02-home_schema.sql to use schema-only version, OR"
echo "2. Keep backup data separate and restore manually after fresh install"
echo ""
echo "To restore old data after fresh install:"
echo "  docker exec -i pgvector psql -U postgres -d healthv10 < $DATA_FILE"

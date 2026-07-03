-- db_sanity_check.sql
-- Post-restore sanity check for healthv10 database
--
-- Run against the POSTGRES database first to check cluster state,
-- then against healthv10 for detailed checks.
--
-- Usage (run BOTH steps):
--
--   Step 1 - Cluster overview (checks all databases):
--     docker exec -i pgvector psql -U postgres -d postgres \
--       -c "SELECT datname, pg_size_pretty(pg_database_size(datname)) AS size FROM pg_database WHERE NOT datistemplate ORDER BY datname;"
--
--   Step 2 - healthv10 sanity check:
--     cat DataModel3/db_sanity_check.sql | docker exec -i pgvector psql -U postgres -d healthv10
--
-- If Step 2 fails with "database healthv10 does not exist", the restore
-- went into the wrong database (probably 'postgres'). Fix:
--     docker exec -i pgvector psql -U postgres -d postgres \
--       -c "SELECT schemaname, COUNT(*) FROM pg_stat_user_tables GROUP BY schemaname;"
--
-- Date: 2026-04-13
-- Author: Claude Code

\pset border 1
\pset format aligned
\pset tuples_only off

\echo ''
\echo '╔══════════════════════════════════════════════════════════════════════╗'
\echo '║  HEALTHV10 DATABASE SANITY CHECK                                   ║'
\echo '╚══════════════════════════════════════════════════════════════════════╝'

-- ============================================================================
-- 1. BASICS: Am I in the right database?
-- ============================================================================

\echo ''
\echo '── 1. DATABASE IDENTITY ──────────────────────────────────────────────'

SELECT
    current_database() AS connected_to,
    current_user AS connected_as,
    inet_server_addr() AS server_addr,
    inet_server_port() AS server_port,
    version() AS pg_version;

-- ============================================================================
-- 2. SCHEMAS: Where did tables land?
-- ============================================================================

\echo ''
\echo '── 2. SCHEMAS WITH TABLES ────────────────────────────────────────────'
\echo '    (If "public" is missing or has 0 tables, the restore went wrong)'

SELECT
    schemaname AS schema,
    COUNT(*) AS table_count,
    pg_size_pretty(SUM(pg_total_relation_size(quote_ident(schemaname) || '.' || quote_ident(tablename)))) AS total_size
FROM pg_tables
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
GROUP BY schemaname
ORDER BY schemaname;

-- ============================================================================
-- 3. CRITICAL TABLES: Do the must-have tables exist?
-- ============================================================================

\echo ''
\echo '── 3. CRITICAL TABLE CHECK ───────────────────────────────────────────'
\echo '    (All should show EXISTS. Any MISSING = broken restore)'

SELECT unnest AS table_name,
    CASE WHEN EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = unnest
    ) THEN '✓ EXISTS' ELSE '✗ MISSING' END AS status
FROM unnest(ARRAY[
    'tenants', 'users', 'sessions', 'health_inputs', 'stacks',
    'stack_inputs', 'timeframes', 'health_input_log', 'reminders',
    'health_metrics', 'health_blood_pressure_readings',
    'health_food_itemsv2', 'health_food_logv2',
    'health_conditions', 'health_allergies',
    'documents', 'document_pages',
    'hkit_records', 'hkit_activity_summaries',
    'garm_hr', 'garm_daily_summ',
    'garmin_credentials', 'api_tokens'
]);

-- ============================================================================
-- 4. TABLE COUNT vs EXPECTED
-- ============================================================================

\echo ''
\echo '── 4. TABLE INVENTORY ────────────────────────────────────────────────'
\echo '    (Home Edition schema defines ~85 tables. Migration scripts may add more.)'

SELECT
    COUNT(*) AS public_tables,
    CASE
        WHEN COUNT(*) >= 80 THEN '✓ Looks complete'
        WHEN COUNT(*) >= 45 THEN '⚠ Partial restore?'
        WHEN COUNT(*) > 0  THEN '✗ Very incomplete'
        ELSE '✗ EMPTY — tables are in wrong database/schema'
    END AS assessment
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE';

-- ============================================================================
-- 5. ROW COUNTS for key tables
-- ============================================================================

\echo ''
\echo '── 5. KEY TABLE ROW COUNTS ───────────────────────────────────────────'
\echo '    (Non-zero rows = data restored. Zero everywhere = schema only.)'

SELECT 'tenants' AS table_name, COUNT(*) AS rows FROM tenants
UNION ALL SELECT 'users', COUNT(*) FROM users
UNION ALL SELECT 'sessions', COUNT(*) FROM sessions
UNION ALL SELECT 'health_inputs', COUNT(*) FROM health_inputs
UNION ALL SELECT 'stacks', COUNT(*) FROM stacks
UNION ALL SELECT 'health_metrics', COUNT(*) FROM health_metrics
UNION ALL SELECT 'health_conditions', COUNT(*) FROM health_conditions
UNION ALL SELECT 'documents', COUNT(*) FROM documents
UNION ALL SELECT 'hkit_records', COUNT(*) FROM hkit_records
UNION ALL SELECT 'garm_hr', COUNT(*) FROM garm_hr
UNION ALL SELECT 'api_tokens', COUNT(*) FROM api_tokens
ORDER BY table_name;

-- ============================================================================
-- 6. EXTENSIONS
-- ============================================================================

\echo ''
\echo '── 6. REQUIRED EXTENSIONS ────────────────────────────────────────────'
\echo '    (pgcrypto + vector are required. uuid-ossp is common.)'

SELECT
    required.ext AS extension,
    CASE WHEN e.extname IS NOT NULL
        THEN '✓ ' || e.extversion
        ELSE '✗ MISSING'
    END AS status
FROM (VALUES ('pgcrypto'), ('vector'), ('uuid-ossp')) AS required(ext)
LEFT JOIN pg_extension e ON e.extname = required.ext;

-- ============================================================================
-- 7. ROLES
-- ============================================================================

\echo ''
\echo '── 7. APP ROLE CHECK ─────────────────────────────────────────────────'
\echo '    (healthv10_app is the application role; it must exist for the app to connect)'

SELECT
    rolname,
    rolcanlogin AS can_login,
    rolsuper AS is_super
FROM pg_roles
WHERE rolname IN ('healthv10_app', 'postgres')
ORDER BY rolname;

-- Check grants on a few critical tables
\echo ''
\echo '── 7b. GRANTS ON CRITICAL TABLES ─────────────────────────────────────'

SELECT
    table_name,
    grantee,
    string_agg(privilege_type, ', ' ORDER BY privilege_type) AS privileges
FROM information_schema.table_privileges
WHERE grantee = 'healthv10_app'
  AND table_schema = 'public'
  AND table_name IN ('users', 'sessions', 'health_inputs', 'tenants', 'stacks')
GROUP BY table_name, grantee
ORDER BY table_name;

-- ============================================================================
-- 8. TABLE POLICY CHECK (a single-household database expects NONE)
-- ============================================================================

\echo ''
\echo '── 8. TABLE POLICY CHECK ─────────────────────────────────────────────'
\echo '    (Per-user scoping is enforced in the app; no table policies should'
\echo '     exist in a single-household database. Any row-security setting or'
\echo '     policy below means the restore does not match the schema.)'

SELECT
    COUNT(*) FILTER (WHERE c.relrowsecurity) AS tables_with_row_security,
    (SELECT COUNT(*) FROM pg_policies WHERE schemaname = 'public') AS total_policies,
    CASE
        WHEN COUNT(*) FILTER (WHERE c.relrowsecurity) = 0
         AND (SELECT COUNT(*) FROM pg_policies WHERE schemaname = 'public') = 0
        THEN '✓ Clean — no table policies (as expected)'
        ELSE '✗ Unexpected table policies present — investigate'
    END AS assessment
FROM pg_class c
WHERE c.relnamespace = 'public'::regnamespace
  AND c.relkind = 'r';

-- ============================================================================
-- 9. SEQUENCES (ownership matters after restore)
-- ============================================================================

\echo ''
\echo '── 9. SEQUENCE CHECK ─────────────────────────────────────────────────'
\echo '    (Sequences must be owned by their column or INSERTs will fail)'

SELECT
    s.relname AS sequence_name,
    COALESCE(d.refobjid::regclass::text, '⚠ UNOWNED') AS owned_by_table,
    COALESCE(a.attname, '⚠ NO COLUMN') AS owned_by_column
FROM pg_class s
LEFT JOIN pg_depend d ON d.objid = s.oid AND d.deptype = 'a'
LEFT JOIN pg_attribute a ON a.attrelid = d.refobjid AND a.attnum = d.refobjsubid
WHERE s.relkind = 'S'
  AND s.relnamespace = 'public'::regnamespace
ORDER BY s.relname;

-- ============================================================================
-- 10. SEARCH_PATH
-- ============================================================================

\echo ''
\echo '── 10. SEARCH PATH ───────────────────────────────────────────────────'

SHOW search_path;

-- ============================================================================
-- 11. DATABASE SIZE
-- ============================================================================

\echo ''
\echo '── 11. DATABASE SIZE ─────────────────────────────────────────────────'

SELECT pg_size_pretty(pg_database_size('healthv10')) AS healthv10_size;

\echo ''
\echo '══════════════════════════════════════════════════════════════════════'
\echo '  SANITY CHECK COMPLETE'
\echo '══════════════════════════════════════════════════════════════════════'
\echo ''

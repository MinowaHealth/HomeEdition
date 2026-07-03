-- export-schema-snapshot.sql
-- Exports a comprehensive snapshot of the healthv10 database schema
--
-- Usage (run on the appliance):
--   cat DataModel3/export-schema-snapshot.sql | docker exec -i pgvector psql -U postgres -d healthv10
--
-- Output: Human-readable report of all tables, columns, indexes,
--         constraints, functions, and row counts. Pipe to a file and compare
--         against DataModel3 documentation to detect drift.
--
-- Date: 2026-02-24
-- Author: Claude Code

\pset border 2
\pset format aligned
\pset tuples_only off

-- ============================================================================
-- 1. TABLE INVENTORY WITH ROW COUNTS
-- ============================================================================

\echo ''
\echo '============================================================================'
\echo '  1. TABLE INVENTORY WITH ROW COUNTS'
\echo '============================================================================'

SELECT
    t.table_name,
    COALESCE(s.n_live_tup, 0) AS approx_rows,
    pg_size_pretty(pg_total_relation_size(quote_ident(t.table_name))) AS total_size
FROM information_schema.tables t
LEFT JOIN pg_stat_user_tables s ON s.relname = t.table_name
WHERE t.table_schema = 'public'
  AND t.table_type = 'BASE TABLE'
ORDER BY t.table_name;

-- ============================================================================
-- 2. COLUMNS PER TABLE (types, nullability, defaults)
-- ============================================================================

\echo ''
\echo '============================================================================'
\echo '  2. COLUMNS PER TABLE'
\echo '============================================================================'

-- Note: ordinal_position is deliberately omitted. PostgreSQL assigns column
-- positions by add-order (there is no MOVE COLUMN), so prod and a
-- CREATE-TABLE-fresh reference will almost never agree on column order for
-- any table that has ever had ALTER TABLE ADD COLUMN applied. The set of
-- columns and their types/nullability/defaults is what matters.
SELECT
    c.table_name,
    c.column_name,
    c.data_type ||
        CASE WHEN c.character_maximum_length IS NOT NULL
             THEN '(' || c.character_maximum_length || ')'
             ELSE '' END AS data_type,
    CASE WHEN c.is_nullable = 'YES' THEN 'NULL' ELSE 'NOT NULL' END AS nullable,
    COALESCE(c.column_default, '') AS default_value
FROM information_schema.columns c
WHERE c.table_schema = 'public'
ORDER BY c.table_name, c.column_name;

-- ============================================================================
-- 3. PRIMARY KEYS AND UNIQUE CONSTRAINTS
-- ============================================================================

\echo ''
\echo '============================================================================'
\echo '  3. PRIMARY KEYS AND UNIQUE CONSTRAINTS'
\echo '============================================================================'

SELECT
    tc.table_name,
    tc.constraint_name,
    tc.constraint_type,
    string_agg(kcu.column_name, ', ' ORDER BY kcu.ordinal_position) AS columns
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
    ON tc.constraint_name = kcu.constraint_name
    AND tc.table_schema = kcu.table_schema
WHERE tc.table_schema = 'public'
  AND tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE')
GROUP BY tc.table_name, tc.constraint_name, tc.constraint_type
ORDER BY tc.table_name, tc.constraint_type, tc.constraint_name;

-- ============================================================================
-- 4. FOREIGN KEYS
-- ============================================================================

\echo ''
\echo '============================================================================'
\echo '  4. FOREIGN KEYS'
\echo '============================================================================'

SELECT
    tc.table_name AS from_table,
    string_agg(DISTINCT kcu.column_name, ', ' ORDER BY kcu.column_name) AS from_columns,
    ccu.table_name AS to_table,
    string_agg(DISTINCT ccu.column_name, ', ' ORDER BY ccu.column_name) AS to_columns,
    rc.delete_rule
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
    ON tc.constraint_name = kcu.constraint_name
    AND tc.table_schema = kcu.table_schema
JOIN information_schema.constraint_column_usage ccu
    ON tc.constraint_name = ccu.constraint_name
    AND tc.table_schema = ccu.table_schema
JOIN information_schema.referential_constraints rc
    ON tc.constraint_name = rc.constraint_name
    AND tc.table_schema = rc.constraint_schema
WHERE tc.table_schema = 'public'
  AND tc.constraint_type = 'FOREIGN KEY'
GROUP BY tc.table_name, ccu.table_name, rc.delete_rule, tc.constraint_name
ORDER BY tc.table_name, ccu.table_name;

-- ============================================================================
-- 5. CHECK CONSTRAINTS
-- ============================================================================

\echo ''
\echo '============================================================================'
\echo '  5. CHECK CONSTRAINTS'
\echo '============================================================================'

SELECT
    tc.table_name,
    tc.constraint_name,
    cc.check_clause
FROM information_schema.table_constraints tc
JOIN information_schema.check_constraints cc
    ON tc.constraint_name = cc.constraint_name
    AND tc.constraint_schema = cc.constraint_schema
WHERE tc.table_schema = 'public'
  AND tc.constraint_type = 'CHECK'
  -- Exclude system-generated NOT NULL checks
  AND cc.check_clause NOT LIKE '%IS NOT NULL%'
ORDER BY tc.table_name, tc.constraint_name;

-- ============================================================================
-- 6. INDEXES
-- ============================================================================

\echo ''
\echo '============================================================================'
\echo '  6. INDEXES'
\echo '============================================================================'

SELECT
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
ORDER BY tablename, indexname;

-- ============================================================================
-- 7. FUNCTIONS
-- ============================================================================

\echo ''
\echo '============================================================================'
\echo '  7. FUNCTIONS'
\echo '============================================================================'

SELECT
    p.proname AS function_name,
    pg_get_function_arguments(p.oid) AS arguments,
    pg_get_function_result(p.oid) AS return_type,
    CASE p.provolatile
        WHEN 'i' THEN 'IMMUTABLE'
        WHEN 's' THEN 'STABLE'
        WHEN 'v' THEN 'VOLATILE'
    END AS volatility,
    p.prosrc AS source
FROM pg_proc p
JOIN pg_namespace n ON p.pronamespace = n.oid
WHERE n.nspname = 'public'
ORDER BY p.proname;

-- ============================================================================
-- 8. ROLES AND GRANTS
-- ============================================================================

\echo ''
\echo '============================================================================'
\echo '  8. ROLES AND GRANTS'
\echo '============================================================================'

-- Check if healthv10_app role exists and its attributes
SELECT
    rolname,
    rolsuper,
    rolinherit,
    rolcreaterole,
    rolcreatedb,
    rolcanlogin,
    rolreplication
FROM pg_roles
WHERE rolname IN ('healthv10_app', 'postgres')
ORDER BY rolname;

-- Table grants for healthv10_app
SELECT
    table_name,
    string_agg(privilege_type, ', ' ORDER BY privilege_type) AS privileges
FROM information_schema.table_privileges
WHERE grantee = 'healthv10_app'
  AND table_schema = 'public'
GROUP BY table_name
ORDER BY table_name;

-- ============================================================================
-- 9. EXTENSIONS
-- ============================================================================

\echo ''
\echo '============================================================================'
\echo '  9. EXTENSIONS'
\echo '============================================================================'

SELECT extname, extversion FROM pg_extension ORDER BY extname;

-- ============================================================================
-- 10. VIEWS AND MATERIALIZED VIEWS
-- ============================================================================

\echo ''
\echo '============================================================================'
\echo '  10. VIEWS AND MATERIALIZED VIEWS'
\echo '============================================================================'

SELECT view_name, kind, definition
FROM (
    SELECT
        table_name AS view_name,
        'VIEW' AS kind,
        view_definition AS definition
    FROM information_schema.views
    WHERE table_schema = 'public'
    UNION ALL
    SELECT
        matviewname AS view_name,
        'MATERIALIZED VIEW' AS kind,
        definition
    FROM pg_matviews
    WHERE schemaname = 'public'
) v
ORDER BY kind, view_name;

-- ============================================================================
-- 11. TRIGGERS
-- ============================================================================

\echo ''
\echo '============================================================================'
\echo '  11. TRIGGERS'
\echo '============================================================================'

SELECT
    c.relname AS table_name,
    t.tgname AS trigger_name,
    CASE t.tgenabled WHEN 'D' THEN 'DISABLED' ELSE 'ENABLED' END AS status,
    pg_get_triggerdef(t.oid) AS definition
FROM pg_trigger t
JOIN pg_class c ON t.tgrelid = c.oid
JOIN pg_namespace n ON c.relnamespace = n.oid
WHERE n.nspname = 'public'
  AND NOT t.tgisinternal  -- exclude FK-backing triggers
ORDER BY c.relname, t.tgname;

-- ============================================================================
-- 12. CUSTOM TYPES AND ENUMS
-- ============================================================================

\echo ''
\echo '============================================================================'
\echo '  12. CUSTOM TYPES AND ENUMS'
\echo '============================================================================'

SELECT
    t.typname AS type_name,
    CASE t.typtype
        WHEN 'e' THEN 'ENUM'
        WHEN 'd' THEN 'DOMAIN'
        WHEN 'c' THEN 'COMPOSITE'
    END AS kind,
    CASE WHEN t.typtype = 'e' THEN
        (SELECT string_agg(enumlabel, ', ' ORDER BY enumsortorder)
         FROM pg_enum WHERE enumtypid = t.oid)
    END AS enum_values
FROM pg_type t
JOIN pg_namespace n ON t.typnamespace = n.oid
LEFT JOIN pg_class c ON c.reltype = t.oid
WHERE n.nspname = 'public'
  AND t.typtype IN ('e', 'd', 'c')
  AND c.oid IS NULL  -- exclude implicit composite types auto-created for tables
ORDER BY kind, type_name;

-- ============================================================================
-- 13. OBJECT COMMENTS
-- ============================================================================

\echo ''
\echo '============================================================================'
\echo '  13. OBJECT COMMENTS'
\echo '============================================================================'

SELECT
    c.relname AS object_name,
    CASE c.relkind
        WHEN 'r' THEN 'table'
        WHEN 'v' THEN 'view'
        WHEN 'm' THEN 'matview'
    END AS object_type,
    COALESCE(col.attname, '') AS column_name,
    d.description
FROM pg_description d
JOIN pg_class c ON d.objoid = c.oid
JOIN pg_namespace n ON c.relnamespace = n.oid
LEFT JOIN pg_attribute col
    ON col.attrelid = c.oid
    AND col.attnum = d.objsubid
    AND d.objsubid > 0
WHERE n.nspname = 'public'
  AND c.relkind IN ('r', 'v', 'm')
ORDER BY c.relname, COALESCE(col.attname, '');

-- ============================================================================
-- 14. SUMMARY
-- ============================================================================

\echo ''
\echo '============================================================================'
\echo '  14. SUMMARY'
\echo '============================================================================'

SELECT 'Tables' AS metric, COUNT(*)::text AS value
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
UNION ALL
SELECT 'Views', COUNT(*)::text
FROM information_schema.views WHERE table_schema = 'public'
UNION ALL
SELECT 'Indexes', COUNT(*)::text
FROM pg_indexes WHERE schemaname = 'public'
UNION ALL
SELECT 'Functions', COUNT(*)::text
FROM pg_proc p JOIN pg_namespace n ON p.pronamespace = n.oid
WHERE n.nspname = 'public'
UNION ALL
SELECT 'Triggers', COUNT(*)::text
FROM pg_trigger t
JOIN pg_class c ON t.tgrelid = c.oid
JOIN pg_namespace n ON c.relnamespace = n.oid
WHERE n.nspname = 'public' AND NOT t.tgisinternal
UNION ALL
SELECT 'Total DB Size', pg_size_pretty(pg_database_size('healthv10'));

\echo ''
\echo 'Snapshot complete. Compare against DataModel3/ documentation for drift.'

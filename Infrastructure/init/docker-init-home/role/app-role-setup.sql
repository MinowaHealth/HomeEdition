-- app-role-setup.sql — Home Edition
-- Application role creation, grants, and query-performance indexes.
--
-- Ported from the v10 rls-complete-setup.sql with the RLS machinery removed:
-- no policies exist in the Home Edition schema, so the role is a plain
-- application login. The (tenant_id, user_id) composite indexes are kept —
-- queries still filter on those columns (tenant_id is always 1).
--
-- This script is IDEMPOTENT — safe to run multiple times.

-- psql variable bootstrap:
-- Supports three modes of receiving app_db_password:
--   (a) -v app_db_password=... on the psql command line
--   (b) APP_DB_PASSWORD environment variable
--   (c) fallback to 'Password2026' with a warning
\if :{?app_db_password}
\else
\getenv app_db_password APP_DB_PASSWORD
\endif
\if :{?app_db_password}
\else
\set app_db_password Password2026
\echo 'WARN: app_db_password not provided; defaulting to Password2026'
\endif

-- ============================================================================
-- STEP 1: Create application role
-- ============================================================================

-- Revoke and reassign before dropping (required when role owns objects)
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'healthv10_app') THEN
        REASSIGN OWNED BY healthv10_app TO postgres;
        DROP OWNED BY healthv10_app;
    END IF;
END
$$;
DROP ROLE IF EXISTS healthv10_app;
CREATE ROLE healthv10_app WITH LOGIN PASSWORD :'app_db_password';

-- ============================================================================
-- STEP 2: Grant permissions to healthv10_app
-- ============================================================================

GRANT USAGE ON SCHEMA public TO healthv10_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO healthv10_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO healthv10_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO healthv10_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO healthv10_app;

-- ============================================================================
-- STEP 3: Composite indexes for query performance
-- ============================================================================
-- Same shapes as v10 (queries still filter on tenant_id, user_id).

-- Large Garmin tables (750k-1M+ rows)
CREATE INDEX IF NOT EXISTS idx_garm_stress_tenant_user ON garm_stress(tenant_id, user_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_garm_rr_tenant_user ON garm_rr(tenant_id, user_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_garm_hr_tenant_user ON garm_hr(tenant_id, user_id, timestamp DESC);

-- Health metrics (4M+ rows)
CREATE INDEX IF NOT EXISTS idx_health_metrics_tenant_user ON health_metrics(tenant_id, user_id, recorded_at DESC);

-- Health input/tracking tables
CREATE INDEX IF NOT EXISTS idx_health_inputs_tenant_user ON health_inputs(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_health_input_log_tenant_user ON health_input_log(tenant_id, user_id, logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_stacks_tenant_user ON stacks(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_stack_inputs_tenant_user ON stack_inputs(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_timeframes_tenant_user ON timeframes(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_remedies_tenant_user ON remedies(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_remedy_inputs_tenant_user ON remedy_inputs(tenant_id, user_id);

-- Vitals and observations
CREATE INDEX IF NOT EXISTS idx_bp_readings_tenant_user ON health_blood_pressure_readings(tenant_id, user_id, measured_at DESC);
CREATE INDEX IF NOT EXISTS idx_blood_work_tenant_user ON health_blood_work(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_observations_tenant_user ON health_observations(tenant_id, user_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_conditions_tenant_user ON health_conditions(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_vaccinations_tenant_user ON health_vaccinations(tenant_id, user_id);

-- Food tracking
CREATE INDEX IF NOT EXISTS idx_food_items_tenant_user ON health_food_itemsv2(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_food_log_tenant_user ON health_food_logv2(tenant_id, user_id, logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_meals_tenant_user ON meals(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_meal_items_tenant_user ON meal_items(tenant_id, user_id);

-- User data
CREATE INDEX IF NOT EXISTS idx_daily_energy_tenant_user ON daily_energy(tenant_id, user_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_tenant_user ON feedback(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_user_prefs_tenant_user ON user_preferences(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_sync_queue_tenant_user ON sync_queue(tenant_id, user_id);

-- Garmin integration
CREATE INDEX IF NOT EXISTS idx_garm_daily_summ_tenant_user ON garm_daily_summ(tenant_id, user_id, calendar_date DESC);
CREATE INDEX IF NOT EXISTS idx_garm_sleep_tenant_user ON garm_sleep(tenant_id, user_id, calendar_date DESC);
CREATE INDEX IF NOT EXISTS idx_garm_sleep_events_tenant_user ON garm_sleep_events(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_garm_upload_date_tenant_user ON garm_upload_date(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_garmin_credentials_tenant_user ON garmin_credentials(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_garmin_sync_jobs_tenant_user ON garmin_sync_jobs(tenant_id, user_id);

-- HealthKit integration
CREATE INDEX IF NOT EXISTS idx_hkit_sources_tenant_user ON hkit_sources(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_hkit_records_tenant_user ON hkit_records(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_hkit_activity_tenant_user ON hkit_activity_summaries(tenant_id, user_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_hkit_workouts_tenant_user ON hkit_workouts(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_hkit_user_profile_tenant_user ON hkit_user_profile(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_hkit_clinical_tenant_user ON hkit_clinical_records(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_hkit_lab_tenant_user ON hkit_lab_observations(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_hkit_allergies_tenant_user ON hkit_allergies(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_hkit_immunizations_tenant_user ON hkit_immunizations(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_hkit_medications_tenant_user ON hkit_medications(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_healthkit_import_tenant_user ON healthkit_import_jobs(tenant_id, user_id);

-- Provider contact book (patient-side data; table kept whole)
CREATE INDEX IF NOT EXISTS idx_upc_tenant_user ON user_provider_contacts(tenant_id, user_id, created_at DESC);

-- System tables
CREATE INDEX IF NOT EXISTS idx_schema_versions_tenant ON schema_versions(tenant_id);

-- ============================================================================
-- STEP 4: Summary
-- ============================================================================

SELECT 'App role setup finished (role + grants + indexes)' as status;
SELECT 'Policies (expect 0): ' || COUNT(*)::text FROM pg_policies WHERE schemaname = 'public';
SELECT 'Indexes: ' || COUNT(*)::text FROM pg_indexes WHERE schemaname = 'public' AND indexname LIKE 'idx_%tenant%';

-- 2026-07-14 — data_sync_log: user-visible sync history
-- Design: APIDocumentation/DataSyncLog-Plan1.md
-- One append-only row per terminal sync/import run (Garmin, HealthKit, future
-- ecosystems), surfaced by /all-logs as type='sync'. Forward-only: no backfill
-- of historical garmin_sync_jobs / healthkit_import_jobs rows (Neal, 2026-07-14).
-- Idempotent — safe to re-apply.

CREATE TABLE IF NOT EXISTS public.data_sync_log (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    source text NOT NULL,
    job_id uuid,
    status text NOT NULL,
    detail jsonb,
    error_message text,
    synced_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE,
    CONSTRAINT data_sync_log_status_check CHECK ((status = ANY (ARRAY['completed'::text, 'failed'::text])))
);

CREATE INDEX IF NOT EXISTS idx_data_sync_log_user_time ON public.data_sync_log USING btree (tenant_id, user_id, synced_at DESC);

ALTER TABLE public.data_sync_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.data_sync_log FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS data_sync_log_isolation ON public.data_sync_log;
CREATE POLICY data_sync_log_isolation ON public.data_sync_log
    FOR ALL
    USING (tenant_id = current_setting('app.current_tenant_id')::SMALLINT
           AND user_id = current_setting('app.current_user_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id')::SMALLINT
                AND user_id = current_setting('app.current_user_id')::uuid);

GRANT SELECT, INSERT ON public.data_sync_log TO healthv10_app;

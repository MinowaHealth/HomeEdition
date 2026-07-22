#!/usr/bin/env python3
"""apply_11_1_0_home.py — Bring a running 11.0.0-home box up to 11.1.0-home.

July 2026 HB port delta, idempotent (safe to re-run):
  - data_sync_log + health_input_acquisitions tables and indexes
  - documents: fts generated tsvector + partial GIN, provenance JSONB,
    source CHECK extended with 'chat_summary' and 'episode_report'
  - 'AI Sessions' + 'Episode Reports' system folders (trigger refresh
    + backfill for existing users)
  - user_preferences.bp_devices text[]
  - schema_version marker 11.1.0-home

Fresh installs get all of this from 02-home_schema.sql; this script exists
only for the already-running appliance (No-psql-CLI rule — apply via
psycopg with credentials from local.env).

Usage (from the repo root, venv active):
    .venv/bin/python scripts/apply_11_1_0_home.py [--env-file local.env] [--dry-run]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import psycopg

DDL = """
BEGIN;

-- ── health_input_acquisitions ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.health_input_acquisitions (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sqlite_id BIGINT,
    user_id uuid NOT NULL,
    health_input_id uuid,
    item_name text NOT NULL,
    acquired_date date NOT NULL,
    quantity numeric(10,2),
    unit text,
    cost numeric(10,2),
    brand text,
    vendor text,
    expiration_date date,
    notes text,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    synced_at timestamp with time zone,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, health_input_id) REFERENCES public.health_inputs(tenant_id, id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_hia_user_date
    ON public.health_input_acquisitions USING btree (tenant_id, user_id, acquired_date DESC);
CREATE INDEX IF NOT EXISTS idx_hia_user_input
    ON public.health_input_acquisitions USING btree (tenant_id, user_id, health_input_id);

-- ── data_sync_log ───────────────────────────────────────────────────────────
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
CREATE INDEX IF NOT EXISTS idx_data_sync_log_user_time
    ON public.data_sync_log USING btree (tenant_id, user_id, synced_at DESC);

-- ── documents: provenance + fts + widened source CHECK ─────────────────────
ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS provenance jsonb;
ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS fts tsvector
    GENERATED ALWAYS AS (to_tsvector('english',
        left(coalesce(title, '') || ' ' || coalesce(ocr_text_full, ''), 500000))) STORED;
CREATE INDEX IF NOT EXISTS idx_documents_fts
    ON public.documents USING GIN (fts) WHERE deleted_at IS NULL;
ALTER TABLE public.documents DROP CONSTRAINT IF EXISTS documents_source_check;
ALTER TABLE public.documents ADD CONSTRAINT documents_source_check
    CHECK (source IN ('upload', 'fax_inbound', 'email', 'provider_send',
                      'chat_summary', 'episode_report'));

-- ── user_preferences.bp_devices ────────────────────────────────────────────
ALTER TABLE public.user_preferences ADD COLUMN IF NOT EXISTS bp_devices text[] DEFAULT NULL;

-- ── system folders: refresh trigger fn + backfill existing users ───────────
CREATE OR REPLACE FUNCTION public.seed_user_system_folders()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.document_folders (tenant_id, user_id, parent_id, name, is_system)
    VALUES
        (NEW.tenant_id, NEW.id, NULL, 'Documents',       TRUE),
        (NEW.tenant_id, NEW.id, NULL, 'Fax',             TRUE),
        (NEW.tenant_id, NEW.id, NULL, 'AI Sessions',     TRUE),
        (NEW.tenant_id, NEW.id, NULL, 'Episode Reports', TRUE);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

INSERT INTO public.document_folders (tenant_id, user_id, parent_id, name, is_system)
SELECT u.tenant_id, u.id, NULL, f.name, TRUE
FROM public.users u
CROSS JOIN (VALUES ('AI Sessions'), ('Episode Reports')) AS f(name)
WHERE NOT EXISTS (
    SELECT 1 FROM public.document_folders d
    WHERE d.tenant_id = u.tenant_id AND d.user_id = u.id
      AND d.name = f.name AND d.is_system = TRUE AND d.deleted_at IS NULL
);

INSERT INTO public.schema_version (version, description)
VALUES ('11.1.0-home', 'July 2026 HB port: data_sync_log, health_input_acquisitions, documents fts/provenance/chat_summary/episode_report, AI Sessions + Episode Reports system folders, bp_devices on user_preferences')
ON CONFLICT (version) DO NOTHING;

COMMIT;
"""


def read_env(path: Path) -> dict:
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip()
    return env


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or '').split('\n', 1)[0])
    ap.add_argument('--env-file', default='local.env')
    ap.add_argument('--dry-run', action='store_true', help='print the DDL and exit')
    args = ap.parse_args()

    if args.dry_run:
        print(DDL)
        return 0

    env = read_env(Path(args.env_file))
    conninfo = (
        f"host={env.get('POSTGRES_HOST', '127.0.0.1')} "
        f"port={env.get('POSTGRES_PORT', '5432')} "
        f"dbname={env.get('POSTGRES_DB', 'healthv10')} "
        f"user={env.get('POSTGRES_USER', 'postgres')} "
        f"password={env['POSTGRES_PASSWORD']}"
    )
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute(DDL)
        row = conn.execute(
            "SELECT version FROM schema_version ORDER BY applied_at DESC NULLS LAST LIMIT 1"
        ).fetchone()
        print(f"Applied. Latest schema_version marker: {row[0] if row else '?'}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

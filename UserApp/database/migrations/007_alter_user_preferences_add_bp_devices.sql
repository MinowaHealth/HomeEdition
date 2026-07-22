-- Migration: Add bp_devices to user_preferences
-- Date: 2026-07-19
-- Purpose: User-defined list of blood pressure meters. The web log form
--          offers device as a strict pick list drawn from this column
--          (plus 'manual' for no-device entries) instead of free text,
--          so device names can't drift and multi-meter users can filter
--          readings by source reliably.

BEGIN;

ALTER TABLE public.user_preferences
    ADD COLUMN IF NOT EXISTS bp_devices text[] DEFAULT NULL;

COMMENT ON COLUMN public.user_preferences.bp_devices IS
    'User-defined blood pressure meter names for the BP log pick list. NULL/empty = manual entry only.';

INSERT INTO public.schema_version (version, description)
VALUES ('10.18.0', 'bp_devices text[] on user_preferences — user-defined BP meter pick list')
ON CONFLICT (version) DO NOTHING;

COMMIT;

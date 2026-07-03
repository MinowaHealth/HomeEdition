--
-- healthv10 Home Edition Schema
--
-- Source of truth for the running appliance database. One household, one box:
-- tenant_id is present on every table and is always 1 (a fixed app-level
-- scoping convention); per-user privacy is enforced in the application with
-- explicit user_id predicates on every query.
--
-- Schema version marker: 11.0.0-home (see schema_version at the end of this
-- file). Roles, grants, and query-performance indexes live in
-- role/app-role-setup.sql.
--

-- ============================================================================
-- EXTENSIONS
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
-- No VERSION pin: install whatever pgvector the base image ships. The
-- vector(768) type and cosine/IVFFlat operators are stable across pgvector
-- releases, and pinning a micro-version breaks when the (mutable)
-- pgvector/pgvector:pg18 tag stops shipping that version's install script.
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================================
-- TENANTS TABLE (System table - no tenant_id on itself)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.tenants (
    id SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL,
    slug text NOT NULL UNIQUE,  -- URL-safe identifier (e.g., 'acme-health')
    domain text,                 -- Custom domain if white-labeled
    is_active boolean DEFAULT true,
    settings jsonb DEFAULT '{}',  -- Branding, feature flags, etc.
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

-- Insert default tenant (id=1 for single-tenant deployments)
INSERT INTO public.tenants (name, slug) VALUES ('Minowa', 'minowa') ON CONFLICT (slug) DO NOTHING;

-- System tenant (id=0 reserved but not auto-created)
-- Use for system-wide data if needed in future

CREATE INDEX IF NOT EXISTS idx_tenants_slug ON public.tenants USING btree (slug);
CREATE INDEX IF NOT EXISTS idx_tenants_domain ON public.tenants USING btree (domain) WHERE domain IS NOT NULL;

-- ============================================================================
-- AUTHENTICATION TABLES
-- ============================================================================

-- Users table - authentication and profile
CREATE TABLE IF NOT EXISTS public.users (
    tenant_id SMALLINT NOT NULL DEFAULT 1 REFERENCES public.tenants(id),
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sqlite_id text,
    email text NOT NULL,
    username text,  -- v7 compatibility
    display_name text,
    password_hash character varying(255) NOT NULL,
    phone_number text,
    user_hash text,
    hash_salt text,
    device_at_creation text,
    created_at_ms bigint,
    avatar_photo_url text,
    birth_year integer,
    birth_month integer,
    account_type text DEFAULT 'free'::text,
    deployment_type text DEFAULT 'saas'::text,
    home_timezone text DEFAULT 'America/Los_Angeles'::text,
    locale text DEFAULT 'en-US'::text,
    preferred_language text DEFAULT 'en'::text,
    biological_sex text,
    gender_identity text,
    pronouns text,
    onboarding_complete integer DEFAULT 0,
    last_active_at timestamp with time zone,
    track_energy_spoons integer DEFAULT 0,
    is_active boolean DEFAULT true,
    is_developer boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    last_login timestamp with time zone,
    notes text,
    -- Two-Factor Authentication (TOTP)
    totp_secret text,
    totp_enabled boolean DEFAULT false,
    totp_backup_codes text[],
    totp_enabled_at timestamp with time zone,
    -- Clinical demographics
    date_of_birth date,
    address_line1 text,
    address_line2 text,
    city text,
    state_province text,
    postal_code text,
    country text DEFAULT 'US',
    -- Promo/Invite code used at signup
    promo_code character varying(50),
    -- UserDocs (Phase 0)
    fax_number text,
    accepts_sms boolean DEFAULT false,
    contact_hours jsonb,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, email),
    UNIQUE (tenant_id, user_hash),
    CONSTRAINT users_account_type_check CHECK ((account_type = ANY (ARRAY['free'::text, 'premium'::text, 'family'::text]))),
    CONSTRAINT users_biological_sex_check CHECK ((biological_sex = ANY (ARRAY['female'::text, 'male'::text, 'intersex'::text, 'not_specified'::text]))),
    CONSTRAINT users_deployment_type_check CHECK ((deployment_type = ANY (ARRAY['saas'::text, 'self_hosted'::text])))
);

CREATE INDEX IF NOT EXISTS idx_users_tenant_email ON public.users USING btree (tenant_id, email);
CREATE INDEX IF NOT EXISTS idx_users_tenant_active ON public.users USING btree (tenant_id, is_active);
CREATE INDEX IF NOT EXISTS idx_users_sqlite ON public.users USING btree (sqlite_id) WHERE (sqlite_id IS NOT NULL);
CREATE INDEX IF NOT EXISTS idx_users_promo_code ON public.users USING btree (promo_code) WHERE (promo_code IS NOT NULL);
CREATE INDEX IF NOT EXISTS idx_users_totp_enabled ON public.users USING btree (totp_enabled) WHERE (totp_enabled = true);

-- Sessions table - web/API sessions
CREATE TABLE IF NOT EXISTS public.sessions (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    session_id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    expires_at timestamp with time zone NOT NULL,
    ip_address inet,
    user_agent text,
    last_activity timestamp with time zone DEFAULT now(),
    -- Session type: 'web' for browser sessions, 'api' for legacy API sessions
    session_type text DEFAULT 'web',
    -- When 2FA was verified for this session (NULL if pre-2FA or 2FA not enabled)
    totp_verified_at timestamp with time zone,
    PRIMARY KEY (tenant_id, session_id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE,
    CONSTRAINT valid_expiry CHECK ((expires_at > created_at))
);

CREATE INDEX IF NOT EXISTS idx_sessions_tenant_user ON public.sessions USING btree (tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON public.sessions USING btree (expires_at);

-- Session cleanup function
CREATE OR REPLACE FUNCTION public.cleanup_expired_sessions() RETURNS integer
    LANGUAGE plpgsql AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM sessions WHERE expires_at < NOW();
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$;

-- Password reset tokens
CREATE TABLE IF NOT EXISTS public.password_reset_tokens (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    token text NOT NULL,  -- Plain token (auth.py doesn't hash these)
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,  -- NULL = not used, timestamp = when used
    created_at timestamp with time zone DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_password_reset_token ON public.password_reset_tokens USING btree (token);
CREATE INDEX IF NOT EXISTS idx_password_reset_expires ON public.password_reset_tokens USING btree (expires_at);

-- Email verification tokens (for pre-signup flow - no user_id FK)
CREATE TABLE IF NOT EXISTS public.email_verification_tokens (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    email text NOT NULL,
    display_name text,  -- Stored during signup flow
    token text NOT NULL,  -- Plain token
    expires_at timestamp with time zone NOT NULL,
    verified_at timestamp with time zone,  -- NULL = not verified, timestamp = when verified
    created_at timestamp with time zone DEFAULT now(),
    PRIMARY KEY (tenant_id, id)
    -- No user FK - this is for pre-signup email verification
);

CREATE INDEX IF NOT EXISTS idx_email_verify_token ON public.email_verification_tokens USING btree (token);
CREATE INDEX IF NOT EXISTS idx_email_verify_email ON public.email_verification_tokens USING btree (tenant_id, email);

-- ============================================================================
-- SYSTEM/ADMIN TABLES
-- ============================================================================

-- Audit log (tenant-scoped for isolation)
CREATE TABLE IF NOT EXISTS public.audit_log (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id bigint GENERATED ALWAYS AS IDENTITY,
    user_id uuid,
    action text NOT NULL,
    target_type text,
    target_id text,
    details jsonb,
    ip_address inet,
    created_at timestamp with time zone DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_audit_tenant_user ON public.audit_log USING btree (tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_audit_tenant_created ON public.audit_log USING btree (tenant_id, created_at DESC);

-- Schema version tracking (global, no tenant_id)
CREATE TABLE IF NOT EXISTS public.schema_version (
    version character varying(20) NOT NULL PRIMARY KEY,
    applied_at timestamp with time zone DEFAULT now(),
    description text
);


-- Feedback (tenant-scoped)
CREATE TABLE IF NOT EXISTS public.feedback (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid,
    feedback_type text NOT NULL,
    content text NOT NULL,
    page_context text,
    user_agent text,
    screen_resolution text,
    app_version text,
    source_app text NOT NULL DEFAULT 'UserApp',
    environment text NOT NULL DEFAULT 'pilot',
    metadata jsonb NOT NULL DEFAULT '{}',
    created_at timestamp with time zone DEFAULT now(),
    status text DEFAULT 'new',
    admin_notes text,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE SET NULL,
    CONSTRAINT feedback_type_check CHECK ((feedback_type = ANY (ARRAY['bug'::text, 'feature'::text, 'general'::text, 'praise'::text])))
);

CREATE INDEX IF NOT EXISTS idx_feedback_tenant_status ON public.feedback USING btree (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_feedback_source_app ON public.feedback USING btree (source_app);

-- ============================================================================
-- USER PREFERENCES
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.user_preferences (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    avatar_style text DEFAULT 'default',
    theme text DEFAULT 'default',
    color_scheme text DEFAULT 'system',
    font_size text DEFAULT 'medium',
    compact_mode boolean DEFAULT false,
    show_animations boolean DEFAULT true,
    units_weight text DEFAULT 'lbs',
    units_height text DEFAULT 'ft_in',
    units_temperature text DEFAULT 'fahrenheit',
    units_blood_glucose text DEFAULT 'mg_dl',
    notification_email boolean DEFAULT true,
    notification_push boolean DEFAULT true,
    notification_sms boolean DEFAULT false,
    reminder_medications boolean DEFAULT true,
    reminder_logging boolean DEFAULT true,
    privacy_share_anonymous boolean DEFAULT false,
    privacy_data_retention text DEFAULT 'forever',
    sidebar_order text[] DEFAULT NULL,     -- Activity keys in user's preferred order
    sidebar_hidden text[] DEFAULT NULL,    -- Activity keys the user has hidden
    timezone_reminder_mode text DEFAULT 'local' CHECK (timezone_reminder_mode IN ('home', 'local')),
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, user_id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

-- Daily energy/spoons tracking
CREATE TABLE IF NOT EXISTS public.daily_energy (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    date date NOT NULL,
    starting_spoons integer,
    current_spoons integer,
    notes text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, user_id, date),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

-- Timeframes (user-defined times like "morning", "bedtime")
-- Defined before health_inputs because health_inputs.timeframe_id references it.
CREATE TABLE IF NOT EXISTS public.timeframes (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sqlite_id text,
    user_id uuid NOT NULL,
    name text NOT NULL,
    time_of_day time without time zone,
    sort_order integer DEFAULT 0,
    is_active boolean DEFAULT true,
    notes text,
    frequency text DEFAULT 'daily' CHECK (frequency IN ('daily', 'weekly', 'monthly', 'annual', 'custom', 'once')),
    custom_days integer[],                -- 0=Sun, 1=Mon, ..., 6=Sat (used when frequency='custom')
    start_date date,                      -- Anchor date for recurrence calculations
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    synced_at timestamp with time zone,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_timeframes_tenant_user ON public.timeframes USING btree (tenant_id, user_id);

-- ============================================================================
-- HEALTH INPUTS (Medications, Supplements, etc.)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.health_inputs (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sqlite_id text,
    user_id uuid NOT NULL,
    name text NOT NULL,
    input_type text NOT NULL,
    default_dosage text,
    default_unit text,
    brand text,
    form text,
    frequency text,
    route text,
    instructions text,
    is_active boolean DEFAULT true,
    take_with_food boolean,
    refill_reminder_days integer,
    current_quantity numeric,
    start_date date,
    end_date date,
    prescribing_doctor text,
    pharmacy text,
    rx_number text,
    refills_remaining integer,
    notes text,
    custom_fields jsonb,
    doses_per_day integer,                       -- NULL=unspecified, -1=PRN/as-needed, 1-4=fixed daily doses
    frequent_status text,                       -- NULL=default, 'detected'=auto, 'sticky'=user-pinned
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    synced_at timestamp with time zone,
    embedding_name vector(768),                 -- pgvector: semantic match for log_promotions
    timeframe_id uuid,                          -- Optional: standalone input with projected reminder
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, timeframe_id) REFERENCES public.timeframes(tenant_id, id) ON DELETE SET NULL,
    CONSTRAINT health_inputs_input_type_check CHECK ((input_type = ANY (ARRAY['medication'::text, 'supplement'::text, 'alternative'::text, 'treatment'::text]))),
    CONSTRAINT health_inputs_frequent_status_check CHECK (frequent_status IS NULL OR frequent_status = ANY(ARRAY['detected'::text, 'sticky'::text])),
    CONSTRAINT health_inputs_doses_per_day_check CHECK (doses_per_day IS NULL OR doses_per_day = ANY(ARRAY[-1, 1, 2, 3, 4]))
);

CREATE INDEX IF NOT EXISTS idx_health_inputs_tenant_user ON public.health_inputs USING btree (tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_health_inputs_tenant_type ON public.health_inputs USING btree (tenant_id, input_type);
CREATE INDEX IF NOT EXISTS idx_health_inputs_sqlite ON public.health_inputs USING btree (sqlite_id) WHERE (sqlite_id IS NOT NULL);
CREATE INDEX IF NOT EXISTS idx_health_inputs_timeframe ON public.health_inputs USING btree (tenant_id, timeframe_id) WHERE (timeframe_id IS NOT NULL);
-- Partial unique index — case-insensitive name uniqueness among active rows.
-- Pilot feedback: "stacks duplicates bug" + "cached old/misspelled meds persist
-- after rename" both stem from the absence of this constraint. Partial on
-- is_active means archived rows can coexist with new active ones of the same
-- name (the rename-then-recreate workflow). Delta:
-- Infrastructure/deltas/2026-04-30-stacks_inputs_unique_names.sql
CREATE UNIQUE INDEX IF NOT EXISTS ux_health_inputs_active_name
    ON public.health_inputs (tenant_id, user_id, lower(name))
    WHERE is_active = true;

-- Stacks (bundles of health inputs taken together)
CREATE TABLE IF NOT EXISTS public.stacks (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sqlite_id text,
    user_id uuid NOT NULL,
    name text NOT NULL,
    timeframe_id uuid,
    description text,
    notes text,
    is_active boolean DEFAULT true,
    sort_order integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    synced_at timestamp with time zone,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, timeframe_id) REFERENCES public.timeframes(tenant_id, id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_stacks_tenant_user ON public.stacks USING btree (tenant_id, user_id);
-- Partial unique index — see ux_health_inputs_active_name above for rationale.
CREATE UNIQUE INDEX IF NOT EXISTS ux_stacks_active_name
    ON public.stacks (tenant_id, user_id, lower(name))
    WHERE is_active = true;

-- Stack inputs (junction table)
CREATE TABLE IF NOT EXISTS public.stack_inputs (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sqlite_id text,
    user_id uuid NOT NULL,
    stack_id uuid NOT NULL,
    health_input_id uuid NOT NULL,
    sort_order integer DEFAULT 0,
    dosage_override text,
    notes text,
    created_at timestamp with time zone DEFAULT now(),
    synced_at timestamp with time zone,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, stack_id, health_input_id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, stack_id) REFERENCES public.stacks(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, health_input_id) REFERENCES public.health_inputs(tenant_id, id) ON DELETE CASCADE
);

-- Health conditions
CREATE TABLE IF NOT EXISTS public.health_conditions (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sqlite_id text,
    user_id uuid NOT NULL,
    name text NOT NULL,
    icd10_code text,
    diagnosed_date date,
    status text DEFAULT 'active',
    severity text,
    treating_doctor text,
    notes text,
    custom_fields jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    synced_at timestamp with time zone,
    embedding_condition vector(768),            -- pgvector: condition name/description
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE,
    CONSTRAINT health_conditions_status_check CHECK ((status = ANY (ARRAY['active'::text, 'managed'::text, 'resolved'::text, 'monitoring'::text])))
);

CREATE INDEX IF NOT EXISTS idx_health_conditions_tenant_user ON public.health_conditions USING btree (tenant_id, user_id);

-- Health allergies (manual entry from any platform; hkit_allergies is HealthKit-only)
CREATE TABLE IF NOT EXISTS public.health_allergies (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sqlite_id text,
    user_id uuid NOT NULL,
    allergen text NOT NULL,                    -- medication name, food, substance
    allergy_type text,                         -- 'medication', 'food', 'environmental', 'insect', 'other'
    reaction text,                             -- e.g. 'hives', 'anaphylaxis', 'rash'
    severity text,                             -- 'mild', 'moderate', 'severe', 'life-threatening'
    onset_date date,
    status text DEFAULT 'active',              -- 'active', 'resolved', 'suspected'
    notes text,
    source text DEFAULT 'manual',              -- 'manual', 'healthkit_import', 'provider_reported'
    custom_fields jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    synced_at timestamp with time zone,
    embedding_allergy_full vector(768),         -- pgvector: allergen + reaction + notes
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_health_allergies_tenant_user ON public.health_allergies (tenant_id, user_id);

-- Family medical history — one row per family member per condition
CREATE TABLE IF NOT EXISTS public.health_family_history (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sqlite_id text,
    user_id uuid NOT NULL,
    relationship text NOT NULL,                -- 'mother', 'father', 'sibling', 'child', etc.
    relative_name text,
    relative_age int,                          -- current age or age at death
    vital_status text,                         -- 'alive', 'deceased', 'unknown'
    cause_of_death text,
    condition_name text,                       -- e.g. 'diabetes', 'hypertension', 'breast cancer'
    icd10_code text,
    age_at_onset int,
    notes text,
    custom_fields jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    synced_at timestamp with time zone,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_health_family_history_tenant_user ON public.health_family_history (tenant_id, user_id);

-- Social history — one row per category (tobacco, alcohol, employment, etc.)
CREATE TABLE IF NOT EXISTS public.health_social_history (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sqlite_id text,
    user_id uuid NOT NULL,
    category text NOT NULL,                    -- 'tobacco_use', 'alcohol_use', 'drug_use',
                                                -- 'employment', 'education', 'marital_status',
                                                -- 'living_situation', 'religion', 'exercise', etc.
    status text,                               -- 'current', 'former', 'never' (substance use)
    detail text,
    quantity text,                             -- e.g. '1 pack/day', '2 drinks/week'
    start_date date,
    end_date date,
    notes text,
    custom_fields jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    synced_at timestamp with time zone,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_health_social_history_tenant_user ON public.health_social_history (tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_health_social_history_category ON public.health_social_history (tenant_id, user_id, category);

-- Surgical history — one row per procedure
CREATE TABLE IF NOT EXISTS public.health_surgical_history (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sqlite_id text,
    user_id uuid NOT NULL,
    procedure_name text NOT NULL,
    procedure_date date,
    surgeon text,
    facility text,
    outcome text,                              -- 'successful', 'complicated', free text
    complications text,
    transfusions boolean DEFAULT false,
    anesthesia_type text,                      -- 'general', 'regional', 'local', 'sedation'
    notes text,
    custom_fields jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    synced_at timestamp with time zone,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_health_surgical_history_tenant_user ON public.health_surgical_history (tenant_id, user_id);

-- Remedies (condition-based input groupings)
CREATE TABLE IF NOT EXISTS public.remedies (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sqlite_id text,
    user_id uuid NOT NULL,
    condition_id uuid,
    name text NOT NULL,
    description text,
    effectiveness_rating integer,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    synced_at timestamp with time zone,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, condition_id) REFERENCES public.health_conditions(tenant_id, id) ON DELETE SET NULL
);

-- Remedy inputs (junction table)
CREATE TABLE IF NOT EXISTS public.remedy_inputs (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sqlite_id text,
    user_id uuid NOT NULL,
    remedy_id uuid NOT NULL,
    input_id uuid NOT NULL,
    dosage_for_remedy text,
    notes text,
    created_at timestamp with time zone DEFAULT now(),
    synced_at timestamp with time zone,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, remedy_id) REFERENCES public.remedies(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, input_id) REFERENCES public.health_inputs(tenant_id, id) ON DELETE CASCADE
);

-- ============================================================================
-- HEALTH LOGGING
-- ============================================================================

-- Health input log (medication/supplement intake)
CREATE TABLE IF NOT EXISTS public.health_input_log (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sqlite_id text,
    user_id uuid NOT NULL,
    logged_at timestamp with time zone DEFAULT now(),
    input_id uuid,
    dosage_taken text,
    notes text,
    stack_id uuid,
    skipped boolean DEFAULT false,
    skip_reason text,
    free_text text,
    free_dosage text,
    promoted_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    synced_at timestamp with time zone,
    PRIMARY KEY (tenant_id, id),
    CONSTRAINT chk_input_or_text CHECK (input_id IS NOT NULL OR free_text IS NOT NULL),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, input_id) REFERENCES public.health_inputs(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, stack_id) REFERENCES public.stacks(tenant_id, id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_health_input_log_tenant_user_date ON public.health_input_log USING btree (tenant_id, user_id, logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_input_log_freeform ON public.health_input_log (tenant_id, user_id, free_text) WHERE input_id IS NULL AND free_text IS NOT NULL;

-- Health observations (user notes)
CREATE TABLE IF NOT EXISTS public.health_observations (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sqlite_id text,
    user_id uuid NOT NULL,
    observed_at timestamp with time zone DEFAULT now(),
    category text,
    content text NOT NULL,
    severity integer,
    mental_health_flag boolean DEFAULT false,
    related_inputs uuid[],
    tags text[],
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    synced_at timestamp with time zone,
    embedding_content vector(768),              -- pgvector: observation content
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_health_observations_tenant_user_date ON public.health_observations USING btree (tenant_id, user_id, observed_at DESC);

-- ============================================================================
-- VITALS & METRICS
-- ============================================================================

-- Blood pressure readings
CREATE TABLE IF NOT EXISTS public.health_blood_pressure_readings (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sqlite_id text,
    user_id uuid NOT NULL,
    measured_at timestamp with time zone DEFAULT now(),
    systolic integer NOT NULL,
    diastolic integer NOT NULL,
    pulse integer,
    position text,
    arm text,
    notes text,
    device text,
    created_at timestamp with time zone DEFAULT now(),
    synced_at timestamp with time zone,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE,
    CONSTRAINT bp_systolic_range CHECK ((systolic > 0 AND systolic < 300)),
    CONSTRAINT bp_diastolic_range CHECK ((diastolic > 0 AND diastolic < 200))
);

CREATE INDEX IF NOT EXISTS idx_bp_tenant_user_date ON public.health_blood_pressure_readings USING btree (tenant_id, user_id, measured_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_bp_sync_dedupe ON public.health_blood_pressure_readings USING btree (tenant_id, user_id, measured_at, systolic, diastolic);

-- Blood work / lab results
CREATE TABLE IF NOT EXISTS public.health_blood_work (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sqlite_id text,
    user_id uuid NOT NULL,
    test_date date NOT NULL,
    test_name text NOT NULL,
    value numeric,
    unit text,
    reference_range text,
    is_abnormal boolean,
    lab_name text,
    loinc_code text,                       -- LOINC code (e.g., '4548-4' for HbA1c)
    panel_name text,                       -- e.g., 'CBC', 'CMP', 'Lipid Panel'
    notes text,
    created_at timestamp with time zone DEFAULT now(),
    synced_at timestamp with time zone,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_blood_work_tenant_user_date ON public.health_blood_work USING btree (tenant_id, user_id, test_date DESC);
CREATE INDEX IF NOT EXISTS idx_blood_work_loinc ON public.health_blood_work (tenant_id, user_id, loinc_code) WHERE loinc_code IS NOT NULL;

-- Generic health metrics (weight, temperature, etc.)
CREATE TABLE IF NOT EXISTS public.health_metrics (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sqlite_id text,
    user_id uuid NOT NULL,
    metric_type text NOT NULL,
    recorded_at timestamp with time zone DEFAULT now(),
    value numeric,  -- nullable for V7 compatibility
    unit text,
    source text,
    notes text,
    -- Optional pointer back to the source log row this metric was projected from
    -- (currently used by the nutrition projector for food_log → health_metrics).
    -- Nullable for HealthKit-imported and manually-entered rows. See
    -- Infrastructure/deltas/2026-05-01-nutrition_projection.sql.
    source_log_id uuid,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    synced_at timestamp with time zone,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE,
    CONSTRAINT health_metrics_type_check CHECK ((metric_type = ANY (ARRAY[
        'steps'::text,
        'heart_rate'::text,
        'resting_heart_rate'::text,
        'sleep'::text,
        'nutrition'::text,
        'active_energy_burned'::text,
        'basal_energy_burned'::text,
        'distance_walking_running'::text,
        'workout'::text,
        'workout_route'::text,
        'floors_climbed'::text,
        'wheelchair_pushes'::text,
        'hydration'::text,
        'heart_rate_variability'::text,
        'respiratory_rate'::text,
        'body_temperature'::text,
        'basal_body_temperature'::text,
        'medication'::text,
        'weight'::text,
        'height'::text,
        'body_fat_percentage'::text,
        'lean_body_mass'::text,
        'blood_glucose'::text,
        'oxygen_saturation'::text,
        'vo2_max'::text,
        'allergy_record'::text,
        'condition_record'::text,
        'immunization_record'::text,
        'lab_result_record'::text,
        'medication_record'::text,
        'procedure_record'::text,
        'vital_sign_record'::text,
        'temperature'::text,
        'blood_oxygen'::text,
        'apple_exercise_time'::text,
        'apple_stand_hour'::text,
        'mindful_session'::text
    ])))
);

CREATE INDEX IF NOT EXISTS idx_health_metrics_tenant_user_type ON public.health_metrics USING btree (tenant_id, user_id, metric_type, recorded_at DESC);
-- Sync-import dedupe: prevents the same external metric (HealthKit/Garmin)
-- from being inserted twice. Partial: only enforced for rows that did NOT
-- come from the projector (source_log_id IS NULL). Projected nutrition
-- rows dedupe via source_log_id instead — two food logs at the same minute
-- with the same calories are real distinct meals, not duplicates.
CREATE UNIQUE INDEX IF NOT EXISTS idx_health_metrics_sync_dedupe
    ON public.health_metrics
    USING btree (tenant_id, user_id, metric_type, recorded_at, value, unit, source)
    WHERE source_log_id IS NULL;
-- Provenance lookup: nutrition projector uses NOT EXISTS via source_log_id for idempotency.
CREATE INDEX IF NOT EXISTS idx_health_metrics_source_log
    ON public.health_metrics (tenant_id, source_log_id)
    WHERE source_log_id IS NOT NULL;

-- Vaccinations
CREATE TABLE IF NOT EXISTS public.health_vaccinations (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sqlite_id text,
    user_id uuid NOT NULL,
    vaccine_name text NOT NULL,
    administered_date date,
    lot_number text,
    site text,
    administered_by text,
    location text,
    next_dose_due date,
    reaction_notes text,
    created_at timestamp with time zone DEFAULT now(),
    synced_at timestamp with time zone,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_vaccinations_tenant_user ON public.health_vaccinations USING btree (tenant_id, user_id);

-- ============================================================================
-- FOOD TRACKING
-- ============================================================================

-- Food items database
CREATE TABLE IF NOT EXISTS public.health_food_itemsv2 (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sqlite_id text,
    user_id uuid NOT NULL,
    name text NOT NULL,
    brand text,
    barcode text,
    serving_size text,
    serving_unit text,
    calories numeric,
    protein_g numeric,
    carbs_g numeric,
    fat_g numeric,
    fiber_g numeric,
    sugar_g numeric,
    sodium_mg numeric,
    cholesterol_mg numeric,
    saturated_fat_g numeric,
    trans_fat_g numeric,
    potassium_mg numeric,
    vitamin_a_pct numeric,
    vitamin_c_pct numeric,
    calcium_pct numeric,
    iron_pct numeric,
    custom_nutrients jsonb,
    is_favorite boolean DEFAULT false,
    is_custom boolean DEFAULT true,
    source text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    synced_at timestamp with time zone,
    embedding_name vector(768),                 -- pgvector: food name semantic match
    fdc_id          INTEGER,                     -- Optional USDA FoodData Central id (resolved client-side); kept for Central sync compatibility. Home Edition keeps no local FDC cache.
    diet_flags      JSONB,                       -- Reserved diet-classification column, kept for Central sync compatibility. Not populated in Home Edition.
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_food_items_tenant_user ON public.health_food_itemsv2 USING btree (tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_food_items_barcode ON public.health_food_itemsv2 USING btree (barcode) WHERE (barcode IS NOT NULL);
CREATE INDEX IF NOT EXISTS idx_food_items_fdc ON public.health_food_itemsv2 USING btree (tenant_id, fdc_id) WHERE (fdc_id IS NOT NULL);

-- NOTE: Home Edition keeps no local USDA FoodData Central cache. The enterprise
-- fdc_food / fdc_food_portion / fdc_nutrient catalog tables (and the
-- health_food_itemsv2 → fdc_food FK) are intentionally absent. The fdc_id /
-- diet_flags columns above are retained for Central sync compatibility only.

-- Food log
CREATE TABLE IF NOT EXISTS public.health_food_logv2 (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sqlite_id text,
    user_id uuid NOT NULL,
    logged_at timestamp with time zone DEFAULT now(),
    food_item_id uuid,
    servings numeric DEFAULT 1,
    meal_type text,
    timeframe_id uuid,
    notes text,
    free_text text,
    photo_url text,
    promoted_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    synced_at timestamp with time zone,
    PRIMARY KEY (tenant_id, id),
    CONSTRAINT chk_food_or_text CHECK (food_item_id IS NOT NULL OR free_text IS NOT NULL),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, food_item_id) REFERENCES public.health_food_itemsv2(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, timeframe_id) REFERENCES public.timeframes(tenant_id, id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_food_log_tenant_user_date ON public.health_food_logv2 USING btree (tenant_id, user_id, logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_food_log_freeform ON public.health_food_logv2 (tenant_id, user_id, free_text) WHERE food_item_id IS NULL AND free_text IS NOT NULL;

-- Log promotions (AI/fuzzy-match suggestions linking freeform entries to catalog items)
CREATE TABLE IF NOT EXISTS public.log_promotions (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sqlite_id text,
    user_id uuid NOT NULL,
    source_table text NOT NULL CHECK (source_table IN ('health_input_log', 'health_food_logv2')),
    source_log_id uuid NOT NULL,
    suggested_catalog_table text CHECK (suggested_catalog_table IN ('health_inputs', 'health_food_itemsv2')),
    suggested_catalog_id uuid,
    free_text_original text NOT NULL,
    match_confidence real CHECK (match_confidence BETWEEN 0.0 AND 1.0),
    match_method text CHECK (match_method IN ('exact', 'fuzzy', 'ai', 'user')),
    status text DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'dismissed', 'auto_linked')),
    resolved_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    synced_at timestamp with time zone,
    is_deleted integer DEFAULT 0,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_promotions_pending ON public.log_promotions (tenant_id, user_id, status) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_promotions_source ON public.log_promotions (tenant_id, source_table, source_log_id);
CREATE INDEX IF NOT EXISTS idx_promotions_sqlite ON public.log_promotions (sqlite_id) WHERE sqlite_id IS NOT NULL;

-- Meals (pre-defined meal templates)
CREATE TABLE IF NOT EXISTS public.meals (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sqlite_id text,
    user_id uuid NOT NULL,
    name text NOT NULL,
    description text,
    meal_type text,
    is_favorite boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    synced_at timestamp with time zone,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_meals_tenant_user ON public.meals USING btree (tenant_id, user_id);

-- Meal items (junction table)
CREATE TABLE IF NOT EXISTS public.meal_items (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sqlite_id text,
    user_id uuid NOT NULL,
    meal_id uuid NOT NULL,
    food_item_id uuid NOT NULL,
    servings numeric DEFAULT 1,
    notes text,
    created_at timestamp with time zone DEFAULT now(),
    synced_at timestamp with time zone,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, meal_id) REFERENCES public.meals(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, food_item_id) REFERENCES public.health_food_itemsv2(tenant_id, id) ON DELETE CASCADE
);

-- ============================================================================
-- DIETARY SETTINGS (history-tracked preferences)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.dietary_settings (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    diet_type text,                          -- DEPRECATED: use diet_codes (backfill scheduled). 'standard', 'vegetarian', 'vegan', 'keto', 'paleo', 'mediterranean', etc.
    diet_codes text[] DEFAULT ARRAY['plant_based']::text[],
                                              -- Array of diet_catalog.code values; multi-diet support per Diets-Plan2.md (e.g. {'mediterranean','halal'}).
                                              -- Default 'plant_based' (vegan) per 2026-05-09 product directive: users with no expressed preference are vegan-by-default; opt-out via PUT /api/v1/dietary-settings.

    dietary_restrictions text[],             -- Finer-grained user-added exclusions: ['gluten_free', 'dairy_free', 'nut_free', ...]
    calorie_target integer,
    protein_target_g numeric,
    carb_target_g numeric,
    fat_target_g numeric,
    meal_count_per_day integer DEFAULT 3,
    notes text,
    is_active boolean DEFAULT true,
    effective_date date DEFAULT CURRENT_DATE,
    end_date date,                           -- NULL = current; set when superseded by PUT
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now(),
    deleted_at timestamptz,                   -- RxDB tombstone: NULL = active, non-NULL = soft-deleted
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_dietary_settings_tenant_user
    ON public.dietary_settings (tenant_id, user_id, is_active, effective_date DESC);
CREATE INDEX IF NOT EXISTS idx_dietary_settings_pull_checkpoint
    ON public.dietary_settings (tenant_id, updated_at, id);

-- ============================================================================
-- DIET CATALOG (reference data — 21 named diets)
-- ============================================================================
-- Read-only reference data (the application never writes to it at runtime). Categories:
--   exclusion        — vegetarian variants, kosher/halal, low-FODMAP, carnivore
--   nutrient_pattern — daily aggregate vs threshold (DASH, Zone, Mediterranean, low-carb, etc.)
--   medical          — clinically prescribed (renal, diabetic, healthy_kidney)
-- 'lifestyle' kept in CHECK for future use; no rows currently use it.
--
-- Home Edition uses this catalog purely as the diet-code reference that
-- dietary_settings validates against (and syncs via /diet-catalog/pull). The
-- enterprise per-food adherence-scoring machinery that consumed the excludes /
-- nutrient_targets / derivation_tier columns is not shipped here, so those
-- columns are retained as reference metadata only.

CREATE TABLE IF NOT EXISTS public.diet_catalog (
    tenant_id        SMALLINT NOT NULL DEFAULT 1,
    code             TEXT NOT NULL,
    display_name     TEXT NOT NULL,
    category         TEXT NOT NULL CHECK (category IN
                       ('exclusion','nutrient_pattern','medical','lifestyle')),
    description      TEXT,
    excludes         JSONB,                  -- {"fdc_food_categories": [...], "ingredients_substr": [...]}
    nutrient_targets JSONB,                  -- {"sodium_mg_max_per_day": 2300, ...}
    parent_diet_code TEXT,                   -- inheritance hint (e.g. lacto_vegetarian → plant_based)
    evidence_level   TEXT CHECK (evidence_level IN ('clinical','pattern','philosophical')),
    is_clinical      BOOLEAN NOT NULL DEFAULT false,    -- TRUE → clinically significant
    derivation_tier  TEXT NOT NULL DEFAULT 'clean'
                     CHECK (derivation_tier IN ('clean','approximate','deferred')),
                                              -- 'clean'        : full per-food badge + adherence scoring
                                              -- 'approximate'  : badges with documented caveats in notes
                                              -- 'deferred'     : catalog only; matcher returns 'unknown' until Phase 4 imports licensed data
    notes            TEXT,
    created_at       TIMESTAMPTZ DEFAULT now(),
    updated_at       TIMESTAMPTZ DEFAULT now(),    -- RxDB pull checkpoint: bumped on every write
    PRIMARY KEY (tenant_id, code),
    CONSTRAINT diet_catalog_parent_fkey
        FOREIGN KEY (tenant_id, parent_diet_code) REFERENCES public.diet_catalog(tenant_id, code)
);

CREATE INDEX IF NOT EXISTS idx_diet_catalog_category ON public.diet_catalog (tenant_id, category);
CREATE INDEX IF NOT EXISTS idx_diet_catalog_clinical ON public.diet_catalog (tenant_id, is_clinical) WHERE is_clinical = true;
CREATE INDEX IF NOT EXISTS idx_diet_catalog_pull_checkpoint ON public.diet_catalog (tenant_id, updated_at, code);

-- Seed: 21 diets. Order matters — parents (plant_based) before children
-- (lacto_vegetarian, ovo_vegetarian) due to diet_catalog_parent_fkey.
-- The derivation_tier / excludes / nutrient_targets values are retained as
-- reference metadata; Home Edition ships no per-food adherence scorer.

INSERT INTO public.diet_catalog
    (tenant_id, code, display_name, category, description, excludes, nutrient_targets, parent_diet_code, evidence_level, is_clinical, derivation_tier, notes)
VALUES
-- Exclusion-pattern (10) — Tier A clean (8) + Tier B approximate (2 vegetarian variants with ingredient-substring caveats)
(1, 'plant_based', 'Plant Based Diet', 'exclusion',
 'Excludes all animal products',
 '{"fdc_food_categories": ["Beef Products", "Pork Products", "Poultry Products", "Lamb, Veal, and Game Products", "Sausages and Luncheon Meats", "Finfish and Shellfish Products", "Dairy and Egg Products"]}'::jsonb,
 NULL, NULL, 'clinical', false, 'clean',
 'Vegan-equivalent. Parent of lacto_vegetarian and ovo_vegetarian for inheritance.'),
(1, 'lacto_vegetarian', 'Lacto-Vegetarian', 'exclusion',
 'Excludes meat, fish, and eggs; allows dairy',
 '{"fdc_food_categories": ["Beef Products", "Pork Products", "Poultry Products", "Lamb, Veal, and Game Products", "Sausages and Luncheon Meats", "Finfish and Shellfish Products"], "ingredients_substr": ["egg", "albumin"]}'::jsonb,
 NULL, 'plant_based', 'clinical', false, 'approximate',
 'Eggs only flagged when "egg" or "albumin" appears in the ingredient text.'),
(1, 'ovo_vegetarian', 'Ovo-Vegetarian', 'exclusion',
 'Excludes meat, fish, and dairy; allows eggs',
 '{"fdc_food_categories": ["Beef Products", "Pork Products", "Poultry Products", "Lamb, Veal, and Game Products", "Sausages and Luncheon Meats", "Finfish and Shellfish Products"], "ingredients_substr": ["milk", "cheese", "butter", "whey", "casein", "lactose"]}'::jsonb,
 NULL, 'plant_based', 'clinical', false, 'approximate',
 'Misses dairy hidden as "ghee," "casein," or in compound foods.'),
(1, 'ovo_lacto_vegetarian', 'Ovo-Lacto-Vegetarian', 'exclusion',
 'Excludes meat and fish; allows eggs and dairy',
 '{"fdc_food_categories": ["Beef Products", "Pork Products", "Poultry Products", "Lamb, Veal, and Game Products", "Sausages and Luncheon Meats", "Finfish and Shellfish Products"]}'::jsonb,
 NULL, NULL, 'clinical', false, 'clean',
 'Most common vegetarian variant in the US.'),
(1, 'pollotarian', 'Pollotarian', 'exclusion',
 'Excludes red meat and fish; allows poultry',
 '{"fdc_food_categories": ["Beef Products", "Pork Products", "Lamb, Veal, and Game Products", "Sausages and Luncheon Meats", "Finfish and Shellfish Products"]}'::jsonb,
 NULL, NULL, 'pattern', false, 'clean', NULL),
(1, 'kangatarian', 'Kangatarian', 'exclusion',
 'Excludes mammalian meat except kangaroo; allows kangaroo and fish',
 '{"fdc_food_categories": ["Beef Products", "Pork Products", "Lamb, Veal, and Game Products", "Sausages and Luncheon Meats"]}'::jsonb,
 NULL, NULL, 'philosophical', false, 'clean',
 'Australia-specific. Kangaroo is allowed but is not a distinct FDC category; included for catalog completeness.'),
(1, 'carnivore', 'Carnivore', 'exclusion',
 'Excludes plants entirely; eats only animal products',
 '{"fdc_food_categories": ["Vegetables and Vegetable Products", "Fruits and Fruit Juices", "Cereal Grains and Pasta", "Legumes and Legume Products", "Nut and Seed Products", "Spices and Herbs"]}'::jsonb,
 NULL, NULL, 'philosophical', false, 'clean',
 'Inverse of vegan; not standard clinical guidance.'),
(1, 'low_fodmap', 'Low-FODMAP Diet', 'exclusion',
 'Excludes fermentable oligo-, di-, monosaccharides and polyols',
 '{"ingredients_substr": ["onion", "garlic", "wheat", "rye", "honey", "high fructose"]}'::jsonb,
 NULL, NULL, 'clinical', false, 'deferred',
 'Tier C: matcher returns ''unknown'' until Phase 4 imports the Monash University FODMAP database.'),
(1, 'kosher', 'Kosher', 'exclusion',
 'Excludes pork, shellfish, and meat-dairy mixtures',
 '{"fdc_food_categories": ["Pork Products"], "ingredients_substr": ["pork", "bacon", "ham", "shrimp", "lobster", "crab", "shellfish", "gelatin"]}'::jsonb,
 NULL, NULL, 'pattern', false, 'approximate',
 'System filters known violators only. Users verify (K) or (U) certification on packaging at purchase. Meat-dairy separation is out of scope in v1.'),
(1, 'halal', 'Halal', 'exclusion',
 'Excludes pork, alcohol-derived ingredients',
 '{"fdc_food_categories": ["Pork Products"], "ingredients_substr": ["pork", "bacon", "ham", "lard", "alcohol", "wine", "rum", "bourbon", "vanilla extract", "gelatin"]}'::jsonb,
 NULL, NULL, 'pattern', false, 'approximate',
 'System filters known violators only. Users verify Halal certification on packaging at purchase. Slaughter-method enforcement is out of scope in v1.'),
-- Nutrient-pattern (8) — Tier A clean (5) + Tier B approximate (1 mediterranean) + Tier C deferred (2)
(1, 'dash', 'DASH (low sodium, low fat)', 'nutrient_pattern',
 'Dietary Approaches to Stop Hypertension',
 NULL,
 '{"sodium_mg_max_per_day": 2300, "saturated_fat_g_max_per_day": 16}'::jsonb,
 NULL, 'clinical', false, 'clean',
 'NIH-recommended for blood pressure. Tighter 1500mg/day sodium variant is not modeled separately in v1.'),
(1, 'zone', 'Zone Diet', 'nutrient_pattern',
 '40% carbohydrates / 30% protein / 30% fat',
 NULL,
 '{"carbs_pct_calories_target": 0.40, "protein_pct_calories_target": 0.30, "fat_pct_calories_target": 0.30}'::jsonb,
 NULL, 'pattern', false, 'clean', NULL),
(1, 'mediterranean', 'Mediterranean', 'nutrient_pattern',
 'High vegetables, olive oil, fish; limited red meat and processed foods',
 '{"fdc_food_categories_limit": ["Beef Products", "Pork Products", "Sausages and Luncheon Meats"]}'::jsonb,
 '{"fiber_g_min_per_day": 25}'::jsonb,
 NULL, 'clinical', false, 'approximate',
 'Pattern, not a rulebook — soft limits. Limits red meat; scores fiber daily.'),
(1, 'low_carb', 'Low Carb', 'nutrient_pattern',
 'Carbohydrate intake below 100g/day',
 NULL,
 '{"carbs_g_max_per_day": 100}'::jsonb,
 NULL, 'clinical', false, 'clean', NULL),
(1, 'low_glycemic', 'Low Glycemic Index Diet', 'nutrient_pattern',
 'Foods with glycemic index ≤ 55',
 NULL,
 '{"glycemic_index_max": 55}'::jsonb,
 NULL, 'clinical', false, 'deferred',
 'Tier C: matcher returns ''unknown'' until Phase 4 imports a GI database.'),
(1, 'high_protein', 'High Protein Diet', 'nutrient_pattern',
 'Protein at or above 30% of calories',
 NULL,
 '{"protein_pct_calories_min": 0.30}'::jsonb,
 NULL, 'pattern', false, 'clean', NULL),
(1, 'high_residue', 'High Residue (Fiber) Diet', 'nutrient_pattern',
 'At least 25g fiber per day',
 NULL,
 '{"fiber_g_min_per_day": 25}'::jsonb,
 NULL, 'clinical', false, 'clean',
 'Also called High Fiber Diet.'),
(1, 'alkaline', 'Alkaline Diet', 'nutrient_pattern',
 'Favors alkaline-forming foods (PRAL-based)',
 NULL,
 NULL,
 NULL, 'philosophical', false, 'deferred',
 'Tier C: matcher returns ''unknown'' until Phase 4 imports PRAL tables. Weak peer-reviewed support.'),
-- Medical / clinical (3) — all Tier A clean
(1, 'diabetic', 'Diabetic Diet', 'medical',
 'Carb-counted, glycemic-controlled diet',
 NULL,
 '{"carbs_g_max_per_meal": 60, "added_sugar_g_max_per_day": 25}'::jsonb,
 NULL, 'clinical', true, 'clean',
 'Targets vary by patient HbA1c, medications, and provider guidance. Numbers here are conservative defaults.'),
(1, 'healthy_kidney', 'Healthy Kidney Diet', 'medical',
 'Early-stage CKD: moderate sodium and protein',
 NULL,
 '{"sodium_mg_max_per_day": 2300, "protein_g_max_per_day": 80}'::jsonb,
 NULL, 'clinical', true, 'clean',
 'Pre-dialysis CKD stages 1-3; less restrictive than Renal.'),
(1, 'renal', 'Renal Diet', 'medical',
 'Advanced CKD: low potassium, phosphorus, sodium, and fluid',
 NULL,
 '{"potassium_mg_max_per_day": 2000, "phosphorus_mg_max_per_day": 1000, "sodium_mg_max_per_day": 2000}'::jsonb,
 NULL, 'clinical', true, 'clean',
 'CKD stages 4-5 / dialysis. Provider guidance is critical; numbers vary by patient.')
ON CONFLICT (tenant_id, code) DO NOTHING;

-- ============================================================================
-- HOUSEHOLDS (multi-person planning)
-- ============================================================================
-- Scoping is conservative: a user sees and manages only households they
-- created and only their own membership rows. Household-aware planning can
-- later broaden cross-member visibility for diet_codes via a dedicated view.

CREATE TABLE IF NOT EXISTS public.households (
    tenant_id       SMALLINT NOT NULL DEFAULT 1,
    id              UUID NOT NULL DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    household_type  TEXT NOT NULL CHECK (household_type IN
                      ('family','roommates','group_home','clinical_facility')),
    created_by      UUID NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, created_by) REFERENCES public.users(tenant_id, id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_households_tenant_creator ON public.households (tenant_id, created_by);

CREATE TABLE IF NOT EXISTS public.household_members (
    tenant_id       SMALLINT NOT NULL DEFAULT 1,
    household_id    UUID NOT NULL,
    user_id         UUID NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('admin','cook','planner','eater')),
    joined_at       TIMESTAMPTZ DEFAULT now(),
    left_at         TIMESTAMPTZ,                 -- NULL = currently active
    PRIMARY KEY (tenant_id, household_id, user_id),
    FOREIGN KEY (tenant_id, household_id) REFERENCES public.households(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_household_members_tenant_user ON public.household_members (tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_household_members_active ON public.household_members (tenant_id, household_id) WHERE left_at IS NULL;

-- ============================================================================
-- REMINDERS
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.reminders (
    tenant_id       SMALLINT    NOT NULL DEFAULT 1,
    id              UUID        NOT NULL DEFAULT gen_random_uuid(),
    user_id         UUID        NOT NULL,
    title           TEXT        NOT NULL,
    category        TEXT        NOT NULL DEFAULT 'medication',
    time            TEXT        NOT NULL,          -- HH:mm format
    frequency       TEXT        NOT NULL DEFAULT 'daily',
    custom_days     INTEGER[],                     -- 0=Sun … 6=Sat (used when frequency='custom')
    timezone        TEXT,
    snooze_minutes  INTEGER,
    privacy_level   TEXT                 DEFAULT 'normal',
    notes           TEXT,
    enabled         BOOLEAN              DEFAULT TRUE,
    completed       BOOLEAN              DEFAULT FALSE,
    completed_at    TIMESTAMPTZ,
    snoozed_until   TIMESTAMPTZ,
    last_triggered  TIMESTAMPTZ,
    health_input_id UUID,
    created_at      TIMESTAMPTZ          DEFAULT NOW(),
    updated_at      TIMESTAMPTZ          DEFAULT NOW(),
    sqlite_id       TEXT,                         -- Mobile sync ID
    synced_at       TIMESTAMPTZ,

    PRIMARY KEY (tenant_id, id),

    CONSTRAINT fk_reminders_user
        FOREIGN KEY (tenant_id, user_id)
        REFERENCES public.users (tenant_id, id)
        ON DELETE CASCADE,

    CONSTRAINT chk_reminders_category
        CHECK (category IN ('medication', 'health-check', 'activity', 'hydration', 'appointment')),

    CONSTRAINT chk_reminders_frequency
        CHECK (frequency IN ('daily', 'weekly', 'monthly', 'custom', 'once')),

    CONSTRAINT chk_reminders_privacy_level
        CHECK (privacy_level IN ('normal', 'private', 'hidden'))
);

CREATE INDEX IF NOT EXISTS idx_reminders_tenant_user
    ON public.reminders (tenant_id, user_id);

CREATE INDEX IF NOT EXISTS idx_reminders_tenant_category
    ON public.reminders (tenant_id, category);

CREATE INDEX IF NOT EXISTS idx_reminders_sqlite_id
    ON public.reminders (tenant_id, sqlite_id) WHERE sqlite_id IS NOT NULL;

-- ============================================================================
-- APPOINTMENTS (one-time medical events with lead-time reminders)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.appointments (
    tenant_id           SMALLINT NOT NULL DEFAULT 1,
    id                  UUID NOT NULL DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL,
    sqlite_id           TEXT,                           -- Mobile sync ID

    -- Event details
    title               TEXT NOT NULL,
    appointment_datetime TIMESTAMPTZ NOT NULL,          -- Full datetime of event
    duration_minutes    INTEGER,                        -- Expected duration
    location            TEXT,
    provider_id         UUID,                           -- Optional: linked contact (user_provider_contacts)
    notes               TEXT,

    -- Reminder configuration
    reminder_lead_times INTEGER[] DEFAULT '{1440, 60}', -- Minutes before (24h, 1h)
    reminder_enabled    BOOLEAN DEFAULT TRUE,

    -- Status tracking
    status              TEXT DEFAULT 'scheduled'
                        CHECK (status IN ('scheduled', 'completed', 'cancelled', 'no_show')),
    completed_at        TIMESTAMPTZ,

    -- Timestamps
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    synced_at           TIMESTAMPTZ,

    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_appointments_user_datetime
    ON public.appointments(tenant_id, user_id, appointment_datetime);
CREATE INDEX IF NOT EXISTS idx_appointments_status
    ON public.appointments(tenant_id, status) WHERE status = 'scheduled';
CREATE INDEX IF NOT EXISTS idx_appointments_sqlite_id
    ON public.appointments(tenant_id, sqlite_id) WHERE sqlite_id IS NOT NULL;

-- ============================================================================
-- PROJECTED REMINDERS (derived from timeframes for meds/supplements)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.projected_reminders (
    tenant_id           SMALLINT NOT NULL DEFAULT 1,
    id                  UUID NOT NULL DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL,
    sqlite_id           TEXT,                           -- Mobile sync ID

    -- Source linkage (exactly one must be set)
    stack_id            UUID,                           -- Projected from stack
    health_input_id     UUID,                           -- Or from standalone input

    -- Derived from timeframe
    timeframe_id        UUID NOT NULL,                  -- Source timeframe
    scheduled_time      TIME NOT NULL,                  -- Copied from timeframe.time_of_day

    -- Recurrence (from timeframe or input settings)
    frequency           TEXT NOT NULL DEFAULT 'daily'
                        CHECK (frequency IN ('daily', 'weekly', 'monthly', 'annual', 'custom', 'once')),
    custom_days         INTEGER[],                      -- 0-6 for day-of-week
    start_date          DATE,                           -- Anchor date for recurrence

    -- Timezone behavior
    timezone_mode       TEXT DEFAULT 'local'
                        CHECK (timezone_mode IN ('home', 'local')),

    -- State
    enabled             BOOLEAN DEFAULT TRUE,
    snoozed_until       TIMESTAMPTZ,

    -- Timestamps
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    synced_at           TIMESTAMPTZ,

    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES public.users(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, stack_id)
        REFERENCES public.stacks(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, health_input_id)
        REFERENCES public.health_inputs(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, timeframe_id)
        REFERENCES public.timeframes(tenant_id, id) ON DELETE CASCADE,

    -- Ensure exactly one source
    CONSTRAINT chk_projected_reminder_source
        CHECK ((stack_id IS NOT NULL AND health_input_id IS NULL) OR
               (stack_id IS NULL AND health_input_id IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_projected_reminders_user
    ON public.projected_reminders(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_projected_reminders_timeframe
    ON public.projected_reminders(tenant_id, timeframe_id);
CREATE INDEX IF NOT EXISTS idx_projected_reminders_stack
    ON public.projected_reminders(tenant_id, stack_id) WHERE stack_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_projected_reminders_health_input
    ON public.projected_reminders(tenant_id, health_input_id) WHERE health_input_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_projected_reminders_sqlite_id
    ON public.projected_reminders(tenant_id, sqlite_id) WHERE sqlite_id IS NOT NULL;

-- ============================================================================
-- DATA CORRECTIONS
-- ============================================================================
-- Tracks user corrections to synced health records (exercise names, food names).
-- Provides an audit trail for PHI modifications.

CREATE TABLE IF NOT EXISTS public.data_corrections (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       SMALLINT    NOT NULL DEFAULT 1 REFERENCES public.tenants(id),
    user_id         UUID        NOT NULL,
    record_type     TEXT        NOT NULL CHECK (record_type IN ('workout', 'food')),
    source_app      TEXT,
    corrected_field TEXT        NOT NULL CHECK (corrected_field IN ('activityType', 'food_name')),
    original_value  TEXT,
    new_value       TEXT        NOT NULL,
    corrected_at    TIMESTAMPTZ DEFAULT NOW(),

    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users (tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_data_corrections_user
    ON public.data_corrections USING btree (tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_data_corrections_date
    ON public.data_corrections USING btree (corrected_at);

-- ============================================================================
-- SYNC INFRASTRUCTURE
-- ============================================================================

-- Sync queue for mobile SQLite sync
CREATE TABLE IF NOT EXISTS public.sync_queue (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    table_name text NOT NULL,
    record_id uuid NOT NULL,
    operation text NOT NULL,
    payload jsonb,
    status text DEFAULT 'pending',
    retry_count integer DEFAULT 0,
    error_message text,
    created_at timestamp with time zone DEFAULT now(),
    processed_at timestamp with time zone,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE,
    CONSTRAINT sync_queue_operation_check CHECK ((operation = ANY (ARRAY['insert'::text, 'update'::text, 'delete'::text]))),
    CONSTRAINT sync_queue_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'syncing'::text, 'synced'::text, 'failed'::text])))
);

CREATE INDEX IF NOT EXISTS idx_sync_queue_tenant_user_status ON public.sync_queue USING btree (tenant_id, user_id, status);

-- Schema versions tracking (per-device)
CREATE TABLE IF NOT EXISTS public.schema_versions (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    device_id text NOT NULL,
    schema_version integer NOT NULL,
    app_version text,
    platform text,
    last_sync timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, user_id, device_id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

-- ============================================================================
-- GARMIN INTEGRATION
-- ============================================================================

-- Garmin daily summary
CREATE TABLE IF NOT EXISTS public.garm_daily_summ (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    user_id uuid NOT NULL,
    calendar_date date NOT NULL,
    total_steps integer,
    daily_step_goal integer,
    total_distance_meters integer,
    active_time_secs integer,
    sedentary_time_secs integer,
    sleeping_time_secs integer,
    floors_climbed integer,
    floors_descended integer,
    intensity_minutes_goal integer,
    intensity_time_goal integer,  -- V7 compatibility (alias)
    moderate_intensity_minutes integer,
    moderate_activity_time integer,  -- V7 compatibility (alias)
    vigorous_intensity_minutes integer,
    vigorous_activity_time integer,  -- V7 compatibility (alias)
    avg_stress_level numeric,
    max_stress_level integer,
    min_heart_rate integer,
    max_heart_rate integer,
    resting_heart_rate integer,
    avg_heart_rate integer,
    bmr_kcals integer,
    active_kcals integer,
    total_kcals integer,
    calories_goal integer,  -- V7 compatibility
    calories_consumed integer,  -- V7 compatibility
    hydration_goal integer,  -- V7 compatibility
    hydration_intake integer,  -- V7 compatibility
    sweat_loss integer,  -- V7 compatibility
    body_battery_charged integer,
    body_battery_drained integer,
    body_battery_high integer,
    body_battery_low integer,
    spo2_avg numeric,
    spo2_low numeric,
    respiration_avg numeric,
    respiration_high numeric,
    respiration_low numeric,
    description text,  -- V7 compatibility
    PRIMARY KEY (tenant_id, user_id, calendar_date),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

-- Garmin heart rate
CREATE TABLE IF NOT EXISTS public.garm_hr (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    user_id uuid NOT NULL,
    "timestamp" timestamp with time zone NOT NULL,
    heart_rate integer,  -- nullable for V7 compatibility
    PRIMARY KEY (tenant_id, user_id, "timestamp"),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

-- Garmin respiratory rate
CREATE TABLE IF NOT EXISTS public.garm_rr (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    user_id uuid NOT NULL,
    "timestamp" timestamp with time zone NOT NULL,
    respiratory_rate numeric,  -- nullable for V7 compatibility
    PRIMARY KEY (tenant_id, user_id, "timestamp"),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

-- Garmin sleep
CREATE TABLE IF NOT EXISTS public.garm_sleep (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    user_id uuid NOT NULL,
    calendar_date date NOT NULL,
    sleep_start timestamp with time zone,
    sleep_end timestamp with time zone,
    deep_sleep_secs integer,
    light_sleep_secs integer,
    rem_sleep_secs integer,
    awake_secs integer,
    avg_respiration numeric,
    avg_spo2 numeric,
    avg_stress numeric,
    sleep_score integer,
    sleep_score_quality text,
    PRIMARY KEY (tenant_id, user_id, calendar_date),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

-- Garmin sleep events
CREATE TABLE IF NOT EXISTS public.garm_sleep_events (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    user_id uuid NOT NULL,
    start_time timestamp with time zone NOT NULL,
    end_time timestamp with time zone NOT NULL,
    sleep_type text NOT NULL,
    PRIMARY KEY (tenant_id, user_id, start_time),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

-- Garmin stress
CREATE TABLE IF NOT EXISTS public.garm_stress (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    user_id uuid NOT NULL,
    "timestamp" timestamp with time zone NOT NULL,
    garm_stress integer,  -- nullable for V7 compatibility
    PRIMARY KEY (tenant_id, user_id, "timestamp"),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

-- Performance indexes for the large Garmin time-series tables
-- These enable efficient per-user "most recent first" queries
CREATE INDEX IF NOT EXISTS idx_garm_stress_tenant_user ON public.garm_stress(tenant_id, user_id, "timestamp" DESC);
CREATE INDEX IF NOT EXISTS idx_garm_hr_tenant_user ON public.garm_hr(tenant_id, user_id, "timestamp" DESC);
CREATE INDEX IF NOT EXISTS idx_garm_rr_tenant_user ON public.garm_rr(tenant_id, user_id, "timestamp" DESC);

-- Garmin upload tracking
CREATE TABLE IF NOT EXISTS public.garm_upload_date (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    upload_timestamp timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    upload_date date,
    status text DEFAULT 'success',
    notes text,  -- V7 compatibility
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

-- Garmin credentials
CREATE TABLE IF NOT EXISTS public.garmin_credentials (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    oauth1_token text,
    oauth1_secret text,
    encrypted_password text,
    email text,
    last_sync timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, user_id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

-- Garmin sync jobs
CREATE TABLE IF NOT EXISTS public.garmin_sync_jobs (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    job_type text NOT NULL,
    status text DEFAULT 'pending',
    start_date date,
    end_date date,
    progress jsonb,
    error_message text,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE,
    CONSTRAINT garmin_sync_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'completed'::text, 'failed'::text, 'cancelled'::text])))
);

CREATE INDEX IF NOT EXISTS idx_garmin_sync_tenant_user_status ON public.garmin_sync_jobs USING btree (tenant_id, user_id, status);

-- ============================================================================
-- HEALTHKIT INTEGRATION
-- ============================================================================

-- HealthKit record types (lookup table - no tenant, global reference)
CREATE TABLE IF NOT EXISTS public.hkit_record_types (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    type_identifier text NOT NULL UNIQUE,
    display_name text,
    category text,
    unit text
);

-- HealthKit sources (per-user devices/apps)
CREATE TABLE IF NOT EXISTS public.hkit_sources (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id integer GENERATED ALWAYS AS IDENTITY,
    user_id uuid NOT NULL,
    source_name text NOT NULL,
    source_bundle_id text,
    source_version text,
    device_name text,
    device_model text,
    created_at timestamp with time zone DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, user_id, source_bundle_id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

-- HealthKit records
CREATE TABLE IF NOT EXISTS public.hkit_records (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id bigint GENERATED ALWAYS AS IDENTITY,
    user_id uuid NOT NULL,
    record_type_id integer REFERENCES public.hkit_record_types(id),
    source_id integer,
    value numeric,
    unit text,
    start_date timestamp with time zone,
    end_date timestamp with time zone,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_hkit_records_tenant_user_type ON public.hkit_records USING btree (tenant_id, user_id, record_type_id, start_date DESC);

-- Dedup index: same record type from same source at same time is a duplicate
CREATE UNIQUE INDEX IF NOT EXISTS idx_hkit_records_dedup
    ON public.hkit_records (tenant_id, user_id, record_type_id, source_id, start_date, end_date);

-- HealthKit activity summaries
-- move_time / move_time_goal support the wheelchair "Move Time" ring
-- (HKActivitySummary.appleMoveTime and appleMoveTimeGoal), introduced in iOS 11.
-- Regular walking users will have exercise_time populated and move_time NULL;
-- wheelchair users will have move_time populated and exercise_time NULL.
CREATE TABLE IF NOT EXISTS public.hkit_activity_summaries (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id bigint GENERATED ALWAYS AS IDENTITY,
    user_id uuid NOT NULL,
    date date NOT NULL,
    active_energy_burned numeric,
    active_energy_burned_goal numeric,
    exercise_time integer,
    exercise_time_goal integer,
    stand_hours integer,
    stand_hours_goal integer,
    move_time integer,
    move_time_goal integer,
    created_at timestamp with time zone DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, user_id, date),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

-- HealthKit workouts
CREATE TABLE IF NOT EXISTS public.hkit_workouts (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id bigint GENERATED ALWAYS AS IDENTITY,
    user_id uuid NOT NULL,
    workout_type text NOT NULL,
    source_id integer,
    start_date timestamp with time zone,
    end_date timestamp with time zone,
    duration_seconds numeric,
    total_distance numeric,
    total_energy_burned numeric,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_hkit_workouts_tenant_user_date ON public.hkit_workouts USING btree (tenant_id, user_id, start_date DESC);

-- HealthKit user profile
CREATE TABLE IF NOT EXISTS public.hkit_user_profile (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id integer GENERATED ALWAYS AS IDENTITY,
    user_id uuid NOT NULL,
    date_of_birth date,
    biological_sex text,
    blood_type text,
    fitzpatrick_skin_type text,
    wheelchair_use boolean,
    updated_at timestamp with time zone DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, user_id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

-- HealthKit clinical records
CREATE TABLE IF NOT EXISTS public.hkit_clinical_records (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id integer GENERATED ALWAYS AS IDENTITY,
    user_id uuid NOT NULL,
    fhir_resource_type text NOT NULL,
    fhir_identifier text,
    fhir_source_url text,
    display_name text,
    received_date timestamp with time zone,
    raw_fhir jsonb,
    created_at timestamp with time zone DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_hkit_clinical_tenant_user ON public.hkit_clinical_records USING btree (tenant_id, user_id);

-- Dedup index: a re-imported FHIR resource carries the same fhir_identifier
-- as the original. Without this index, importing the same Apple Health export
-- twice would double the row count. Partial WHERE handles the (currently
-- nonexistent) NULL-identifier case forward-compat.
CREATE UNIQUE INDEX IF NOT EXISTS ux_hkit_clinical_records_fhir_id
    ON public.hkit_clinical_records (tenant_id, user_id, fhir_identifier)
    WHERE fhir_identifier IS NOT NULL;

-- HealthKit lab observations
CREATE TABLE IF NOT EXISTS public.hkit_lab_observations (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id integer GENERATED ALWAYS AS IDENTITY,
    user_id uuid NOT NULL,
    clinical_record_id integer,
    loinc_code text,
    display_name text,
    value_quantity numeric,
    value_unit text,
    value_string text,
    reference_range text,
    interpretation text,
    effective_date timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_hkit_labs_tenant_user ON public.hkit_lab_observations USING btree (tenant_id, user_id);

-- Dedup index: each parent hkit_clinical_records row may contribute at most
-- one extracted lab observation today. If a future extractor learns to emit
-- multiple sub-rows per parent (e.g. multi-component lab panels), this needs
-- a discriminator column added.
CREATE UNIQUE INDEX IF NOT EXISTS ux_hkit_lab_observations_parent
    ON public.hkit_lab_observations (tenant_id, user_id, clinical_record_id)
    WHERE clinical_record_id IS NOT NULL;

-- HealthKit allergies
CREATE TABLE IF NOT EXISTS public.hkit_allergies (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id integer GENERATED ALWAYS AS IDENTITY,
    user_id uuid NOT NULL,
    clinical_record_id integer,
    allergen text NOT NULL,
    reaction text,
    severity text,
    onset_date timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_hkit_allergies_parent
    ON public.hkit_allergies (tenant_id, user_id, clinical_record_id)
    WHERE clinical_record_id IS NOT NULL;

-- HealthKit immunizations
CREATE TABLE IF NOT EXISTS public.hkit_immunizations (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id integer GENERATED ALWAYS AS IDENTITY,
    user_id uuid NOT NULL,
    clinical_record_id integer,
    vaccine_code text,
    vaccine_name text NOT NULL,
    administered_date timestamp with time zone,
    lot_number text,
    created_at timestamp with time zone DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_hkit_immunizations_parent
    ON public.hkit_immunizations (tenant_id, user_id, clinical_record_id)
    WHERE clinical_record_id IS NOT NULL;

-- HealthKit medications
CREATE TABLE IF NOT EXISTS public.hkit_medications (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id integer GENERATED ALWAYS AS IDENTITY,
    user_id uuid NOT NULL,
    clinical_record_id integer,
    medication_code text,
    medication_name text NOT NULL,
    dosage text,
    status text,
    authored_date timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_hkit_medications_parent
    ON public.hkit_medications (tenant_id, user_id, clinical_record_id)
    WHERE clinical_record_id IS NOT NULL;

-- HealthKit incremental-sync anchors (Phase D of the HealthKit Consistency Plan).
-- Stores the opaque HKAnchoredObjectQuery token the mobile client last processed,
-- keyed by device so a reinstall can resume without re-reading the full 5-year window.
CREATE TABLE IF NOT EXISTS public.hkit_sync_anchors (
    tenant_id   SMALLINT    NOT NULL DEFAULT 1,
    user_id     uuid        NOT NULL,
    device_id   text        NOT NULL,
    sample_type text        NOT NULL,
    anchor      text        NOT NULL,
    updated_at  timestamp with time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_id, device_id, sample_type),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

-- HealthKit import jobs
CREATE TABLE IF NOT EXISTS public.healthkit_import_jobs (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    status text DEFAULT 'pending',
    total_records integer,
    processed_records integer DEFAULT 0,
    error_message text,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

-- Appointment prep: user prepares a health data snapshot before an appointment
CREATE TABLE IF NOT EXISTS public.appointment_prep (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    appointment_date date NOT NULL,
    date_range_start date NOT NULL,
    date_range_end date NOT NULL,
    observations text,
    health_data_snapshot jsonb,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id)
);

CREATE INDEX IF NOT EXISTS idx_appointment_prep_user_date ON public.appointment_prep(tenant_id, user_id, appointment_date DESC);

-- ============================================================================
-- USER DEVICES (device registry for embedding capability + analytics)
-- ============================================================================

-- One record per physical device per user. Tracks hardware capabilities
-- (especially embedding support) and device lifecycle (first seen / last seen).
-- Updated on every sync or API call that includes device_capabilities.
CREATE TABLE IF NOT EXISTS public.user_devices (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    user_id UUID NOT NULL,

    -- Device identity
    device_id TEXT NOT NULL,                    -- Client-generated stable ID (persisted in AsyncStorage/Keychain)
    device_name TEXT,                           -- "My iPhone", "Pixel 8 Pro"
    platform TEXT,                              -- 'ios', 'android', 'web'
    os_version TEXT,                            -- "iOS 18.3", "Android 15"
    app_version TEXT,                           -- "2.4.1"

    -- Hardware
    device_model TEXT,                          -- "iPhone11,8" (XR), "SM-S928B" (S25)
    ram_mb INTEGER,                             -- Total RAM in MB (reported by app)

    -- Embedding capabilities (updated each sync)
    can_embed BOOLEAN DEFAULT false,            -- Runtime capability check result
    embed_model TEXT,                           -- "nomic-embed-text-v1.5" or NULL
    embed_model_version TEXT,                   -- "1.5.0-onnx-int4" or NULL
    embed_dimensions SMALLINT,                  -- 768 or NULL

    -- Lifecycle
    first_seen_at TIMESTAMPTZ DEFAULT now(),
    last_seen_at TIMESTAMPTZ DEFAULT now(),

    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, user_id, device_id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_user_devices_tenant_user ON public.user_devices(tenant_id, user_id);

-- ============================================================================
-- USER DATA TABLES (need full CRUD - use FOR ALL with WITH CHECK)
-- ============================================================================

-- households / household_members: conservative own-row scoping. Household-
-- aware planning may later broaden cross-member visibility for diet_codes
-- via a dedicated view — recursive evaluation across household_members
-- must be designed
-- carefully to avoid the self-reference loop.


-- ============================================================================
-- HELPER FUNCTIONS
-- ============================================================================

-- Updated timestamp trigger
CREATE OR REPLACE FUNCTION public.update_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

-- Apply updated_at triggers only where the application doesn't already manage
-- updated_at explicitly. See DataModel3/UpdatedAtPolicy.md for the rule:
-- routes that SET updated_at = NOW() themselves need no trigger; tables with
-- no UPDATE path need neither until one is added.

-- History tables — defensive: no UPDATE paths today, but PHI-classified
-- and likely to gain edit routes. See UpdatedAtPolicy.md for the
-- "trigger as belt + route-side bump as suspenders" rationale.
DROP TRIGGER IF EXISTS update_health_family_history_updated_at ON public.health_family_history;
CREATE TRIGGER update_health_family_history_updated_at BEFORE UPDATE ON public.health_family_history
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();

DROP TRIGGER IF EXISTS update_health_social_history_updated_at ON public.health_social_history;
CREATE TRIGGER update_health_social_history_updated_at BEFORE UPDATE ON public.health_social_history
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();

DROP TRIGGER IF EXISTS update_health_surgical_history_updated_at ON public.health_surgical_history;
CREATE TRIGGER update_health_surgical_history_updated_at BEFORE UPDATE ON public.health_surgical_history
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();

DROP TRIGGER IF EXISTS set_dietary_settings_updated_at ON public.dietary_settings;
CREATE TRIGGER set_dietary_settings_updated_at BEFORE UPDATE ON public.dietary_settings
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();

-- ============================================================================
-- API TOKENS (Long-lived mobile/integration tokens)
-- ============================================================================

-- Separate from sessions for different lifecycle management
-- Web sessions: short-lived (24hr), affected by web logout
-- API tokens: long-lived (30+ days), survive web logout, require explicit revocation
CREATE TABLE IF NOT EXISTS public.api_tokens (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    user_id UUID NOT NULL,
    token_hash VARCHAR(255) NOT NULL,      -- SHA-256 hash, never store plaintext
    device_name TEXT,                       -- "My iPhone", "HealthKit Sync"
    token_type TEXT DEFAULT 'mobile',       -- 'mobile', 'healthkit', 'integration', 'mcp'
    key_prefix VARCHAR(12),                 -- First 12 chars of token for display/lookup (e.g. "hbk_a1b2c3d4")

    -- Lifecycle
    created_at TIMESTAMPTZ DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,                 -- NULL = never expires
    revoked_at TIMESTAMPTZ,                 -- Soft delete for audit trail

    -- Security tracking
    totp_verified_at TIMESTAMPTZ,           -- When 2FA was verified (if applicable)
    created_ip INET,
    last_ip INET,

    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON public.api_tokens(tenant_id, user_id) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_api_tokens_hash ON public.api_tokens(token_hash) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_api_tokens_prefix ON public.api_tokens(key_prefix) WHERE revoked_at IS NULL;

-- GRANT for api_tokens is applied by role/app-role-setup.sql (after role creation)

-- ============================================================================
-- MOBILE EVENTS (in-app event logging from React Native clients)
-- ============================================================================

-- Append-only event log. Server-authoritative (no sqlite_id, no mobile sync).
-- Used for analytics, debugging, and semantic search over user behavior.
CREATE TABLE IF NOT EXISTS public.mobile_events (
    tenant_id    SMALLINT NOT NULL DEFAULT 1,
    id           uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id      uuid NOT NULL,
    device_type  text,
    screen       text,
    event_text   text NOT NULL,
    duration_ms  integer,
    status       text,
    error_code   text,
    embedding_event_text vector(768),
    created_at   timestamp with time zone DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES public.users(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_mobile_events_tenant_user
    ON public.mobile_events USING btree (tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_mobile_events_tenant_user_created
    ON public.mobile_events USING btree (tenant_id, user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mobile_events_status
    ON public.mobile_events USING btree (tenant_id, status, created_at DESC)
    WHERE status IS NOT NULL;

-- ============================================================================
-- USERDOCS: DOCUMENT FOLDERS (file-system metaphor; tree via parent_id)
-- ============================================================================
-- Every document lives in a folder. Two system folders are auto-created for
-- every new user by the trigger below: `Documents` (default) and `Fax`
-- (destination for accepted inbound faxes). System folders cannot be trashed.

CREATE TABLE IF NOT EXISTS public.document_folders (
    tenant_id           SMALLINT NOT NULL DEFAULT 1,
    id                  UUID DEFAULT gen_random_uuid() NOT NULL,
    user_id             UUID NOT NULL,
    parent_id           UUID,
    name                TEXT NOT NULL,
    is_system           BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at          TIMESTAMPTZ DEFAULT now(),
    sqlite_id           BIGINT,
    synced_at           TIMESTAMPTZ,

    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, parent_id) REFERENCES public.document_folders(tenant_id, id) ON DELETE RESTRICT,

    CONSTRAINT document_folders_name_not_empty CHECK (length(trim(name)) > 0),
    CONSTRAINT document_folders_not_self_parent CHECK (parent_id IS NULL OR parent_id <> id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_document_folders_unique_name_live
    ON public.document_folders (tenant_id, user_id, COALESCE(parent_id, '00000000-0000-0000-0000-000000000000'::uuid), lower(name))
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_document_folders_tenant_user
    ON public.document_folders USING btree (tenant_id, user_id)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_document_folders_parent
    ON public.document_folders USING btree (tenant_id, parent_id)
    WHERE deleted_at IS NULL;

-- Trigger: auto-create Documents + Fax system folders for every new user.
-- Runs AFTER INSERT on users so FK (tenant_id, user_id) resolves.
-- Uses SECURITY DEFINER so the folder seed runs with definer privileges
-- (the user context is not yet established at signup time).
CREATE OR REPLACE FUNCTION public.seed_user_system_folders()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.document_folders (tenant_id, user_id, parent_id, name, is_system)
    VALUES
        (NEW.tenant_id, NEW.id, NULL, 'Documents', TRUE),
        (NEW.tenant_id, NEW.id, NULL, 'Fax',       TRUE);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS trg_users_seed_system_folders ON public.users;
CREATE TRIGGER trg_users_seed_system_folders
    AFTER INSERT ON public.users
    FOR EACH ROW
    EXECUTE FUNCTION public.seed_user_system_folders();

-- ============================================================================
-- USERDOCS: DOCUMENTS (user-owned files — uploads, fax images, emails)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.documents (
    tenant_id           SMALLINT NOT NULL DEFAULT 1,
    id                  UUID DEFAULT gen_random_uuid() NOT NULL,
    user_id             UUID NOT NULL,
    folder_id           UUID NOT NULL,
    filename            TEXT NOT NULL,
    mime_type           TEXT,
    file_size_bytes     INTEGER,
    file_path           TEXT NOT NULL,
    sha256              CHAR(64),
    source              TEXT NOT NULL DEFAULT 'upload',
    ocr_status          TEXT DEFAULT 'pending',
    quality_label       TEXT DEFAULT 'unknown',
    page_count          INTEGER,
    title               TEXT,
    category            TEXT,
    tags                JSONB DEFAULT '[]',
    embedding_content   vector(768),
    ocr_text_full       TEXT,
    storage_tier        TEXT NOT NULL DEFAULT 'local',
    remote_bucket       TEXT,
    remote_key          TEXT,
    stashed_at          TIMESTAMPTZ,
    local_expires_at    TIMESTAMPTZ,
    deleted_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at          TIMESTAMPTZ DEFAULT now(),

    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, folder_id) REFERENCES public.document_folders(tenant_id, id) ON DELETE RESTRICT,

    CONSTRAINT documents_source_check CHECK (source IN ('upload', 'fax_inbound', 'email', 'provider_send')),
    CONSTRAINT documents_ocr_status_check CHECK (ocr_status IN ('pending', 'processing', 'complete', 'failed', 'not_needed')),
    CONSTRAINT documents_quality_label_check CHECK (quality_label IN ('green', 'yellow', 'red', 'unknown')),
    CONSTRAINT documents_storage_tier_check CHECK (storage_tier IN ('local', 'remote', 'both')),
    CONSTRAINT documents_sha256_hex CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS idx_documents_folder
    ON public.documents USING btree (tenant_id, folder_id, created_at DESC)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_documents_tenant_user
    ON public.documents USING btree (tenant_id, user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_documents_tenant_user_active
    ON public.documents USING btree (tenant_id, user_id, created_at DESC)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_documents_cleanup_eligible
    ON public.documents (local_expires_at)
    WHERE storage_tier = 'both' AND local_expires_at IS NOT NULL;

-- ============================================================================
-- USERDOCS: DOCUMENT PAGES (per-page OCR results and rendered images)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.document_pages (
    tenant_id           SMALLINT NOT NULL DEFAULT 1,
    id                  UUID DEFAULT gen_random_uuid() NOT NULL,
    document_id         UUID NOT NULL,
    user_id             UUID NOT NULL,
    page_number         INTEGER NOT NULL,
    ocr_text            TEXT,
    ocr_confidence      REAL,
    quality_label       TEXT,
    image_path          TEXT,
    remote_key          TEXT,
    created_at          TIMESTAMPTZ DEFAULT now() NOT NULL,

    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, document_id) REFERENCES public.documents(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE,
    UNIQUE (tenant_id, document_id, page_number),

    CONSTRAINT document_pages_quality_check CHECK (quality_label IN ('green', 'yellow', 'red'))
);

CREATE INDEX IF NOT EXISTS idx_document_pages_tenant_doc
    ON public.document_pages USING btree (tenant_id, document_id, page_number);
CREATE INDEX IF NOT EXISTS idx_document_pages_tenant_user
    ON public.document_pages USING btree (tenant_id, user_id);

-- ============================================================================
-- USERDOCS: DOCUMENT ANNOTATIONS (Phase 3 activates, schema created now)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.document_annotations (
    tenant_id           SMALLINT NOT NULL DEFAULT 1,
    id                  UUID DEFAULT gen_random_uuid() NOT NULL,
    document_id         UUID NOT NULL,
    user_id             UUID NOT NULL,
    author_type         TEXT NOT NULL,
    author_id           UUID NOT NULL,
    page_number         INTEGER,
    body                TEXT NOT NULL,
    embedding_body      vector(768),
    created_at          TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at          TIMESTAMPTZ DEFAULT now(),

    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, document_id) REFERENCES public.documents(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE,

    CONSTRAINT annotations_author_type_check CHECK (author_type IN ('user', 'provider', 'delegate'))
);

CREATE INDEX IF NOT EXISTS idx_document_annotations_tenant_doc
    ON public.document_annotations USING btree (tenant_id, document_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_document_annotations_tenant_user
    ON public.document_annotations USING btree (tenant_id, user_id);

-- GRANTs for UserDocs tables are applied by role/app-role-setup.sql (after role creation)

-- ============================================================================
-- CONTACTS
-- ============================================================================

-- User's personal contact book of their own doctors and practices
-- (scoped by tenant_id + user_id)
CREATE TABLE IF NOT EXISTS public.user_provider_contacts (
    tenant_id           SMALLINT NOT NULL DEFAULT 1 REFERENCES public.tenants(id),
    id                  UUID DEFAULT gen_random_uuid() NOT NULL,
    user_id             UUID NOT NULL,

    -- Contact info (user-entered)
    display_name        TEXT NOT NULL,
    first_name          TEXT,
    last_name           TEXT,
    phone               TEXT,
    address_line1       TEXT,
    address_line2       TEXT,
    city                TEXT,
    state               TEXT,
    zip_code            TEXT,
    portal_url          TEXT,
    notes               TEXT,

    -- Classification
    practitioner_type   TEXT NOT NULL DEFAULT 'medical',
    relationship_type   TEXT DEFAULT 'primary_care',

    -- NPI verification
    verification_status TEXT NOT NULL DEFAULT 'pending',
    npi_number          VARCHAR(20),
    npi_data            JSONB,
    npi_candidates      JSONB,
    linked_provider_id  UUID,

    created_at          TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at          TIMESTAMPTZ DEFAULT now() NOT NULL,

    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES public.users(tenant_id, id) ON DELETE CASCADE,

    CONSTRAINT upc_practitioner_type_check CHECK (
        practitioner_type IN ('medical', 'dental', 'massage', 'acupuncture', 'chiropractic', 'naturopathic', 'mental_health', 'physical_therapy', 'other')
    ),
    CONSTRAINT upc_verification_status_check CHECK (
        verification_status IN ('pending', 'verified', 'review', 'unverified', 'user_confirmed')
    ),
    CONSTRAINT upc_relationship_type_check CHECK (
        relationship_type IN ('primary_care', 'specialist', 'therapist', 'caregiver', 'family', 'dentist', 'other')
    )
);

CREATE INDEX IF NOT EXISTS idx_upc_tenant_user
    ON public.user_provider_contacts USING btree (tenant_id, user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_upc_verification_status
    ON public.user_provider_contacts USING btree (tenant_id, verification_status)
    WHERE verification_status IN ('pending', 'review');
CREATE INDEX IF NOT EXISTS idx_upc_linked_provider
    ON public.user_provider_contacts USING btree (tenant_id, linked_provider_id)
    WHERE linked_provider_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_upc_npi
    ON public.user_provider_contacts USING btree (npi_number)
    WHERE npi_number IS NOT NULL;

-- pgvector IVFFlat indexes for embedding columns (Tier 1 + 2)
-- lists=100 is conservative for alpha; tune per EmbeddingDesign.md Section 5
CREATE INDEX IF NOT EXISTS idx_health_observations_embedding_content ON health_observations USING ivfflat (embedding_content vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_health_food_itemsv2_embedding_name ON health_food_itemsv2 USING ivfflat (embedding_name vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_health_inputs_embedding_name ON health_inputs USING ivfflat (embedding_name vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_health_allergies_embedding_allergy_full ON health_allergies USING ivfflat (embedding_allergy_full vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_health_conditions_embedding_condition ON health_conditions USING ivfflat (embedding_condition vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_mobile_events_embedding_event_text ON mobile_events USING ivfflat (embedding_event_text vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_documents_embedding_content ON documents USING ivfflat (embedding_content vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_document_annotations_embedding_body ON document_annotations USING ivfflat (embedding_body vector_cosine_ops) WITH (lists = 100);

-- ============================================================================
-- OBJECT COMMENTS
-- ============================================================================
-- Accumulated table and column documentation. Kept in one block for easy audit
-- against the live database (compare_full.py OBJECT COMMENTS section).

-- Feedback
COMMENT ON COLUMN public.feedback.environment IS 'Deployment environment: pilot, production';
COMMENT ON COLUMN public.feedback.metadata IS 'Extensible JSONB context: client_ip, hostname, session_id, user_agent, etc.';
COMMENT ON COLUMN public.feedback.screen_resolution IS 'Deprecated: never populated. Use metadata->>screen_resolution instead.';
COMMENT ON COLUMN public.feedback.source_app IS 'Producer application: UserApp, UserMCP, mobile';
COMMENT ON COLUMN public.feedback.user_agent IS 'Deprecated: never populated. Use metadata->>user_agent instead.';

-- Food items and inputs
COMMENT ON COLUMN public.health_food_itemsv2.fdc_id IS 'Optional link to authoritative USDA FDC record. Resolved by mobile app via photo ID or search.';
COMMENT ON COLUMN public.health_inputs.timeframe_id IS 'Optional timeframe for standalone inputs (not in a stack) to get projected reminders';
COMMENT ON COLUMN public.health_metrics.source_log_id IS 'Optional FK-by-convention to the source log row this metric was projected from. Used by the nutrition projector (food_log → health_metrics(metric_type=nutrition)) and may be reused by future projectors. Nullable: HealthKit-imported rows and manually-entered rows leave this NULL.';

-- Timeframes
COMMENT ON COLUMN public.timeframes.frequency IS 'Recurrence pattern: daily (default), weekly, monthly, annual, custom (use custom_days), once';
COMMENT ON COLUMN public.timeframes.custom_days IS 'Day-of-week array for custom frequency: 0=Sun, 1=Mon, ..., 6=Sat';
COMMENT ON COLUMN public.timeframes.start_date IS 'Anchor date for weekly/monthly/annual recurrence calculations';

-- User preferences
COMMENT ON TABLE public.user_preferences IS 'Per-user UI preferences (sidebar config, theme, font size). 1:1 with users.';
COMMENT ON COLUMN public.user_preferences.sidebar_hidden IS 'Activity keys the user has hidden, e.g. {analysis,contacts}. NULL = show all.';
COMMENT ON COLUMN public.user_preferences.sidebar_order IS 'Activity keys in user preferred order, e.g. {logging,config,analysis}. NULL = default order.';
COMMENT ON COLUMN public.user_preferences.timezone_reminder_mode IS 'home = reminders fire at home timezone time; local = reminders fire at device local time';

-- ============================================================================
-- Security column annotations (4thDegree.md Track 7 — schema crypto contract)
-- ============================================================================
-- Every column whose name matches the sensitive-name pattern
-- (`password|secret|token` substring, case-insensitive) MUST carry an
-- ``algo: <name>`` annotation declaring how the column's value is
-- protected. The ``code_query_audit.py`` audit reads these comments and
-- fails when a sensitive-pattern column has none, or when the value is
-- not in the recognised set:
--
--   argon2id          - Argon2id password hash (PHC-encoded)
--   bcrypt            - bcrypt hash
--   sha256            - SHA-256 hash
--   fernet            - Fernet symmetric encryption
--   aes-gcm           - AES-256-GCM symmetric encryption
--   plaintext         - intentionally plaintext (declare why in the
--                       comment text — TOTP secrets, single-use tokens
--                       returned via email, etc.)
--   tbd               - placeholder for sensitive material whose crypto
--                       helper is not yet built (the F2 finding's
--                       `garmin_credentials.encrypted_password` shape
--                       lives here today). New code MUST NOT use this
--                       value; it exists to grandfather one specific
--                       column.
--   not-a-credential  - column name matched the sensitive-name pattern
--                       by coincidence (api_tokens.token_type holds
--                       'mobile'/'healthkit' enums; tokens_used is an
--                       LLM token count). Declares the audit-pattern
--                       false-positive in-band.
--
-- Track 3 Rule 4 binds against this annotation: future enrichment of
-- the audit will verify that the writing function uses a helper
-- consistent with the declared algo (e.g. an `algo: argon2id` column
-- written through `bytes.b64encode` will fail).
COMMENT ON COLUMN public.users.password_hash IS 'algo: argon2id — Argon2id PHC string written by hash_password() in webapp/auth.py.';
COMMENT ON COLUMN public.users.totp_secret IS 'algo: plaintext — TOTP shared secret. Plaintext-by-design: TOTP requires the server to recompute the same code the user''s authenticator app generates from the secret, so a hash would defeat the protocol.';
COMMENT ON COLUMN public.password_reset_tokens.token IS 'algo: plaintext — UUID4 reset token. Plaintext by current design (single-use, 24h TTL, invalidated on first use); see Compliance/sensitive-write-sites.md Finding 2 for the future hardening direction (hash this, send plaintext only in email).';
COMMENT ON COLUMN public.email_verification_tokens.token IS 'algo: plaintext — UUID4 verification token. Same shape and trade-off as password_reset_tokens.token.';
COMMENT ON COLUMN public.garmin_credentials.encrypted_password IS 'algo: tbd — Garmin OAuth session blob. Currently base64-encoded plaintext (F2 finding from 2ndOpinion.md). Per Compliance/garmin-credential-decision.md the encryption helper is required work; this comment will move to "fernet" or "aes-gcm" in the same PR that adds the helper.';
COMMENT ON COLUMN public.garmin_credentials.oauth1_token IS 'algo: tbd — Garmin OAuth1 access token. Same encryption work as encrypted_password; tracked as a unit.';
COMMENT ON COLUMN public.garmin_credentials.oauth1_secret IS 'algo: tbd — Garmin OAuth1 access token secret. Same encryption work as encrypted_password; tracked as a unit.';
COMMENT ON COLUMN public.api_tokens.token_hash IS 'algo: sha256 — SHA-256 hex digest of the bearer token. Plaintext token returned to the client exactly once at creation, never stored.';
COMMENT ON COLUMN public.api_tokens.token_type IS 'algo: not-a-credential — enum: ''mobile'' | ''healthkit'' | ''integration'' | ''mcp''. Column name matches the sensitive-name pattern by coincidence.';

-- Home Edition marker
INSERT INTO public.schema_version (version, description) VALUES ('11.0.0-home', 'Home Edition single-household schema; tenant_id fixed at 1') ON CONFLICT (version) DO NOTHING;

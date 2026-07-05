# Home Edition Database Report — healthv10

**Date: 2026-07-03 03:45 PDT**

Generated from `Infrastructure/init/docker-init-home/02-home_schema.sql` (schema version `11.0.0-home`), 73 tables. That file is the schema source of truth; this report is a derived reference and is regenerated, never hand-drifted.

Every user-owned table carries `tenant_id` (always `1`) as a fixed app-level scoping convention; per-user privacy is enforced in the application with explicit `user_id` predicates on every query. Companion diagram file: `HomeDatabaseERD.md`.

## Domain overview

| Domain | Tables |
|--------|--------|
| Core & authentication | 8 |
| System & telemetry | 4 |
| Health tracking — medications & supplements | 8 |
| Scheduling & reminders | 4 |
| Dietary, food & household | 8 |
| Vitals & metrics | 5 |
| Clinical history | 6 |
| Contacts | 1 |
| Documents & embeddings | 4 |
| Garmin | 9 |
| HealthKit | 13 |
| Mobile sync | 3 |
| **Total** | **73** |

## Core & authentication

Identity and access for the household: member accounts, login sessions, tokens, devices, and per-user preferences. Everything else in the database hangs off `users(tenant_id, id)`.

### `tenants`

Anchor for the fixed `tenant_id` convention. Holds exactly one seeded row (`id = 1`, slug `minowa`); every other table's `tenant_id` column points at this row (three tables carry an actual FK; the rest reference it by convention through their composite FKs to `users`). The name/slug/domain/settings columns are deployment metadata.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `id` | SMALLINT | NOT NULL | `generated always as identity` |
| `name` | TEXT | NOT NULL |  |
| `slug` | TEXT | NOT NULL |  |
| `domain` | TEXT |  |  |
| `is_active` | BOOLEAN |  | `TRUE` |
| `settings` | JSONB |  | `'{}'` |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(id)`
- **Unique:** `(slug)`
- **Index:** `idx_tenants_slug` — `USING btree (slug)`
- **Index:** `idx_tenants_domain` — `USING btree (domain) WHERE domain IS NOT NULL`

### `users`

One row per household member. Carries authentication (Argon2id `password_hash`, optional TOTP 2FA fields), profile and demographics (display name, birth fields, address block), locale and `home_timezone`, inclusivity fields (`biological_sex`, `gender_identity`, `pronouns`, `track_energy_spoons`), contact preferences, and lifecycle flags. Inserting a user fires `trg_users_seed_system_folders` (see `document_folders`).

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `sqlite_id` | TEXT |  |  |
| `email` | TEXT | NOT NULL |  |
| `username` | TEXT |  |  |
| `display_name` | TEXT |  |  |
| `password_hash` | VARCHAR(255) | NOT NULL |  |
| `phone_number` | TEXT |  |  |
| `user_hash` | TEXT |  |  |
| `hash_salt` | TEXT |  |  |
| `device_at_creation` | TEXT |  |  |
| `created_at_ms` | BIGINT |  |  |
| `avatar_photo_url` | TEXT |  |  |
| `birth_year` | INT |  |  |
| `birth_month` | INT |  |  |
| `account_type` | TEXT |  | `'free'` |
| `deployment_type` | TEXT |  | `'saas'` |
| `home_timezone` | TEXT |  | `'America/Los_Angeles'` |
| `locale` | TEXT |  | `'en-US'` |
| `unit_system` | TEXT | NOT NULL | `'imperial'` |
| `preferred_language` | TEXT |  | `'en'` |
| `biological_sex` | TEXT |  |  |
| `gender_identity` | TEXT |  |  |
| `pronouns` | TEXT |  |  |
| `onboarding_complete` | INT |  | `0` |
| `last_active_at` | TIMESTAMPTZ |  |  |
| `track_energy_spoons` | INT |  | `0` |
| `is_active` | BOOLEAN |  | `TRUE` |
| `is_developer` | BOOLEAN |  | `FALSE` |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` |
| `last_login` | TIMESTAMPTZ |  |  |
| `notes` | TEXT |  |  |
| `totp_secret` | TEXT |  |  |
| `totp_enabled` | BOOLEAN |  | `FALSE` |
| `totp_backup_codes` | TEXT[] |  |  |
| `totp_enabled_at` | TIMESTAMPTZ |  |  |
| `date_of_birth` | DATE |  |  |
| `address_line1` | TEXT |  |  |
| `address_line2` | TEXT |  |  |
| `city` | TEXT |  |  |
| `state_province` | TEXT |  |  |
| `postal_code` | TEXT |  |  |
| `country` | TEXT |  | `'US'` |
| `promo_code` | VARCHAR(50) |  |  |
| `fax_number` | TEXT |  |  |
| `accepts_sms` | BOOLEAN |  | `FALSE` |
| `contact_hours` | JSONB |  |  |

- **Primary key:** `(tenant_id, id)`
- **Unique:** `(tenant_id, email)`
- **Unique:** `(tenant_id, user_hash)`
- **Check:** `users_account_type_check`: `(account_type IN ('free', 'premium', 'family'))`
- **Check:** `users_biological_sex_check`: `(biological_sex IN ('female', 'male', 'intersex', 'not_specified'))`
- **Check:** `users_deployment_type_check`: `(deployment_type IN ('saas', 'self_hosted'))`
- **FK:** `(tenant_id)` → `tenants(id)`
- **Index:** `idx_users_tenant_email` — `USING btree (tenant_id, email)`
- **Index:** `idx_users_tenant_active` — `USING btree (tenant_id, is_active)`
- **Index:** `idx_users_sqlite` — `USING btree (sqlite_id) WHERE (sqlite_id IS NOT NULL)`
- **Index:** `idx_users_promo_code` — `USING btree (promo_code) WHERE (promo_code IS NOT NULL)`
- **Index:** `idx_users_totp_enabled` — `USING btree (totp_enabled) WHERE (totp_enabled = true)`

### `sessions`

Web and API login sessions with expiry, client metadata (`ip_address`, `user_agent`), and the timestamp of 2FA verification for the session. The helper function `cleanup_expired_sessions()` deletes expired rows.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `session_id` | UUID | NOT NULL | `gen_random_uuid()` |
| `user_id` | UUID | NOT NULL |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `expires_at` | TIMESTAMPTZ | NOT NULL |  |
| `ip_address` | INET |  |  |
| `user_agent` | TEXT |  |  |
| `last_activity` | TIMESTAMPTZ |  | `now()` |
| `session_type` | TEXT |  | `'web'` |
| `totp_verified_at` | TIMESTAMPTZ |  |  |

- **Primary key:** `(tenant_id, session_id)`
- **Check:** `valid_expiry`: `(expires_at > created_at)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_sessions_tenant_user` — `USING btree (tenant_id, user_id)`
- **Index:** `idx_sessions_expiry` — `USING btree (expires_at)`

### `password_reset_tokens`

Single-use password-reset tokens. The token value is stored plain by design (single use, TTL, invalidated via `used_at`); the column carries an `algo: plaintext` annotation.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `user_id` | UUID | NOT NULL |  |
| `token` | TEXT | NOT NULL |  |
| `expires_at` | TIMESTAMPTZ | NOT NULL |  |
| `used_at` | TIMESTAMPTZ |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_password_reset_token` — `USING btree (token)`
- **Index:** `idx_password_reset_expires` — `USING btree (expires_at)`

### `email_verification_tokens`

Pre-signup email-verification tokens. Deliberately carries no FK to `users` because the account does not exist yet at verification time.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `email` | TEXT | NOT NULL |  |
| `display_name` | TEXT |  |  |
| `token` | TEXT | NOT NULL |  |
| `expires_at` | TIMESTAMPTZ | NOT NULL |  |
| `verified_at` | TIMESTAMPTZ |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(tenant_id, id)`
- **Index:** `idx_email_verify_token` — `USING btree (token)`
- **Index:** `idx_email_verify_email` — `USING btree (tenant_id, email)`

### `api_tokens`

Long-lived bearer tokens for mobile, HealthKit sync, integration, and MCP clients — separate from `sessions` because their lifecycle differs (30+ days, survive web logout, explicit revocation). Only the SHA-256 `token_hash` is stored; `key_prefix` supports display and lookup; revocation is a soft delete via `revoked_at`.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `user_id` | UUID | NOT NULL |  |
| `token_hash` | VARCHAR(255) | NOT NULL |  |
| `device_name` | TEXT |  |  |
| `token_type` | TEXT |  | `'mobile'` |
| `key_prefix` | VARCHAR(12) |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `last_used_at` | TIMESTAMPTZ |  |  |
| `expires_at` | TIMESTAMPTZ |  |  |
| `revoked_at` | TIMESTAMPTZ |  |  |
| `totp_verified_at` | TIMESTAMPTZ |  |  |
| `created_ip` | INET |  |  |
| `last_ip` | INET |  |  |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_api_tokens_user` — `(tenant_id, user_id) WHERE revoked_at IS NULL`
- **Index:** `idx_api_tokens_hash` — `(token_hash) WHERE revoked_at IS NULL`
- **Index:** `idx_api_tokens_prefix` — `(key_prefix) WHERE revoked_at IS NULL`

### `user_devices`

Registry of physical devices per user: identity (`device_id`, platform, versions), hardware, lifecycle timestamps, and device-reported embedding-capability fields (`can_embed`, `embed_model`, `embed_dimensions`). All embedding runs server-side via host Ollama (see `EmbeddingDesign.md`); these capability fields are reported metadata, not an execution path.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `user_id` | UUID | NOT NULL |  |
| `device_id` | TEXT | NOT NULL |  |
| `device_name` | TEXT |  |  |
| `platform` | TEXT |  |  |
| `os_version` | TEXT |  |  |
| `app_version` | TEXT |  |  |
| `device_model` | TEXT |  |  |
| `ram_mb` | INT |  |  |
| `can_embed` | BOOLEAN |  | `FALSE` |
| `embed_model` | TEXT |  |  |
| `embed_model_version` | TEXT |  |  |
| `embed_dimensions` | SMALLINT |  |  |
| `first_seen_at` | TIMESTAMPTZ |  | `now()` |
| `last_seen_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(tenant_id, id)`
- **Unique:** `(tenant_id, user_id, device_id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_user_devices_tenant_user` — `(tenant_id, user_id)`

### `user_preferences`

Per-user UI and behavior preferences: theme, units, notification toggles, reminder toggles, sidebar layout (`sidebar_order`, `sidebar_hidden`), and reminder timezone mode. 1:1 with `users`.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `user_id` | UUID | NOT NULL |  |
| `avatar_style` | TEXT |  | `'default'` |
| `theme` | TEXT |  | `'default'` |
| `color_scheme` | TEXT |  | `'system'` |
| `font_size` | TEXT |  | `'medium'` |
| `compact_mode` | BOOLEAN |  | `FALSE` |
| `show_animations` | BOOLEAN |  | `TRUE` |
| `units_weight` | TEXT |  | `'lbs'` |
| `units_height` | TEXT |  | `'ft_in'` |
| `units_temperature` | TEXT |  | `'fahrenheit'` |
| `units_blood_glucose` | TEXT |  | `'mg_dl'` |
| `notification_email` | BOOLEAN |  | `TRUE` |
| `notification_push` | BOOLEAN |  | `TRUE` |
| `notification_sms` | BOOLEAN |  | `FALSE` |
| `reminder_medications` | BOOLEAN |  | `TRUE` |
| `reminder_logging` | BOOLEAN |  | `TRUE` |
| `privacy_share_anonymous` | BOOLEAN |  | `FALSE` |
| `privacy_data_retention` | TEXT |  | `'forever'` |
| `sidebar_order` | TEXT[] |  | `NULL` |
| `sidebar_hidden` | TEXT[] |  | `NULL` |
| `timezone_reminder_mode` | TEXT |  | `'local'` |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(tenant_id, id)`
- **Unique:** `(tenant_id, user_id)`
- **Check:** `timezone_reminder_mode IN ('home', 'local')`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** none beyond the primary key

## System & telemetry

Bookkeeping tables: schema markers, the action audit trail, in-app feedback, and the mobile event stream.

### `schema_version`

Global schema version markers (no `tenant_id`). The Home Edition marker row is `11.0.0-home`.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `version` | VARCHAR(20) | NOT NULL |  |
| `applied_at` | TIMESTAMPTZ |  | `now()` |
| `description` | TEXT |  |  |

- **Primary key:** `(version)`
- **Index:** none beyond the primary key

### `audit_log`

Append-only record of notable actions: actor `user_id`, `action`, target reference (`target_type`/`target_id`), JSONB `details`, and source IP.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | BIGINT |  | `generated always as identity` |
| `user_id` | UUID |  |  |
| `action` | TEXT | NOT NULL |  |
| `target_type` | TEXT |  |  |
| `target_id` | TEXT |  |  |
| `details` | JSONB |  |  |
| `ip_address` | INET |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_audit_tenant_user` — `USING btree (tenant_id, user_id)`
- **Index:** `idx_audit_tenant_created` — `USING btree (tenant_id, created_at DESC)`

### `feedback`

In-app feedback (bug / feature / general / praise) with page context, producing app, environment, and extensible JSONB `metadata`. `user_id` is nullable and set to NULL if the submitting user is deleted, so feedback survives account removal.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `user_id` | UUID |  |  |
| `feedback_type` | TEXT | NOT NULL |  |
| `content` | TEXT | NOT NULL |  |
| `page_context` | TEXT |  |  |
| `user_agent` | TEXT |  |  |
| `screen_resolution` | TEXT |  |  |
| `app_version` | TEXT |  |  |
| `source_app` | TEXT | NOT NULL | `'UserApp'` |
| `environment` | TEXT | NOT NULL | `'pilot'` |
| `metadata` | JSONB | NOT NULL | `'{}'` |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `status` | TEXT |  | `'new'` |
| `admin_notes` | TEXT |  |  |

- **Primary key:** `(tenant_id, id)`
- **Check:** `feedback_type_check`: `(feedback_type IN ('bug', 'feature', 'general', 'praise'))`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE SET NULL
- **Index:** `idx_feedback_tenant_status` — `USING btree (tenant_id, status)`
- **Index:** `idx_feedback_source_app` — `USING btree (source_app)`

### `mobile_events`

Append-only event log from mobile clients: screen, event text, duration, status and error codes, plus an `embedding_event_text` vector for semantic search over behavior. Server-authoritative — no `sqlite_id`, no mobile sync.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `user_id` | UUID | NOT NULL |  |
| `device_type` | TEXT |  |  |
| `screen` | TEXT |  |  |
| `event_text` | TEXT | NOT NULL |  |
| `duration_ms` | INT |  |  |
| `status` | TEXT |  |  |
| `error_code` | TEXT |  |  |
| `embedding_event_text` | VECTOR(768) |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_mobile_events_tenant_user` — `USING btree (tenant_id, user_id)`
- **Index:** `idx_mobile_events_tenant_user_created` — `USING btree (tenant_id, user_id, created_at DESC)`
- **Index:** `idx_mobile_events_status` — `USING btree (tenant_id, status, created_at DESC) WHERE status IS NOT NULL`
- **Index:** `idx_mobile_events_embedding_event_text` — `USING ivfflat (embedding_event_text vector_cosine_ops) WITH (lists = 100)`

## Health tracking — medications & supplements

The catalog of what each member takes (`health_inputs`), bundles (`stacks`), condition-oriented groupings (`remedies`), the intake log, and the freeform-to-catalog matching queue (`log_promotions`). `remedies.condition_id` reaches into the clinical-history domain (`health_conditions`).

### `timeframes`

User-defined named times of day (“morning”, “bedtime”) with a recurrence pattern (`frequency`, `custom_days`, `start_date`). Stacks, standalone health inputs, and food-log rows attach to timeframes, and `projected_reminders` rows are derived from them.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `sqlite_id` | TEXT |  |  |
| `user_id` | UUID | NOT NULL |  |
| `name` | TEXT | NOT NULL |  |
| `time_of_day` | TIME |  |  |
| `sort_order` | INT |  | `0` |
| `is_active` | BOOLEAN |  | `TRUE` |
| `notes` | TEXT |  |  |
| `frequency` | TEXT |  | `'daily'` |
| `custom_days` | INT[] |  |  |
| `start_date` | DATE |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |
| `synced_at` | TIMESTAMPTZ |  |  |

- **Primary key:** `(tenant_id, id)`
- **Check:** `frequency IN ('daily', 'weekly', 'monthly', 'annual', 'custom', 'once')`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_timeframes_tenant_user` — `USING btree (tenant_id, user_id)`

### `health_inputs`

Catalog of things a member takes: medications, supplements, alternatives, and treatments. Holds dosage defaults, prescription details (prescribing doctor, pharmacy, rx number, refills), recurrence hints (`doses_per_day`, `frequent_status`), an optional `timeframe_id` so a standalone input can get a projected reminder, and an `embedding_name` vector used for freeform-log matching. Active rows have case-insensitive unique names per user (partial unique index), which lets an archived row coexist with a new active row of the same name.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `sqlite_id` | TEXT |  |  |
| `user_id` | UUID | NOT NULL |  |
| `name` | TEXT | NOT NULL |  |
| `input_type` | TEXT | NOT NULL |  |
| `default_dosage` | TEXT |  |  |
| `default_unit` | TEXT |  |  |
| `brand` | TEXT |  |  |
| `form` | TEXT |  |  |
| `frequency` | TEXT |  |  |
| `route` | TEXT |  |  |
| `instructions` | TEXT |  |  |
| `is_active` | BOOLEAN |  | `TRUE` |
| `take_with_food` | BOOLEAN |  |  |
| `refill_reminder_days` | INT |  |  |
| `current_quantity` | DECIMAL |  |  |
| `start_date` | DATE |  |  |
| `end_date` | DATE |  |  |
| `prescribing_doctor` | TEXT |  |  |
| `pharmacy` | TEXT |  |  |
| `rx_number` | TEXT |  |  |
| `refills_remaining` | INT |  |  |
| `notes` | TEXT |  |  |
| `custom_fields` | JSONB |  |  |
| `doses_per_day` | INT |  |  |
| `frequent_status` | TEXT |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |
| `synced_at` | TIMESTAMPTZ |  |  |
| `embedding_name` | VECTOR(768) |  |  |
| `timeframe_id` | UUID |  |  |

- **Primary key:** `(tenant_id, id)`
- **Check:** `health_inputs_input_type_check`: `(input_type IN ('medication', 'supplement', 'alternative', 'treatment'))`
- **Check:** `health_inputs_frequent_status_check`: `frequent_status IS NULL OR frequent_status IN ('detected', 'sticky')`
- **Check:** `health_inputs_doses_per_day_check`: `doses_per_day IS NULL OR doses_per_day IN (-1, 1, 2, 3, 4)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **FK:** `(tenant_id, timeframe_id)` → `timeframes(tenant_id, id)` ON DELETE SET NULL
- **Index:** `idx_health_inputs_tenant_user` — `USING btree (tenant_id, user_id)`
- **Index:** `idx_health_inputs_tenant_type` — `USING btree (tenant_id, input_type)`
- **Index:** `idx_health_inputs_sqlite` — `USING btree (sqlite_id) WHERE (sqlite_id IS NOT NULL)`
- **Index:** `idx_health_inputs_timeframe` — `USING btree (tenant_id, timeframe_id) WHERE (timeframe_id IS NOT NULL)`
- **Index:** UNIQUE `ux_health_inputs_active_name` — `(tenant_id, user_id, lower(name)) WHERE is_active = true`
- **Index:** `idx_health_inputs_embedding_name` — `USING ivfflat (embedding_name vector_cosine_ops) WITH (lists = 100)`

### `stacks`

Named bundles of health inputs taken together, optionally attached to a timeframe. Active rows have case-insensitive unique names per user, same pattern as `health_inputs`.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `sqlite_id` | TEXT |  |  |
| `user_id` | UUID | NOT NULL |  |
| `name` | TEXT | NOT NULL |  |
| `timeframe_id` | UUID |  |  |
| `description` | TEXT |  |  |
| `notes` | TEXT |  |  |
| `is_active` | BOOLEAN |  | `TRUE` |
| `sort_order` | INT |  | `0` |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |
| `synced_at` | TIMESTAMPTZ |  |  |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **FK:** `(tenant_id, timeframe_id)` → `timeframes(tenant_id, id)` ON DELETE SET NULL
- **Index:** `idx_stacks_tenant_user` — `USING btree (tenant_id, user_id)`
- **Index:** UNIQUE `ux_stacks_active_name` — `(tenant_id, user_id, lower(name)) WHERE is_active = true`

### `stack_inputs`

Junction between stacks and health inputs, with per-stack `dosage_override` and `sort_order`. Unique per (stack, input).

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `sqlite_id` | TEXT |  |  |
| `user_id` | UUID | NOT NULL |  |
| `stack_id` | UUID | NOT NULL |  |
| `health_input_id` | UUID | NOT NULL |  |
| `sort_order` | INT |  | `0` |
| `dosage_override` | TEXT |  |  |
| `notes` | TEXT |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `synced_at` | TIMESTAMPTZ |  |  |

- **Primary key:** `(tenant_id, id)`
- **Unique:** `(tenant_id, stack_id, health_input_id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **FK:** `(tenant_id, stack_id)` → `stacks(tenant_id, id)` ON DELETE CASCADE
- **FK:** `(tenant_id, health_input_id)` → `health_inputs(tenant_id, id)` ON DELETE CASCADE
- **Index:** none beyond the primary key

### `remedies`

Named groupings of inputs aimed at a specific condition, with an `effectiveness_rating`. `condition_id` points at `health_conditions` (SET NULL on condition delete).

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `sqlite_id` | TEXT |  |  |
| `user_id` | UUID | NOT NULL |  |
| `condition_id` | UUID |  |  |
| `name` | TEXT | NOT NULL |  |
| `description` | TEXT |  |  |
| `effectiveness_rating` | INT |  |  |
| `is_active` | BOOLEAN |  | `TRUE` |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |
| `synced_at` | TIMESTAMPTZ |  |  |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **FK:** `(tenant_id, condition_id)` → `health_conditions(tenant_id, id)` ON DELETE SET NULL
- **Index:** none beyond the primary key

### `remedy_inputs`

Junction between remedies and health inputs, with a remedy-specific dosage.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `sqlite_id` | TEXT |  |  |
| `user_id` | UUID | NOT NULL |  |
| `remedy_id` | UUID | NOT NULL |  |
| `input_id` | UUID | NOT NULL |  |
| `dosage_for_remedy` | TEXT |  |  |
| `notes` | TEXT |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `synced_at` | TIMESTAMPTZ |  |  |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **FK:** `(tenant_id, remedy_id)` → `remedies(tenant_id, id)` ON DELETE CASCADE
- **FK:** `(tenant_id, input_id)` → `health_inputs(tenant_id, id)` ON DELETE CASCADE
- **Index:** none beyond the primary key

### `health_input_log`

Intake log. Each row records either a catalog item (`input_id`) or a freeform entry (`free_text`/`free_dosage`) — `chk_input_or_text` enforces at least one. Skipped doses are recorded via `skipped`/`skip_reason`; `promoted_at` marks freeform rows later linked to the catalog through `log_promotions`.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `sqlite_id` | TEXT |  |  |
| `user_id` | UUID | NOT NULL |  |
| `logged_at` | TIMESTAMPTZ |  | `now()` |
| `input_id` | UUID |  |  |
| `dosage_taken` | TEXT |  |  |
| `notes` | TEXT |  |  |
| `stack_id` | UUID |  |  |
| `skipped` | BOOLEAN |  | `FALSE` |
| `skip_reason` | TEXT |  |  |
| `free_text` | TEXT |  |  |
| `free_dosage` | TEXT |  |  |
| `promoted_at` | TIMESTAMPTZ |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `synced_at` | TIMESTAMPTZ |  |  |

- **Primary key:** `(tenant_id, id)`
- **Check:** `chk_input_or_text`: `NOT input_id IS NULL OR NOT free_text IS NULL`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **FK:** `(tenant_id, input_id)` → `health_inputs(tenant_id, id)` ON DELETE CASCADE
- **FK:** `(tenant_id, stack_id)` → `stacks(tenant_id, id)` ON DELETE SET NULL
- **Index:** `idx_health_input_log_tenant_user_date` — `USING btree (tenant_id, user_id, logged_at DESC)`
- **Index:** `idx_input_log_freeform` — `(tenant_id, user_id, free_text) WHERE input_id IS NULL AND free_text IS NOT NULL`

### `log_promotions`

Match suggestions linking freeform log rows (from `health_input_log` or `health_food_logv2`) to catalog entries (`health_inputs` or `health_food_itemsv2`), with match confidence, method (exact / fuzzy / ai / user), and resolution status. Source and target references are by convention (`source_table` + `source_log_id`), not FKs, because they span two possible tables each.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `sqlite_id` | TEXT |  |  |
| `user_id` | UUID | NOT NULL |  |
| `source_table` | TEXT | NOT NULL |  |
| `source_log_id` | UUID | NOT NULL |  |
| `suggested_catalog_table` | TEXT |  |  |
| `suggested_catalog_id` | UUID |  |  |
| `free_text_original` | TEXT | NOT NULL |  |
| `match_confidence` | REAL |  |  |
| `match_method` | TEXT |  |  |
| `status` | TEXT |  | `'pending'` |
| `resolved_at` | TIMESTAMPTZ |  |  |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` |
| `synced_at` | TIMESTAMPTZ |  |  |
| `is_deleted` | INT |  | `0` |

- **Primary key:** `(tenant_id, id)`
- **Check:** `source_table IN ('health_input_log', 'health_food_logv2')`
- **Check:** `suggested_catalog_table IN ('health_inputs', 'health_food_itemsv2')`
- **Check:** `match_confidence BETWEEN 0.0 AND 1.0`
- **Check:** `match_method IN ('exact', 'fuzzy', 'ai', 'user')`
- **Check:** `status IN ('pending', 'accepted', 'dismissed', 'auto_linked')`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_promotions_pending` — `(tenant_id, user_id, status) WHERE status = 'pending'`
- **Index:** `idx_promotions_source` — `(tenant_id, source_table, source_log_id)`
- **Index:** `idx_promotions_sqlite` — `(sqlite_id) WHERE sqlite_id IS NOT NULL`

## Scheduling & reminders

Standalone reminders, reminders projected from timeframes, one-time appointments, and prepared visit packets. `projected_reminders` holds FKs into the medications domain (`stacks`, `health_inputs`, `timeframes`).

### `reminders`

Standalone scheduled reminders (medication, health-check, activity, hydration, appointment) with time-of-day, recurrence, snooze, completion state, and a per-reminder `privacy_level`. `health_input_id` is a bare UUID column with no FK constraint.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `user_id` | UUID | NOT NULL |  |
| `title` | TEXT | NOT NULL |  |
| `category` | TEXT | NOT NULL | `'medication'` |
| `time` | TEXT | NOT NULL |  |
| `frequency` | TEXT | NOT NULL | `'daily'` |
| `custom_days` | INT[] |  |  |
| `timezone` | TEXT |  |  |
| `snooze_minutes` | INT |  |  |
| `privacy_level` | TEXT |  | `'normal'` |
| `notes` | TEXT |  |  |
| `enabled` | BOOLEAN |  | `TRUE` |
| `completed` | BOOLEAN |  | `FALSE` |
| `completed_at` | TIMESTAMPTZ |  |  |
| `snoozed_until` | TIMESTAMPTZ |  |  |
| `last_triggered` | TIMESTAMPTZ |  |  |
| `health_input_id` | UUID |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |
| `sqlite_id` | TEXT |  |  |
| `synced_at` | TIMESTAMPTZ |  |  |

- **Primary key:** `(tenant_id, id)`
- **Check:** `chk_reminders_category`: `category IN ('medication', 'health-check', 'activity', 'hydration', 'appointment')`
- **Check:** `chk_reminders_frequency`: `frequency IN ('daily', 'weekly', 'monthly', 'custom', 'once')`
- **Check:** `chk_reminders_privacy_level`: `privacy_level IN ('normal', 'private', 'hidden')`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_reminders_tenant_user` — `(tenant_id, user_id)`
- **Index:** `idx_reminders_tenant_category` — `(tenant_id, category)`
- **Index:** `idx_reminders_sqlite_id` — `(tenant_id, sqlite_id) WHERE sqlite_id IS NOT NULL`

### `projected_reminders`

Reminder rows derived from a timeframe for either a stack or a standalone health input — `chk_projected_reminder_source` enforces exactly one source. Carries the copied schedule (`scheduled_time`, recurrence fields) plus `timezone_mode` (home vs local).

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `user_id` | UUID | NOT NULL |  |
| `sqlite_id` | TEXT |  |  |
| `stack_id` | UUID |  |  |
| `health_input_id` | UUID |  |  |
| `timeframe_id` | UUID | NOT NULL |  |
| `scheduled_time` | TIME | NOT NULL |  |
| `frequency` | TEXT | NOT NULL | `'daily'` |
| `custom_days` | INT[] |  |  |
| `start_date` | DATE |  |  |
| `timezone_mode` | TEXT |  | `'local'` |
| `enabled` | BOOLEAN |  | `TRUE` |
| `snoozed_until` | TIMESTAMPTZ |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |
| `synced_at` | TIMESTAMPTZ |  |  |

- **Primary key:** `(tenant_id, id)`
- **Check:** `frequency IN ('daily', 'weekly', 'monthly', 'annual', 'custom', 'once')`
- **Check:** `timezone_mode IN ('home', 'local')`
- **Check:** `chk_projected_reminder_source`: `(NOT stack_id IS NULL AND health_input_id IS NULL) OR (stack_id IS NULL AND NOT health_input_id IS NULL)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **FK:** `(tenant_id, stack_id)` → `stacks(tenant_id, id)` ON DELETE CASCADE
- **FK:** `(tenant_id, health_input_id)` → `health_inputs(tenant_id, id)` ON DELETE CASCADE
- **FK:** `(tenant_id, timeframe_id)` → `timeframes(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_projected_reminders_user` — `(tenant_id, user_id)`
- **Index:** `idx_projected_reminders_timeframe` — `(tenant_id, timeframe_id)`
- **Index:** `idx_projected_reminders_stack` — `(tenant_id, stack_id) WHERE stack_id IS NOT NULL`
- **Index:** `idx_projected_reminders_health_input` — `(tenant_id, health_input_id) WHERE health_input_id IS NOT NULL`
- **Index:** `idx_projected_reminders_sqlite_id` — `(tenant_id, sqlite_id) WHERE sqlite_id IS NOT NULL`

### `appointments`

One-time medical events with lead-time reminders (`reminder_lead_times`, default 24 h and 1 h before) and status tracking (scheduled / completed / cancelled / no_show). The optional practitioner-link column is a bare UUID with no FK constraint in this schema.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `user_id` | UUID | NOT NULL |  |
| `sqlite_id` | TEXT |  |  |
| `title` | TEXT | NOT NULL |  |
| `appointment_datetime` | TIMESTAMPTZ | NOT NULL |  |
| `duration_minutes` | INT |  |  |
| `location` | TEXT |  |  |
| `provider_id` | UUID |  |  |
| `notes` | TEXT |  |  |
| `reminder_lead_times` | INT[] |  | `'{1440, 60}'` |
| `reminder_enabled` | BOOLEAN |  | `TRUE` |
| `status` | TEXT |  | `'scheduled'` |
| `completed_at` | TIMESTAMPTZ |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |
| `synced_at` | TIMESTAMPTZ |  |  |

- **Primary key:** `(tenant_id, id)`
- **Check:** `status IN ('scheduled', 'completed', 'cancelled', 'no_show')`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_appointments_user_datetime` — `(tenant_id, user_id, appointment_datetime)`
- **Index:** `idx_appointments_status` — `(tenant_id, status) WHERE status = 'scheduled'`
- **Index:** `idx_appointments_sqlite_id` — `(tenant_id, sqlite_id) WHERE sqlite_id IS NOT NULL`

### `appointment_prep`

A prepared visit packet: a date-ranged JSONB snapshot of the member's health data plus freeform observations, keyed to an appointment date.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `user_id` | UUID | NOT NULL |  |
| `appointment_date` | DATE | NOT NULL |  |
| `date_range_start` | DATE | NOT NULL |  |
| `date_range_end` | DATE | NOT NULL |  |
| `observations` | TEXT |  |  |
| `health_data_snapshot` | JSONB |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)`
- **Index:** `idx_appointment_prep_user_date` — `(tenant_id, user_id, appointment_date DESC)`

## Dietary, food & household

Food catalog and log, meal templates, history-tracked diet preferences validated against the seeded diet catalog, and household groupings. `health_food_logv2.timeframe_id` reaches into the medications domain (`timeframes`).

### `health_food_itemsv2`

Per-user food catalog with full nutrition facts, barcode, favorite/custom flags, an `embedding_name` vector for freeform matching, and reserved linkage columns (`fdc_id` for an optional USDA FDC food id resolved client-side, and `diet_flags`) that this appliance keeps for sync compatibility but does not populate — there is no local USDA food cache.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `sqlite_id` | TEXT |  |  |
| `user_id` | UUID | NOT NULL |  |
| `name` | TEXT | NOT NULL |  |
| `brand` | TEXT |  |  |
| `barcode` | TEXT |  |  |
| `serving_size` | TEXT |  |  |
| `serving_unit` | TEXT |  |  |
| `calories` | DECIMAL |  |  |
| `protein_g` | DECIMAL |  |  |
| `carbs_g` | DECIMAL |  |  |
| `fat_g` | DECIMAL |  |  |
| `fiber_g` | DECIMAL |  |  |
| `sugar_g` | DECIMAL |  |  |
| `sodium_mg` | DECIMAL |  |  |
| `cholesterol_mg` | DECIMAL |  |  |
| `saturated_fat_g` | DECIMAL |  |  |
| `trans_fat_g` | DECIMAL |  |  |
| `potassium_mg` | DECIMAL |  |  |
| `vitamin_a_pct` | DECIMAL |  |  |
| `vitamin_c_pct` | DECIMAL |  |  |
| `calcium_pct` | DECIMAL |  |  |
| `iron_pct` | DECIMAL |  |  |
| `custom_nutrients` | JSONB |  |  |
| `is_favorite` | BOOLEAN |  | `FALSE` |
| `is_custom` | BOOLEAN |  | `TRUE` |
| `source` | TEXT |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |
| `synced_at` | TIMESTAMPTZ |  |  |
| `embedding_name` | VECTOR(768) |  |  |
| `fdc_id` | INT |  |  |
| `diet_flags` | JSONB |  |  |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_food_items_tenant_user` — `USING btree (tenant_id, user_id)`
- **Index:** `idx_food_items_barcode` — `USING btree (barcode) WHERE (barcode IS NOT NULL)`
- **Index:** `idx_food_items_fdc` — `USING btree (tenant_id, fdc_id) WHERE (fdc_id IS NOT NULL)`
- **Index:** `idx_health_food_itemsv2_embedding_name` — `USING ivfflat (embedding_name vector_cosine_ops) WITH (lists = 100)`

### `health_food_logv2`

Food intake log. Each row is either a catalog item (`food_item_id`) or freeform (`free_text`) — `chk_food_or_text` enforces at least one. Optional photo, `meal_type`, and `timeframe_id`; `promoted_at` marks freeform rows later linked via `log_promotions`.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `sqlite_id` | TEXT |  |  |
| `user_id` | UUID | NOT NULL |  |
| `logged_at` | TIMESTAMPTZ |  | `now()` |
| `food_item_id` | UUID |  |  |
| `servings` | DECIMAL |  | `1` |
| `meal_type` | TEXT |  |  |
| `timeframe_id` | UUID |  |  |
| `notes` | TEXT |  |  |
| `free_text` | TEXT |  |  |
| `photo_url` | TEXT |  |  |
| `promoted_at` | TIMESTAMPTZ |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `synced_at` | TIMESTAMPTZ |  |  |

- **Primary key:** `(tenant_id, id)`
- **Check:** `chk_food_or_text`: `NOT food_item_id IS NULL OR NOT free_text IS NULL`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **FK:** `(tenant_id, food_item_id)` → `health_food_itemsv2(tenant_id, id)` ON DELETE CASCADE
- **FK:** `(tenant_id, timeframe_id)` → `timeframes(tenant_id, id)` ON DELETE SET NULL
- **Index:** `idx_food_log_tenant_user_date` — `USING btree (tenant_id, user_id, logged_at DESC)`
- **Index:** `idx_food_log_freeform` — `(tenant_id, user_id, free_text) WHERE food_item_id IS NULL AND free_text IS NOT NULL`

### `meals`

Reusable meal templates (name, description, meal type, favorite flag).

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `sqlite_id` | TEXT |  |  |
| `user_id` | UUID | NOT NULL |  |
| `name` | TEXT | NOT NULL |  |
| `description` | TEXT |  |  |
| `meal_type` | TEXT |  |  |
| `is_favorite` | BOOLEAN |  | `FALSE` |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |
| `synced_at` | TIMESTAMPTZ |  |  |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_meals_tenant_user` — `USING btree (tenant_id, user_id)`

### `meal_items`

Junction between meals and food items, with servings.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `sqlite_id` | TEXT |  |  |
| `user_id` | UUID | NOT NULL |  |
| `meal_id` | UUID | NOT NULL |  |
| `food_item_id` | UUID | NOT NULL |  |
| `servings` | DECIMAL |  | `1` |
| `notes` | TEXT |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `synced_at` | TIMESTAMPTZ |  |  |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **FK:** `(tenant_id, meal_id)` → `meals(tenant_id, id)` ON DELETE CASCADE
- **FK:** `(tenant_id, food_item_id)` → `health_food_itemsv2(tenant_id, id)` ON DELETE CASCADE
- **Index:** none beyond the primary key

### `dietary_settings`

History-tracked diet preferences: `diet_codes` (values from `diet_catalog`; default `{plant_based}`), the deprecated single `diet_type`, finer-grained `dietary_restrictions`, and calorie/macro targets. Rows form a history via `effective_date`/`end_date` (NULL `end_date` = current); `deleted_at` is a soft-delete tombstone for mobile sync.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `user_id` | UUID | NOT NULL |  |
| `diet_type` | TEXT |  |  |
| `diet_codes` | TEXT[] |  | `ARRAY['plant_based']::text[]` |
| `dietary_restrictions` | TEXT[] |  |  |
| `calorie_target` | INT |  |  |
| `protein_target_g` | DECIMAL |  |  |
| `carb_target_g` | DECIMAL |  |  |
| `fat_target_g` | DECIMAL |  |  |
| `meal_count_per_day` | INT |  | `3` |
| `notes` | TEXT |  |  |
| `is_active` | BOOLEAN |  | `TRUE` |
| `effective_date` | DATE |  | `CURRENT_DATE` |
| `end_date` | DATE |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |
| `deleted_at` | TIMESTAMPTZ |  |  |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_dietary_settings_tenant_user` — `(tenant_id, user_id, is_active, effective_date DESC)`
- **Index:** `idx_dietary_settings_pull_checkpoint` — `(tenant_id, updated_at, id)`

### `diet_catalog`

Read-only reference catalog of 21 named diets seeded in the schema, in three active categories (exclusion, nutrient_pattern, medical; `lifestyle` is reserved in the CHECK). `parent_diet_code` is a self-referencing inheritance hint (e.g. `lacto_vegetarian` → `plant_based`). The `excludes` / `nutrient_targets` / `derivation_tier` columns are retained as reference metadata only — this appliance ships no per-food adherence scorer. `is_clinical` flags clinically prescribed diets. This is the one deliberate household-shared read: the catalog is reference data, not per-user data.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `code` | TEXT | NOT NULL |  |
| `display_name` | TEXT | NOT NULL |  |
| `category` | TEXT | NOT NULL |  |
| `description` | TEXT |  |  |
| `excludes` | JSONB |  |  |
| `nutrient_targets` | JSONB |  |  |
| `parent_diet_code` | TEXT |  |  |
| `evidence_level` | TEXT |  |  |
| `is_clinical` | BOOLEAN | NOT NULL | `FALSE` |
| `derivation_tier` | TEXT | NOT NULL | `'clean'` |
| `notes` | TEXT |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(tenant_id, code)`
- **Check:** `category IN ('exclusion', 'nutrient_pattern', 'medical', 'lifestyle')`
- **Check:** `evidence_level IN ('clinical', 'pattern', 'philosophical')`
- **Check:** `derivation_tier IN ('clean', 'approximate', 'deferred')`
- **FK:** `(tenant_id, parent_diet_code)` → `diet_catalog(tenant_id, code)`
- **Index:** `idx_diet_catalog_category` — `(tenant_id, category)`
- **Index:** `idx_diet_catalog_clinical` — `(tenant_id, is_clinical) WHERE is_clinical = true`
- **Index:** `idx_diet_catalog_pull_checkpoint` — `(tenant_id, updated_at, code)`

### `households`

Named household groupings (family, roommates, group_home, clinical_facility) created by a user. `created_by` is RESTRICT on delete, so a creator account cannot be deleted while its household exists.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `name` | TEXT | NOT NULL |  |
| `household_type` | TEXT | NOT NULL |  |
| `created_by` | UUID | NOT NULL |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(tenant_id, id)`
- **Check:** `household_type IN ('family', 'roommates', 'group_home', 'clinical_facility')`
- **FK:** `(tenant_id, created_by)` → `users(tenant_id, id)` ON DELETE RESTRICT
- **Index:** `idx_households_tenant_creator` — `(tenant_id, created_by)`

### `household_members`

Membership of users in households, with a role (admin, cook, planner, eater) and join/leave timestamps (`left_at` NULL = currently active). Natural composite PK on (tenant, household, user).

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `household_id` | UUID | NOT NULL |  |
| `user_id` | UUID | NOT NULL |  |
| `role` | TEXT | NOT NULL |  |
| `joined_at` | TIMESTAMPTZ |  | `now()` |
| `left_at` | TIMESTAMPTZ |  |  |

- **Primary key:** `(tenant_id, household_id, user_id)`
- **Check:** `role IN ('admin', 'cook', 'planner', 'eater')`
- **FK:** `(tenant_id, household_id)` → `households(tenant_id, id)` ON DELETE CASCADE
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_household_members_tenant_user` — `(tenant_id, user_id)`
- **Index:** `idx_household_members_active` — `(tenant_id, household_id) WHERE left_at IS NULL`

## Vitals & metrics

Measured and observed values: blood pressure, lab results, the generic `health_metrics` time series, freeform observations, and daily energy tracking.

### `health_blood_pressure_readings`

Blood-pressure readings with pulse, position/arm, device, and range CHECKs on systolic/diastolic. A unique index on (user, time, systolic, diastolic) dedupes sync imports.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `sqlite_id` | TEXT |  |  |
| `user_id` | UUID | NOT NULL |  |
| `measured_at` | TIMESTAMPTZ |  | `now()` |
| `systolic` | INT | NOT NULL |  |
| `diastolic` | INT | NOT NULL |  |
| `pulse` | INT |  |  |
| `position` | TEXT |  |  |
| `arm` | TEXT |  |  |
| `notes` | TEXT |  |  |
| `device` | TEXT |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `synced_at` | TIMESTAMPTZ |  |  |

- **Primary key:** `(tenant_id, id)`
- **Check:** `bp_systolic_range`: `(systolic > 0 AND systolic < 300)`
- **Check:** `bp_diastolic_range`: `(diastolic > 0 AND diastolic < 200)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_bp_tenant_user_date` — `USING btree (tenant_id, user_id, measured_at DESC)`
- **Index:** UNIQUE `idx_bp_sync_dedupe` — `USING btree (tenant_id, user_id, measured_at, systolic, diastolic)`

### `health_blood_work`

Lab results entered per test: name, value/unit, reference range, abnormal flag, LOINC code, and panel name (CBC, CMP, ...).

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `sqlite_id` | TEXT |  |  |
| `user_id` | UUID | NOT NULL |  |
| `test_date` | DATE | NOT NULL |  |
| `test_name` | TEXT | NOT NULL |  |
| `value` | DECIMAL |  |  |
| `unit` | TEXT |  |  |
| `reference_range` | TEXT |  |  |
| `is_abnormal` | BOOLEAN |  |  |
| `lab_name` | TEXT |  |  |
| `loinc_code` | TEXT |  |  |
| `panel_name` | TEXT |  |  |
| `notes` | TEXT |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `synced_at` | TIMESTAMPTZ |  |  |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_blood_work_tenant_user_date` — `USING btree (tenant_id, user_id, test_date DESC)`
- **Index:** `idx_blood_work_loinc` — `(tenant_id, user_id, loinc_code) WHERE loinc_code IS NOT NULL`

### `health_metrics`

Generic health time series — steps, heart rate, sleep, nutrition, weight, temperature, and the rest of a 37-value CHECK-constrained `metric_type` enum — sourced from manual entry, HealthKit, Garmin, or projection. `source_log_id` is an FK-by-convention back to the source log row (used by the nutrition projector from `health_food_logv2`); a partial unique index dedupes external sync imports while leaving projected rows to dedupe via `source_log_id`.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `sqlite_id` | TEXT |  |  |
| `user_id` | UUID | NOT NULL |  |
| `metric_type` | TEXT | NOT NULL |  |
| `recorded_at` | TIMESTAMPTZ |  | `now()` |
| `value` | DECIMAL |  |  |
| `unit` | TEXT |  |  |
| `source` | TEXT |  |  |
| `notes` | TEXT |  |  |
| `source_log_id` | UUID |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |
| `synced_at` | TIMESTAMPTZ |  |  |

- **Primary key:** `(tenant_id, id)`
- **Check:** `health_metrics_type_check`: `(metric_type IN ('steps', 'heart_rate', 'resting_heart_rate', 'sleep', 'nutrition', 'active_energy_burned', 'basal_energy_burned', 'distance_walking_running', 'workout', 'workout_route', 'floors_climbed', 'wheelchair_pushes', 'hydration', 'heart_rate_variability', 'respiratory_rate', 'body_temperature', 'basal_body_temperature', 'medication', 'weight', 'height', 'body_fat_percentage', 'lean_body_mass', 'blood_glucose', 'oxygen_saturation', 'vo2_max', 'allergy_record', 'condition_record', 'immunization_record', 'lab_result_record', 'medication_record', 'procedure_record', 'vital_sign_record', 'temperature', 'blood_oxygen', 'apple_exercise_time', 'apple_stand_hour', 'mindful_session'))`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_health_metrics_tenant_user_type` — `USING btree (tenant_id, user_id, metric_type, recorded_at DESC)`
- **Index:** UNIQUE `idx_health_metrics_sync_dedupe` — `USING btree (tenant_id, user_id, metric_type, recorded_at, value, unit, source) WHERE source_log_id IS NULL`
- **Index:** `idx_health_metrics_source_log` — `(tenant_id, source_log_id) WHERE source_log_id IS NOT NULL`

### `health_observations`

Freeform member notes with category, severity, `mental_health_flag`, tags, related-input UUID array, and an `embedding_content` vector — the primary semantic-search / retrieval source.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `sqlite_id` | TEXT |  |  |
| `user_id` | UUID | NOT NULL |  |
| `observed_at` | TIMESTAMPTZ |  | `now()` |
| `category` | TEXT |  |  |
| `content` | TEXT | NOT NULL |  |
| `severity` | INT |  |  |
| `mental_health_flag` | BOOLEAN |  | `FALSE` |
| `related_inputs` | UUID[] |  |  |
| `tags` | TEXT[] |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |
| `synced_at` | TIMESTAMPTZ |  |  |
| `embedding_content` | VECTOR(768) |  |  |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_health_observations_tenant_user_date` — `USING btree (tenant_id, user_id, observed_at DESC)`
- **Index:** `idx_health_observations_embedding_content` — `USING ivfflat (embedding_content vector_cosine_ops) WITH (lists = 100)`

### `daily_energy`

One row per user per day of energy (“spoons”) tracking: starting and current spoon counts plus notes. Pairs with `users.track_energy_spoons`.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `user_id` | UUID | NOT NULL |  |
| `date` | DATE | NOT NULL |  |
| `starting_spoons` | INT |  |  |
| `current_spoons` | INT |  |  |
| `notes` | TEXT |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(tenant_id, id)`
- **Unique:** `(tenant_id, user_id, date)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** none beyond the primary key

## Clinical history

Personal medical history: conditions, allergies, family/social/surgical history, and vaccinations. `health_conditions` is referenced by `remedies` in the medications domain.

### `health_conditions`

Diagnosed or tracked conditions with ICD-10 code, status (active / managed / resolved / monitoring), severity, treating doctor, and an `embedding_condition` vector.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `sqlite_id` | TEXT |  |  |
| `user_id` | UUID | NOT NULL |  |
| `name` | TEXT | NOT NULL |  |
| `icd10_code` | TEXT |  |  |
| `diagnosed_date` | DATE |  |  |
| `status` | TEXT |  | `'active'` |
| `severity` | TEXT |  |  |
| `treating_doctor` | TEXT |  |  |
| `notes` | TEXT |  |  |
| `custom_fields` | JSONB |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |
| `synced_at` | TIMESTAMPTZ |  |  |
| `embedding_condition` | VECTOR(768) |  |  |

- **Primary key:** `(tenant_id, id)`
- **Check:** `health_conditions_status_check`: `(status IN ('active', 'managed', 'resolved', 'monitoring'))`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_health_conditions_tenant_user` — `USING btree (tenant_id, user_id)`
- **Index:** `idx_health_conditions_embedding_condition` — `USING ivfflat (embedding_condition vector_cosine_ops) WITH (lists = 100)`

### `health_allergies`

Manually entered allergies from any platform (`hkit_allergies` is the HealthKit-import counterpart): allergen, allergy type, reaction, severity, onset, status, source, and an `embedding_allergy_full` vector over allergen + reaction + notes.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `sqlite_id` | TEXT |  |  |
| `user_id` | UUID | NOT NULL |  |
| `allergen` | TEXT | NOT NULL |  |
| `allergy_type` | TEXT |  |  |
| `reaction` | TEXT |  |  |
| `severity` | TEXT |  |  |
| `onset_date` | DATE |  |  |
| `status` | TEXT |  | `'active'` |
| `notes` | TEXT |  |  |
| `source` | TEXT |  | `'manual'` |
| `custom_fields` | JSONB |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |
| `synced_at` | TIMESTAMPTZ |  |  |
| `embedding_allergy_full` | VECTOR(768) |  |  |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_health_allergies_tenant_user` — `(tenant_id, user_id)`
- **Index:** `idx_health_allergies_embedding_allergy_full` — `USING ivfflat (embedding_allergy_full vector_cosine_ops) WITH (lists = 100)`

### `health_family_history`

Family medical history — one row per family member per condition, with relationship, relative's name/age, vital status, cause of death, condition, ICD-10 code, and age at onset.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `sqlite_id` | TEXT |  |  |
| `user_id` | UUID | NOT NULL |  |
| `relationship` | TEXT | NOT NULL |  |
| `relative_name` | TEXT |  |  |
| `relative_age` | INT |  |  |
| `vital_status` | TEXT |  |  |
| `cause_of_death` | TEXT |  |  |
| `condition_name` | TEXT |  |  |
| `icd10_code` | TEXT |  |  |
| `age_at_onset` | INT |  |  |
| `notes` | TEXT |  |  |
| `custom_fields` | JSONB |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |
| `synced_at` | TIMESTAMPTZ |  |  |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_health_family_history_tenant_user` — `(tenant_id, user_id)`

### `health_social_history`

Social history — one row per category (tobacco_use, alcohol_use, employment, exercise, ...) with status (current / former / never), detail, quantity, and date range.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `sqlite_id` | TEXT |  |  |
| `user_id` | UUID | NOT NULL |  |
| `category` | TEXT | NOT NULL |  |
| `status` | TEXT |  |  |
| `detail` | TEXT |  |  |
| `quantity` | TEXT |  |  |
| `start_date` | DATE |  |  |
| `end_date` | DATE |  |  |
| `notes` | TEXT |  |  |
| `custom_fields` | JSONB |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |
| `synced_at` | TIMESTAMPTZ |  |  |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_health_social_history_tenant_user` — `(tenant_id, user_id)`
- **Index:** `idx_health_social_history_category` — `(tenant_id, user_id, category)`

### `health_surgical_history`

Surgical history — one row per procedure, with surgeon, facility, outcome, complications, transfusions flag, and anesthesia type.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `sqlite_id` | TEXT |  |  |
| `user_id` | UUID | NOT NULL |  |
| `procedure_name` | TEXT | NOT NULL |  |
| `procedure_date` | DATE |  |  |
| `surgeon` | TEXT |  |  |
| `facility` | TEXT |  |  |
| `outcome` | TEXT |  |  |
| `complications` | TEXT |  |  |
| `transfusions` | BOOLEAN |  | `FALSE` |
| `anesthesia_type` | TEXT |  |  |
| `notes` | TEXT |  |  |
| `custom_fields` | JSONB |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |
| `synced_at` | TIMESTAMPTZ |  |  |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_health_surgical_history_tenant_user` — `(tenant_id, user_id)`

### `health_vaccinations`

Vaccination records: vaccine name, administered date, lot number, site, administering party, location, next dose due, and reaction notes.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `sqlite_id` | TEXT |  |  |
| `user_id` | UUID | NOT NULL |  |
| `vaccine_name` | TEXT | NOT NULL |  |
| `administered_date` | DATE |  |  |
| `lot_number` | TEXT |  |  |
| `site` | TEXT |  |  |
| `administered_by` | TEXT |  |  |
| `location` | TEXT |  |  |
| `next_dose_due` | DATE |  |  |
| `reaction_notes` | TEXT |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `synced_at` | TIMESTAMPTZ |  |  |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_vaccinations_tenant_user` — `USING btree (tenant_id, user_id)`

## Contacts

The household member's personal contact book of their own doctors and practitioners.

### `user_provider_contacts`

The household member's personal contact book of their own doctors, dentists, therapists, and other practitioners: user-entered name, phone, address, portal URL, and notes, classified by `practitioner_type` and `relationship_type`. The `npi_number` / `npi_data` / `npi_candidates` columns and `verification_status` hold optional NPI registry lookup results; `linked_provider_id` is a bare UUID with no FK constraint in this schema.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `user_id` | UUID | NOT NULL |  |
| `display_name` | TEXT | NOT NULL |  |
| `first_name` | TEXT |  |  |
| `last_name` | TEXT |  |  |
| `phone` | TEXT |  |  |
| `address_line1` | TEXT |  |  |
| `address_line2` | TEXT |  |  |
| `city` | TEXT |  |  |
| `state` | TEXT |  |  |
| `zip_code` | TEXT |  |  |
| `portal_url` | TEXT |  |  |
| `notes` | TEXT |  |  |
| `practitioner_type` | TEXT | NOT NULL | `'medical'` |
| `relationship_type` | TEXT |  | `'primary_care'` |
| `verification_status` | TEXT | NOT NULL | `'pending'` |
| `npi_number` | VARCHAR(20) |  |  |
| `npi_data` | JSONB |  |  |
| `npi_candidates` | JSONB |  |  |
| `linked_provider_id` | UUID |  |  |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` |

- **Primary key:** `(tenant_id, id)`
- **Check:** `upc_practitioner_type_check`: `practitioner_type IN ('medical', 'dental', 'massage', 'acupuncture', 'chiropractic', 'naturopathic', 'mental_health', 'physical_therapy', 'other')`
- **Check:** `upc_verification_status_check`: `verification_status IN ('pending', 'verified', 'review', 'unverified', 'user_confirmed')`
- **Check:** `upc_relationship_type_check`: `relationship_type IN ('primary_care', 'specialist', 'therapist', 'caregiver', 'family', 'dentist', 'other')`
- **FK:** `(tenant_id)` → `tenants(id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_upc_tenant_user` — `USING btree (tenant_id, user_id, created_at DESC)`
- **Index:** `idx_upc_verification_status` — `USING btree (tenant_id, verification_status) WHERE verification_status IN ('pending', 'review')`
- **Index:** `idx_upc_linked_provider` — `USING btree (tenant_id, linked_provider_id) WHERE linked_provider_id IS NOT NULL`
- **Index:** `idx_upc_npi` — `USING btree (npi_number) WHERE npi_number IS NOT NULL`

## Documents & embeddings

The document store: a folder tree, user-owned files with in-process OCR results and pgvector embeddings, per-page OCR output, and annotations.

### `document_folders`

Folder tree for documents (`parent_id` self-reference, RESTRICT so a non-empty folder cannot be dropped). Two system folders — `Documents` and `Fax` — are auto-created for every new user by `trg_users_seed_system_folders` and cannot be trashed. Live folder names are unique per parent, case-insensitively (soft-deleted folders excluded via the partial index).

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `user_id` | UUID | NOT NULL |  |
| `parent_id` | UUID |  |  |
| `name` | TEXT | NOT NULL |  |
| `is_system` | BOOLEAN | NOT NULL | `FALSE` |
| `deleted_at` | TIMESTAMPTZ |  |  |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |
| `sqlite_id` | BIGINT |  |  |
| `synced_at` | TIMESTAMPTZ |  |  |

- **Primary key:** `(tenant_id, id)`
- **Check:** `document_folders_name_not_empty`: `LENGTH(TRIM(name)) > 0`
- **Check:** `document_folders_not_self_parent`: `parent_id IS NULL OR parent_id <> id`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **FK:** `(tenant_id, parent_id)` → `document_folders(tenant_id, id)` ON DELETE RESTRICT
- **Index:** UNIQUE `idx_document_folders_unique_name_live` — `(tenant_id, user_id, COALESCE(parent_id, '00000000-0000-0000-0000-000000000000'::uuid), lower(name)) WHERE deleted_at IS NULL`
- **Index:** `idx_document_folders_tenant_user` — `USING btree (tenant_id, user_id) WHERE deleted_at IS NULL`
- **Index:** `idx_document_folders_parent` — `USING btree (tenant_id, parent_id) WHERE deleted_at IS NULL`

### `documents`

User-owned files with in-process OCR pipeline state (`ocr_status`, `quality_label`, `page_count`, `ocr_text_full`), an `embedding_content` vector over the OCR text, a SHA-256 content hash, title/category/tags, and storage-tier bookkeeping (`storage_tier`, remote bucket/key, `local_expires_at`). `deleted_at` is a soft-delete tombstone. The `source` CHECK enumerates ingestion seams beyond plain upload.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `user_id` | UUID | NOT NULL |  |
| `folder_id` | UUID | NOT NULL |  |
| `filename` | TEXT | NOT NULL |  |
| `mime_type` | TEXT |  |  |
| `file_size_bytes` | INT |  |  |
| `file_path` | TEXT | NOT NULL |  |
| `sha256` | CHAR(64) |  |  |
| `source` | TEXT | NOT NULL | `'upload'` |
| `ocr_status` | TEXT |  | `'pending'` |
| `quality_label` | TEXT |  | `'unknown'` |
| `page_count` | INT |  |  |
| `title` | TEXT |  |  |
| `category` | TEXT |  |  |
| `tags` | JSONB |  | `'[]'` |
| `embedding_content` | VECTOR(768) |  |  |
| `ocr_text_full` | TEXT |  |  |
| `storage_tier` | TEXT | NOT NULL | `'local'` |
| `remote_bucket` | TEXT |  |  |
| `remote_key` | TEXT |  |  |
| `stashed_at` | TIMESTAMPTZ |  |  |
| `local_expires_at` | TIMESTAMPTZ |  |  |
| `deleted_at` | TIMESTAMPTZ |  |  |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(tenant_id, id)`
- **Check:** `documents_source_check`: `source IN ('upload', 'fax_inbound', 'email', 'provider_send')`
- **Check:** `documents_ocr_status_check`: `ocr_status IN ('pending', 'processing', 'complete', 'failed', 'not_needed')`
- **Check:** `documents_quality_label_check`: `quality_label IN ('green', 'yellow', 'red', 'unknown')`
- **Check:** `documents_storage_tier_check`: `storage_tier IN ('local', 'remote', 'both')`
- **Check:** `documents_sha256_hex`: `sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **FK:** `(tenant_id, folder_id)` → `document_folders(tenant_id, id)` ON DELETE RESTRICT
- **Index:** `idx_documents_folder` — `USING btree (tenant_id, folder_id, created_at DESC) WHERE deleted_at IS NULL`
- **Index:** `idx_documents_tenant_user` — `USING btree (tenant_id, user_id, created_at DESC)`
- **Index:** `idx_documents_tenant_user_active` — `USING btree (tenant_id, user_id, created_at DESC) WHERE deleted_at IS NULL`
- **Index:** `idx_documents_cleanup_eligible` — `(local_expires_at) WHERE storage_tier = 'both' AND local_expires_at IS NOT NULL`
- **Index:** `idx_documents_embedding_content` — `USING ivfflat (embedding_content vector_cosine_ops) WITH (lists = 100)`

### `document_pages`

Per-page OCR output for a document: text, confidence, quality label, rendered image path, and remote key. Unique per (document, page_number).

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `document_id` | UUID | NOT NULL |  |
| `user_id` | UUID | NOT NULL |  |
| `page_number` | INT | NOT NULL |  |
| `ocr_text` | TEXT |  |  |
| `ocr_confidence` | REAL |  |  |
| `quality_label` | TEXT |  |  |
| `image_path` | TEXT |  |  |
| `remote_key` | TEXT |  |  |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` |

- **Primary key:** `(tenant_id, id)`
- **Unique:** `(tenant_id, document_id, page_number)`
- **Check:** `document_pages_quality_check`: `quality_label IN ('green', 'yellow', 'red')`
- **FK:** `(tenant_id, document_id)` → `documents(tenant_id, id)` ON DELETE CASCADE
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_document_pages_tenant_doc` — `USING btree (tenant_id, document_id, page_number)`
- **Index:** `idx_document_pages_tenant_user` — `USING btree (tenant_id, user_id)`

### `document_annotations`

Freeform annotations on a document, optionally anchored to a page, with an `embedding_body` vector. `author_type`/`author_id` record who wrote the note; the CHECK enumerates the author kinds the sync-compatible data model allows.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `document_id` | UUID | NOT NULL |  |
| `user_id` | UUID | NOT NULL |  |
| `author_type` | TEXT | NOT NULL |  |
| `author_id` | UUID | NOT NULL |  |
| `page_number` | INT |  |  |
| `body` | TEXT | NOT NULL |  |
| `embedding_body` | VECTOR(768) |  |  |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(tenant_id, id)`
- **Check:** `annotations_author_type_check`: `author_type IN ('user', 'provider', 'delegate')`
- **FK:** `(tenant_id, document_id)` → `documents(tenant_id, id)` ON DELETE CASCADE
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_document_annotations_tenant_doc` — `USING btree (tenant_id, document_id, created_at DESC)`
- **Index:** `idx_document_annotations_tenant_user` — `USING btree (tenant_id, user_id)`
- **Index:** `idx_document_annotations_embedding_body` — `USING ivfflat (embedding_body vector_cosine_ops) WITH (lists = 100)`

## Garmin

Garmin wearable integration: high-volume time-series tables (`garm_*`), account linkage, and background sync jobs. The time-series tables use natural composite PKs on `(tenant_id, user_id, timestamp-or-date)` rather than surrogate UUIDs.

### `garm_daily_summ`

Garmin daily summary — one row per user per calendar date: steps and goals, distance, active/sedentary/sleep time, floors, intensity minutes, stress, heart-rate aggregates, calories, hydration, sweat loss, body battery, SpO2, and respiration. Several columns are compatibility aliases retained for older exports.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `user_id` | UUID | NOT NULL |  |
| `calendar_date` | DATE | NOT NULL |  |
| `total_steps` | INT |  |  |
| `daily_step_goal` | INT |  |  |
| `total_distance_meters` | INT |  |  |
| `active_time_secs` | INT |  |  |
| `sedentary_time_secs` | INT |  |  |
| `sleeping_time_secs` | INT |  |  |
| `floors_climbed` | INT |  |  |
| `floors_descended` | INT |  |  |
| `intensity_minutes_goal` | INT |  |  |
| `intensity_time_goal` | INT |  |  |
| `moderate_intensity_minutes` | INT |  |  |
| `moderate_activity_time` | INT |  |  |
| `vigorous_intensity_minutes` | INT |  |  |
| `vigorous_activity_time` | INT |  |  |
| `avg_stress_level` | DECIMAL |  |  |
| `max_stress_level` | INT |  |  |
| `min_heart_rate` | INT |  |  |
| `max_heart_rate` | INT |  |  |
| `resting_heart_rate` | INT |  |  |
| `avg_heart_rate` | INT |  |  |
| `bmr_kcals` | INT |  |  |
| `active_kcals` | INT |  |  |
| `total_kcals` | INT |  |  |
| `calories_goal` | INT |  |  |
| `calories_consumed` | INT |  |  |
| `hydration_goal` | INT |  |  |
| `hydration_intake` | INT |  |  |
| `sweat_loss` | INT |  |  |
| `body_battery_charged` | INT |  |  |
| `body_battery_drained` | INT |  |  |
| `body_battery_high` | INT |  |  |
| `body_battery_low` | INT |  |  |
| `spo2_avg` | DECIMAL |  |  |
| `spo2_low` | DECIMAL |  |  |
| `respiration_avg` | DECIMAL |  |  |
| `respiration_high` | DECIMAL |  |  |
| `respiration_low` | DECIMAL |  |  |
| `description` | TEXT |  |  |

- **Primary key:** `(tenant_id, user_id, calendar_date)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** none beyond the primary key

### `garm_hr`

Garmin heart-rate time series — one row per timestamp; natural PK (tenant, user, timestamp).

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `user_id` | UUID | NOT NULL |  |
| `timestamp` | TIMESTAMPTZ | NOT NULL |  |
| `heart_rate` | INT |  |  |

- **Primary key:** `(tenant_id, user_id, timestamp)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_garm_hr_tenant_user` — `(tenant_id, user_id, "timestamp" DESC)`

### `garm_rr`

Garmin respiratory-rate time series — one row per timestamp; natural PK (tenant, user, timestamp).

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `user_id` | UUID | NOT NULL |  |
| `timestamp` | TIMESTAMPTZ | NOT NULL |  |
| `respiratory_rate` | DECIMAL |  |  |

- **Primary key:** `(tenant_id, user_id, timestamp)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_garm_rr_tenant_user` — `(tenant_id, user_id, "timestamp" DESC)`

### `garm_sleep`

Garmin nightly sleep summary per calendar date: sleep window, stage durations (deep / light / REM / awake), respiration, SpO2, stress, and sleep score.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `user_id` | UUID | NOT NULL |  |
| `calendar_date` | DATE | NOT NULL |  |
| `sleep_start` | TIMESTAMPTZ |  |  |
| `sleep_end` | TIMESTAMPTZ |  |  |
| `deep_sleep_secs` | INT |  |  |
| `light_sleep_secs` | INT |  |  |
| `rem_sleep_secs` | INT |  |  |
| `awake_secs` | INT |  |  |
| `avg_respiration` | DECIMAL |  |  |
| `avg_spo2` | DECIMAL |  |  |
| `avg_stress` | DECIMAL |  |  |
| `sleep_score` | INT |  |  |
| `sleep_score_quality` | TEXT |  |  |

- **Primary key:** `(tenant_id, user_id, calendar_date)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** none beyond the primary key

### `garm_sleep_events`

Garmin sleep event intervals (type plus start/end); natural PK (tenant, user, start_time).

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `user_id` | UUID | NOT NULL |  |
| `start_time` | TIMESTAMPTZ | NOT NULL |  |
| `end_time` | TIMESTAMPTZ | NOT NULL |  |
| `sleep_type` | TEXT | NOT NULL |  |

- **Primary key:** `(tenant_id, user_id, start_time)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** none beyond the primary key

### `garm_stress`

Garmin stress-level time series — one row per timestamp; natural PK (tenant, user, timestamp).

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `user_id` | UUID | NOT NULL |  |
| `timestamp` | TIMESTAMPTZ | NOT NULL |  |
| `garm_stress` | INT |  |  |

- **Primary key:** `(tenant_id, user_id, timestamp)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_garm_stress_tenant_user` — `(tenant_id, user_id, "timestamp" DESC)`

### `garm_upload_date`

Bookkeeping for Garmin data uploads: timestamp, covered date, and status.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `user_id` | UUID | NOT NULL |  |
| `upload_timestamp` | TIMESTAMPTZ |  | `now()` |
| `upload_date` | DATE |  |  |
| `status` | TEXT |  | `'success'` |
| `notes` | TEXT |  |  |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** none beyond the primary key

### `garmin_credentials`

Garmin account linkage, 1:1 per user: OAuth1 token material, login email, and last sync. The credential columns carry `algo:` annotations flagging pending encryption work (see the security-annotation note below).

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `user_id` | UUID | NOT NULL |  |
| `oauth1_token` | TEXT |  |  |
| `oauth1_secret` | TEXT |  |  |
| `encrypted_password` | TEXT |  |  |
| `email` | TEXT |  |  |
| `last_sync` | TIMESTAMPTZ |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(tenant_id, id)`
- **Unique:** `(tenant_id, user_id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** none beyond the primary key

### `garmin_sync_jobs`

Background Garmin sync jobs: job type, date range, JSONB progress, error message, and a CHECK-constrained status (pending / running / completed / failed / cancelled).

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `user_id` | UUID | NOT NULL |  |
| `job_type` | TEXT | NOT NULL |  |
| `status` | TEXT |  | `'pending'` |
| `start_date` | DATE |  |  |
| `end_date` | DATE |  |  |
| `progress` | JSONB |  |  |
| `error_message` | TEXT |  |  |
| `started_at` | TIMESTAMPTZ |  |  |
| `completed_at` | TIMESTAMPTZ |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(tenant_id, id)`
- **Check:** `garmin_sync_status_check`: `(status IN ('pending', 'running', 'completed', 'failed', 'cancelled'))`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_garmin_sync_tenant_user_status` — `USING btree (tenant_id, user_id, status)`

## HealthKit

Apple HealthKit integration: sample records, activity summaries, workouts, profile, FHIR clinical records with extracted sub-records, incremental-sync anchors, and bulk import jobs. See `AppleHealthKitERD.md` for the HealthKit-specific deep dive.

### `hkit_record_types`

Global lookup of HealthKit record type identifiers (display name, category, unit). No `tenant_id` — pure reference data.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `id` | INT | NOT NULL | `generated always as identity` |
| `type_identifier` | TEXT | NOT NULL |  |
| `display_name` | TEXT |  |  |
| `category` | TEXT |  |  |
| `unit` | TEXT |  |  |

- **Primary key:** `(id)`
- **Unique:** `(type_identifier)`
- **Index:** none beyond the primary key

### `hkit_sources`

Per-user HealthKit data sources: source app/bundle, version, and device identity. Unique per (user, bundle id).

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | INT |  | `generated always as identity` |
| `user_id` | UUID | NOT NULL |  |
| `source_name` | TEXT | NOT NULL |  |
| `source_bundle_id` | TEXT |  |  |
| `source_version` | TEXT |  |  |
| `device_name` | TEXT |  |  |
| `device_model` | TEXT |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(tenant_id, id)`
- **Unique:** `(tenant_id, user_id, source_bundle_id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** none beyond the primary key

### `hkit_records`

Quantity and category samples imported from HealthKit, typed via `record_type_id` and carrying value/unit, sample interval, and JSONB metadata. A unique index dedupes re-imports (same type, source, and interval). `source_id` is a bare integer reference to `hkit_sources` (no FK constraint).

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | BIGINT |  | `generated always as identity` |
| `user_id` | UUID | NOT NULL |  |
| `record_type_id` | INT |  |  |
| `source_id` | INT |  |  |
| `value` | DECIMAL |  |  |
| `unit` | TEXT |  |  |
| `start_date` | TIMESTAMPTZ |  |  |
| `end_date` | TIMESTAMPTZ |  |  |
| `metadata` | JSONB |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(record_type_id)` → `hkit_record_types(id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_hkit_records_tenant_user_type` — `USING btree (tenant_id, user_id, record_type_id, start_date DESC)`
- **Index:** UNIQUE `idx_hkit_records_dedup` — `(tenant_id, user_id, record_type_id, source_id, start_date, end_date)`

### `hkit_activity_summaries`

Daily Apple activity-ring summaries: active energy, exercise time, stand hours, and the wheelchair Move Time ring (`move_time` populated instead of `exercise_time` for wheelchair users). Unique per (user, date).

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | BIGINT |  | `generated always as identity` |
| `user_id` | UUID | NOT NULL |  |
| `date` | DATE | NOT NULL |  |
| `active_energy_burned` | DECIMAL |  |  |
| `active_energy_burned_goal` | DECIMAL |  |  |
| `exercise_time` | INT |  |  |
| `exercise_time_goal` | INT |  |  |
| `stand_hours` | INT |  |  |
| `stand_hours_goal` | INT |  |  |
| `move_time` | INT |  |  |
| `move_time_goal` | INT |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(tenant_id, id)`
- **Unique:** `(tenant_id, user_id, date)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** none beyond the primary key

### `hkit_workouts`

HealthKit workout sessions: type, sample interval, duration, distance, energy burned, and JSONB metadata.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | BIGINT |  | `generated always as identity` |
| `user_id` | UUID | NOT NULL |  |
| `workout_type` | TEXT | NOT NULL |  |
| `source_id` | INT |  |  |
| `start_date` | TIMESTAMPTZ |  |  |
| `end_date` | TIMESTAMPTZ |  |  |
| `duration_seconds` | DECIMAL |  |  |
| `total_distance` | DECIMAL |  |  |
| `total_energy_burned` | DECIMAL |  |  |
| `metadata` | JSONB |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_hkit_workouts_tenant_user_date` — `USING btree (tenant_id, user_id, start_date DESC)`

### `hkit_user_profile`

1:1 HealthKit profile snapshot per user: date of birth, biological sex, blood type, Fitzpatrick skin type, and wheelchair use.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | INT |  | `generated always as identity` |
| `user_id` | UUID | NOT NULL |  |
| `date_of_birth` | DATE |  |  |
| `biological_sex` | TEXT |  |  |
| `blood_type` | TEXT |  |  |
| `fitzpatrick_skin_type` | TEXT |  |  |
| `wheelchair_use` | BOOLEAN |  |  |
| `updated_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(tenant_id, id)`
- **Unique:** `(tenant_id, user_id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** none beyond the primary key

### `hkit_clinical_records`

Raw FHIR clinical records received from HealthKit (resource type, identifier, source URL, full `raw_fhir` JSONB). A partial unique index on `fhir_identifier` dedupes re-imports of the same export.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | INT |  | `generated always as identity` |
| `user_id` | UUID | NOT NULL |  |
| `fhir_resource_type` | TEXT | NOT NULL |  |
| `fhir_identifier` | TEXT |  |  |
| `fhir_source_url` | TEXT |  |  |
| `display_name` | TEXT |  |  |
| `received_date` | TIMESTAMPTZ |  |  |
| `raw_fhir` | JSONB |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_hkit_clinical_tenant_user` — `USING btree (tenant_id, user_id)`
- **Index:** UNIQUE `ux_hkit_clinical_records_fhir_id` — `(tenant_id, user_id, fhir_identifier) WHERE fhir_identifier IS NOT NULL`

### `hkit_lab_observations`

Lab observations extracted from clinical records: LOINC code, value (quantity or string), unit, reference range, interpretation, and effective date. `clinical_record_id` is a by-convention pointer to `hkit_clinical_records` (no FK); a partial unique index allows at most one extracted observation per parent record.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | INT |  | `generated always as identity` |
| `user_id` | UUID | NOT NULL |  |
| `clinical_record_id` | INT |  |  |
| `loinc_code` | TEXT |  |  |
| `display_name` | TEXT |  |  |
| `value_quantity` | DECIMAL |  |  |
| `value_unit` | TEXT |  |  |
| `value_string` | TEXT |  |  |
| `reference_range` | TEXT |  |  |
| `interpretation` | TEXT |  |  |
| `effective_date` | TIMESTAMPTZ |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_hkit_labs_tenant_user` — `USING btree (tenant_id, user_id)`
- **Index:** UNIQUE `ux_hkit_lab_observations_parent` — `(tenant_id, user_id, clinical_record_id) WHERE clinical_record_id IS NOT NULL`

### `hkit_allergies`

Allergy entries extracted from HealthKit clinical records (allergen, reaction, severity, onset). Same by-convention `clinical_record_id` parent pointer and one-per-parent unique index as the other extraction tables.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | INT |  | `generated always as identity` |
| `user_id` | UUID | NOT NULL |  |
| `clinical_record_id` | INT |  |  |
| `allergen` | TEXT | NOT NULL |  |
| `reaction` | TEXT |  |  |
| `severity` | TEXT |  |  |
| `onset_date` | TIMESTAMPTZ |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** UNIQUE `ux_hkit_allergies_parent` — `(tenant_id, user_id, clinical_record_id) WHERE clinical_record_id IS NOT NULL`

### `hkit_immunizations`

Immunization entries extracted from HealthKit clinical records (vaccine code/name, administered date, lot number). Same parent-pointer pattern as `hkit_allergies`.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | INT |  | `generated always as identity` |
| `user_id` | UUID | NOT NULL |  |
| `clinical_record_id` | INT |  |  |
| `vaccine_code` | TEXT |  |  |
| `vaccine_name` | TEXT | NOT NULL |  |
| `administered_date` | TIMESTAMPTZ |  |  |
| `lot_number` | TEXT |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** UNIQUE `ux_hkit_immunizations_parent` — `(tenant_id, user_id, clinical_record_id) WHERE clinical_record_id IS NOT NULL`

### `hkit_medications`

Medication entries extracted from HealthKit clinical records (code, name, dosage, status, authored date). Same parent-pointer pattern as `hkit_allergies`.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | INT |  | `generated always as identity` |
| `user_id` | UUID | NOT NULL |  |
| `clinical_record_id` | INT |  |  |
| `medication_code` | TEXT |  |  |
| `medication_name` | TEXT | NOT NULL |  |
| `dosage` | TEXT |  |  |
| `status` | TEXT |  |  |
| `authored_date` | TIMESTAMPTZ |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** UNIQUE `ux_hkit_medications_parent` — `(tenant_id, user_id, clinical_record_id) WHERE clinical_record_id IS NOT NULL`

### `hkit_sync_anchors`

Opaque `HKAnchoredObjectQuery` anchors per (user, device, sample type) so incremental HealthKit sync can resume after reinstall without re-reading the full history window.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `user_id` | UUID | NOT NULL |  |
| `device_id` | TEXT | NOT NULL |  |
| `sample_type` | TEXT | NOT NULL |  |
| `anchor` | TEXT | NOT NULL |  |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` |

- **Primary key:** `(tenant_id, user_id, device_id, sample_type)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** none beyond the primary key

### `healthkit_import_jobs`

Bulk HealthKit import jobs: total/processed record counts, status, error message, and timing.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `user_id` | UUID | NOT NULL |  |
| `status` | TEXT |  | `'pending'` |
| `total_records` | INT |  |  |
| `processed_records` | INT |  | `0` |
| `error_message` | TEXT |  |  |
| `started_at` | TIMESTAMPTZ |  |  |
| `completed_at` | TIMESTAMPTZ |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(tenant_id, id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** none beyond the primary key

## Mobile sync

Support tables for the mobile client's SQLite sync: the sync queue, per-device schema version reporting, and user corrections to synced records.

### `sync_queue`

Queue for mobile SQLite sync: target table, record id, operation (insert / update / delete), JSONB payload, retry count, and CHECK-constrained status.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `user_id` | UUID | NOT NULL |  |
| `table_name` | TEXT | NOT NULL |  |
| `record_id` | UUID | NOT NULL |  |
| `operation` | TEXT | NOT NULL |  |
| `payload` | JSONB |  |  |
| `status` | TEXT |  | `'pending'` |
| `retry_count` | INT |  | `0` |
| `error_message` | TEXT |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `processed_at` | TIMESTAMPTZ |  |  |

- **Primary key:** `(tenant_id, id)`
- **Check:** `sync_queue_operation_check`: `(operation IN ('insert', 'update', 'delete'))`
- **Check:** `sync_queue_status_check`: `(status IN ('pending', 'syncing', 'synced', 'failed'))`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_sync_queue_tenant_user_status` — `USING btree (tenant_id, user_id, status)`

### `schema_versions`

Per-device schema version reporting from mobile clients — unique per (user, device) — with app version, platform, and last sync time. Distinct from the global `schema_version` marker table.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `user_id` | UUID | NOT NULL |  |
| `device_id` | TEXT | NOT NULL |  |
| `schema_version` | INT | NOT NULL |  |
| `app_version` | TEXT |  |  |
| `platform` | TEXT |  |  |
| `last_sync` | TIMESTAMPTZ |  |  |
| `created_at` | TIMESTAMPTZ |  | `now()` |
| `updated_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(tenant_id, id)`
- **Unique:** `(tenant_id, user_id, device_id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** none beyond the primary key

### `data_corrections`

User corrections to synced records (workout activity names, food names): record type, corrected field, original and new values. Note the PK is `id` alone — the only user-data table without a composite `(tenant_id, id)` PK.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `id` | UUID | NOT NULL | `gen_random_uuid()` |
| `tenant_id` | SMALLINT | NOT NULL | `1` |
| `user_id` | UUID | NOT NULL |  |
| `record_type` | TEXT | NOT NULL |  |
| `source_app` | TEXT |  |  |
| `corrected_field` | TEXT | NOT NULL |  |
| `original_value` | TEXT |  |  |
| `new_value` | TEXT | NOT NULL |  |
| `corrected_at` | TIMESTAMPTZ |  | `now()` |

- **Primary key:** `(id)`
- **Check:** `record_type IN ('workout', 'food')`
- **Check:** `corrected_field IN ('activityType', 'food_name')`
- **FK:** `(tenant_id)` → `tenants(id)`
- **FK:** `(tenant_id, user_id)` → `users(tenant_id, id)` ON DELETE CASCADE
- **Index:** `idx_data_corrections_user` — `USING btree (tenant_id, user_id)`
- **Index:** `idx_data_corrections_date` — `USING btree (corrected_at)`

## Foreign-key map

**To `users(tenant_id, id)`** — 67 tables carry the composite FK `(tenant_id, user_id)` (or the named creator/owner column) referencing `users`. All are `ON DELETE CASCADE` except the ones listed:

- `feedback` — ON DELETE SET NULL
- `households` — ON DELETE RESTRICT
- `appointment_prep` — no ON DELETE action (default NO ACTION)

**All other foreign keys:**

| From | Columns | To | On delete |
|------|---------|----|-----------|
| `users` | `(tenant_id)` | `tenants(id)` | NO ACTION |
| `health_inputs` | `(tenant_id, timeframe_id)` | `timeframes(tenant_id, id)` | SET NULL |
| `stacks` | `(tenant_id, timeframe_id)` | `timeframes(tenant_id, id)` | SET NULL |
| `stack_inputs` | `(tenant_id, stack_id)` | `stacks(tenant_id, id)` | CASCADE |
| `stack_inputs` | `(tenant_id, health_input_id)` | `health_inputs(tenant_id, id)` | CASCADE |
| `remedies` | `(tenant_id, condition_id)` | `health_conditions(tenant_id, id)` | SET NULL |
| `remedy_inputs` | `(tenant_id, remedy_id)` | `remedies(tenant_id, id)` | CASCADE |
| `remedy_inputs` | `(tenant_id, input_id)` | `health_inputs(tenant_id, id)` | CASCADE |
| `health_input_log` | `(tenant_id, input_id)` | `health_inputs(tenant_id, id)` | CASCADE |
| `health_input_log` | `(tenant_id, stack_id)` | `stacks(tenant_id, id)` | SET NULL |
| `health_food_logv2` | `(tenant_id, food_item_id)` | `health_food_itemsv2(tenant_id, id)` | CASCADE |
| `health_food_logv2` | `(tenant_id, timeframe_id)` | `timeframes(tenant_id, id)` | SET NULL |
| `meal_items` | `(tenant_id, meal_id)` | `meals(tenant_id, id)` | CASCADE |
| `meal_items` | `(tenant_id, food_item_id)` | `health_food_itemsv2(tenant_id, id)` | CASCADE |
| `diet_catalog` | `(tenant_id, parent_diet_code)` | `diet_catalog(tenant_id, code)` | NO ACTION |
| `household_members` | `(tenant_id, household_id)` | `households(tenant_id, id)` | CASCADE |
| `projected_reminders` | `(tenant_id, stack_id)` | `stacks(tenant_id, id)` | CASCADE |
| `projected_reminders` | `(tenant_id, health_input_id)` | `health_inputs(tenant_id, id)` | CASCADE |
| `projected_reminders` | `(tenant_id, timeframe_id)` | `timeframes(tenant_id, id)` | CASCADE |
| `data_corrections` | `(tenant_id)` | `tenants(id)` | NO ACTION |
| `hkit_records` | `(record_type_id)` | `hkit_record_types(id)` | NO ACTION |
| `document_folders` | `(tenant_id, parent_id)` | `document_folders(tenant_id, id)` | RESTRICT |
| `documents` | `(tenant_id, folder_id)` | `document_folders(tenant_id, id)` | RESTRICT |
| `document_pages` | `(tenant_id, document_id)` | `documents(tenant_id, id)` | CASCADE |
| `document_annotations` | `(tenant_id, document_id)` | `documents(tenant_id, id)` | CASCADE |
| `user_provider_contacts` | `(tenant_id)` | `tenants(id)` | NO ACTION |

**References by convention (no FK constraint):** `log_promotions.source_log_id` (into `health_input_log` or `health_food_logv2` per `source_table`), `log_promotions.suggested_catalog_id` (into `health_inputs` or `health_food_itemsv2`), `health_metrics.source_log_id` (nutrition projector provenance), `hkit_records.source_id` (into `hkit_sources`), the `clinical_record_id` columns on the four `hkit_*` extraction tables (into `hkit_clinical_records`), `reminders.health_input_id`, and the bare UUID link columns noted on `appointments` and `user_provider_contacts`.

## Trigger inventory

Five triggers. The four `updated_at` triggers exist only where the application does not already manage `updated_at` in its UPDATE statements — see `UpdatedAtPolicy.md` for the policy and the full reasoning.

| Trigger | Table | Fires | Function |
|---------|-------|-------|----------|
| `update_health_family_history_updated_at` | `health_family_history` | BEFORE UPDATE | `update_updated_at()` |
| `update_health_social_history_updated_at` | `health_social_history` | BEFORE UPDATE | `update_updated_at()` |
| `update_health_surgical_history_updated_at` | `health_surgical_history` | BEFORE UPDATE | `update_updated_at()` |
| `set_dietary_settings_updated_at` | `dietary_settings` | BEFORE UPDATE | `update_updated_at()` |
| `trg_users_seed_system_folders` | `users` | AFTER INSERT | `seed_user_system_folders()` |

### Functions

| Function | Purpose |
|----------|---------|
| `cleanup_expired_sessions()` | Deletes expired `sessions` rows; returns the count. |
| `update_updated_at()` | Generic BEFORE UPDATE trigger body: sets `NEW.updated_at = now()`. |
| `seed_user_system_folders()` | AFTER INSERT on `users`: creates the `Documents` and `Fax` system folders. SECURITY DEFINER. |

## Extensions

| Extension | Used for |
|-----------|----------|
| `pgcrypto` | `gen_random_uuid()` defaults on nearly every PK. |
| `uuid-ossp` | Legacy UUID helpers. |
| `vector` (pgvector) | `VECTOR(768)` embedding columns and IVFFlat cosine indexes. Installed unpinned: the `vector(768)` type and cosine/IVFFlat operators are stable across pgvector releases, and the base image tag is mutable. |

## Embedding columns

8 `VECTOR(768)` columns, each with a matching IVFFlat index (`vector_cosine_ops`, `lists = 100`). Model, best-effort write rules, and the 512-token context window are documented in `EmbeddingDesign.md`.

| Table | Column |
|-------|--------|
| `health_inputs` | `embedding_name` |
| `health_conditions` | `embedding_condition` |
| `health_allergies` | `embedding_allergy_full` |
| `health_observations` | `embedding_content` |
| `health_food_itemsv2` | `embedding_name` |
| `mobile_events` | `embedding_event_text` |
| `documents` | `embedding_content` |
| `document_annotations` | `embedding_body` |

## Security column annotations

Every column whose name matches the sensitive-name pattern (`password|secret|token` substring) carries an `algo:` comment in the schema declaring how the value is protected (`argon2id`, `sha256`, `plaintext` with rationale, `tbd` for the grandfathered Garmin credential columns, or `not-a-credential` for pattern false-positives). `DataModel3/code_query_audit.py` reads these comments and fails when one is missing or unrecognized.

## Seed data

- `tenants`: one row (`id = 1`, name `Minowa`, slug `minowa`).
- `diet_catalog`: 21 diets across the exclusion / nutrient_pattern / medical categories.
- `schema_version`: version marker rows, ending at the running marker `11.0.0-home`.
- `document_folders`: `Documents` and `Fax` system folders, created per user by trigger rather than static seed.

## Summary

| Metric | Count |
|--------|-------|
| Tables | 73 |
| Columns | 984 |
| Indexes (`CREATE INDEX`) | 118 |
| — of which unique | 11 |
| Foreign keys | 93 |
| CHECK constraints | 50 |
| Triggers | 5 |
| Functions | 3 |
| Extensions | 3 |
| `VECTOR(768)` columns | 8 |


# Unused Columns Audit — UserApp

Date: 2026-04-09

## What this is

Automated audit of every column in the `healthv10` schema (`Infrastructure/init/docker-init-home/02-home_schema.sql`) against the UserApp code base (`UserApp/` — excluding `.venv`, `__pycache__`, `.pytest_cache`, `.ruff_cache`). A column is **not read** if no word-boundary match appears in any `.py`, `.html`, `.js`, `.sql`, or template file.

## Methodology

1. Parse `CREATE TABLE public.<name>` blocks from the schema file, extracting column names (skipping constraint/foreign-key lines).
2. Walk `UserApp/` recursively; read every source/template file.
3. For each column, count word-boundary (`\b<name>\b`) matches across the corpus.
4. Columns with **zero hits** are reported.
5. Results are partitioned into four buckets:
   - **Mobile-sync contract (DO NOT DROP)** — `sqlite_id` and `synced_at`. Deliberate data contract for mobile SQLite ↔ hosted PostgreSQL sync. Covered separately below with cohort warning.
   - **Distinctive never-read** — high confidence the column is genuinely unused.
   - **Common-name never-read** — generic names (`id`, `name`, `value`, etc.) where zero hits is suspicious; if `near_table=True` the name co-occurs with its table name within 400 chars in some file (weak positive evidence).
   - **Implicit/framework never-read** — `tenant_id`, `created_at`, `updated_at`, `deleted_at`. These are typically maintained by DB defaults or triggers, so zero Python hits does not mean removable.

## Known limitations (caveats before you delete anything)

- **`SELECT *`** — if a route reads a table with `SELECT *`, every column of that table is implicitly read, but this audit will still flag unreferenced names. Check the table's code path before dropping.
- **Dynamic column access** — `row[col_name]` where `col_name` is a variable cannot be resolved statically; those reads are invisible here.
- **DB-only use** — columns used only in SQL triggers or views are not visible to this UserApp-scoped audit.
- **Migration / seed scripts** — some columns exist to support account provisioning and imports (e.g. `username`).
- **Future / planned work** — columns added for features not yet wired up will look unused.

## Summary

- Tables parsed: **73**
- Columns parsed: **948**
- **Mobile-sync contract columns never read (DO NOT DROP): 0** across 0 tables
- Distinctive columns never read: **110**
- Common-name columns never read: **0**
- Implicit/framework columns never read: **0**

## ⚠️ Mobile-sync contract columns — DO NOT DROP

**The `sqlite_id` and `synced_at` columns listed below are part of a deliberate mobile-app data contract.** They exist to support SQLite ↔ hosted PostgreSQL sync from the mobile app. They are legitimately unread in UserApp today because **bidirectional mobile sync is DEFERRED** (see [`Synchronization/2026-03-28-DECISION.md`](../Synchronization/2026-03-28-DECISION.md) — blocked on per-user encryption). The current `/api/v1/healthkit/sync` handler in `UserApp/webapp/app.py` is write-only for `hkit_*`, `health_metrics`, and `health_blood_pressure_readings`, and does not populate `sqlite_id` / `synced_at` on any table.

**Do not drop these columns, do not drop their indexes (`idx_users_sqlite`, `idx_health_inputs_sqlite`, `idx_promotions_sqlite`), and do not classify them as dead code.** Coordinate with the mobile developer and re-read the Synchronization decision docs before making any schema change that touches these columns. Note the schema explicitly excludes `mobile_events` from this contract: "Append-only event log. Server-authoritative (no sqlite_id, no mobile sync)."

_None._

## Top candidates: distinctive columns never read

Columns here are strong candidates for review because their names are distinctive enough that zero hits is unlikely to be a false positive.

### `api_tokens` (2 columns)

| Column | Type |
|---|---|
| `last_ip` | INET |
| `totp_verified_at` | TIMESTAMPTZ |

### `appointment_prep` (4 columns)

| Column | Type |
|---|---|
| `appointment_date` | date NOT NULL |
| `date_range_end` | date NOT NULL |
| `date_range_start` | date NOT NULL |
| `health_data_snapshot` | jsonb |

### `audit_log` (2 columns)

| Column | Type |
|---|---|
| `target_id` | text |
| `target_type` | text |

### `daily_energy` (2 columns)

| Column | Type |
|---|---|
| `current_spoons` | integer |
| `starting_spoons` | integer |

### `data_corrections` (1 columns)

| Column | Type |
|---|---|
| `corrected_at` | TIMESTAMPTZ DEFAULT NOW() |

### `documents` (3 columns)

| Column | Type |
|---|---|
| `local_expires_at` | TIMESTAMPTZ |
| `remote_bucket` | TEXT |
| `stashed_at` | TIMESTAMPTZ |

### `email_verification_tokens` (1 columns)

| Column | Type |
|---|---|
| `verified_at` | timestamp with time zone |

### `feedback` (2 columns)

| Column | Type |
|---|---|
| `admin_notes` | text |
| `screen_resolution` | text |

### `garm_daily_summ` (7 columns)

| Column | Type |
|---|---|
| `calories_consumed` | integer |
| `hydration_goal` | integer |
| `hydration_intake` | integer |
| `intensity_time_goal` | integer |
| `moderate_activity_time` | integer |
| `sweat_loss` | integer |
| `vigorous_activity_time` | integer |

### `garm_upload_date` (2 columns)

| Column | Type |
|---|---|
| `upload_date` | date |
| `upload_timestamp` | timestamp with time zone DEFAULT CURRENT_TIMESTAMP |

### `garmin_credentials` (2 columns)

| Column | Type |
|---|---|
| `oauth1_secret` | text |
| `oauth1_token` | text |

### `health_allergies` (1 columns)

| Column | Type |
|---|---|
| `substance` | allergy_type text |

### `health_blood_pressure_readings` (1 columns)

| Column | Type |
|---|---|
| `arm` | text |

### `health_food_itemsv2` (10 columns)

| Column | Type |
|---|---|
| `calcium_pct` | numeric |
| `cholesterol_mg` | numeric |
| `custom_nutrients` | jsonb |
| `diet_flags` | JSONB |
| `iron_pct` | numeric |
| `is_custom` | boolean DEFAULT true |
| `saturated_fat_g` | numeric |
| `trans_fat_g` | numeric |
| `vitamin_a_pct` | numeric |
| `vitamin_c_pct` | numeric |

### `health_input_log` (1 columns)

| Column | Type |
|---|---|
| `skip_reason` | text |

### `health_inputs` (6 columns)

| Column | Type |
|---|---|
| `current_quantity` | numeric |
| `pharmacy` | text |
| `prescribing_doctor` | text |
| `refill_reminder_days` | integer |
| `refills_remaining` | integer |
| `rx_number` | text |

### `health_observations` (1 columns)

| Column | Type |
|---|---|
| `related_inputs` | uuid[] |

### `hkit_medications` (1 columns)

| Column | Type |
|---|---|
| `medication_code` | text |

### `household_members` (3 columns)

| Column | Type |
|---|---|
| `household_id` | UUID NOT NULL |
| `joined_at` | TIMESTAMPTZ DEFAULT now() |
| `left_at` | TIMESTAMPTZ |

### `households` (1 columns)

| Column | Type |
|---|---|
| `created_by` | UUID NOT NULL |

### `password_reset_tokens` (1 columns)

| Column | Type |
|---|---|
| `used_at` | timestamp with time zone |

### `remedies` (1 columns)

| Column | Type |
|---|---|
| `effectiveness_rating` | integer |

### `remedy_inputs` (2 columns)

| Column | Type |
|---|---|
| `dosage_for_remedy` | text |
| `remedy_id` | uuid NOT NULL |

### `reminders` (1 columns)

| Column | Type |
|---|---|
| `last_triggered` | TIMESTAMPTZ |

### `schema_version` (1 columns)

| Column | Type |
|---|---|
| `applied_at` | timestamp with time zone DEFAULT now() |

### `schema_versions` (1 columns)

| Column | Type |
|---|---|
| `schema_version` | integer NOT NULL |

### `sessions` (1 columns)

| Column | Type |
|---|---|
| `totp_verified_at` | timestamp with time zone |

### `sync_queue` (2 columns)

| Column | Type |
|---|---|
| `processed_at` | timestamp with time zone |
| `retry_count` | integer DEFAULT 0 |

### `tenants` (2 columns)

| Column | Type |
|---|---|
| `domain` | text |
| `slug` | text NOT NULL UNIQUE |

### `user_preferences` (18 columns)

| Column | Type |
|---|---|
| `avatar_style` | text DEFAULT 'default' |
| `color_scheme` | text DEFAULT 'system' |
| `compact_mode` | boolean DEFAULT false |
| `font_size` | text DEFAULT 'medium' |
| `notification_email` | boolean DEFAULT true |
| `notification_push` | boolean DEFAULT true |
| `notification_sms` | boolean DEFAULT false |
| `privacy_data_retention` | text DEFAULT 'forever' |
| `privacy_share_anonymous` | boolean DEFAULT false |
| `reminder_logging` | boolean DEFAULT true |
| `reminder_medications` | boolean DEFAULT true |
| `show_animations` | boolean DEFAULT true |
| `sidebar_hidden` | text[] DEFAULT NULL |
| `sidebar_order` | text[] DEFAULT NULL |
| `units_blood_glucose` | text DEFAULT 'mg_dl' |
| `units_height` | text DEFAULT 'ft_in' |
| `units_temperature` | text DEFAULT 'fahrenheit' |
| `units_weight` | text DEFAULT 'lbs' |

### `user_provider_contacts` (3 columns)

| Column | Type |
|---|---|
| `npi_candidates` | JSONB |
| `npi_data` | JSONB |
| `npi_number` | VARCHAR(20) |

### `users` (24 columns)

| Column | Type |
|---|---|
| `accepts_sms` | boolean DEFAULT false |
| `account_type` | text DEFAULT 'free'::text |
| `avatar_photo_url` | text |
| `birth_month` | integer |
| `birth_year` | integer |
| `contact_hours` | jsonb |
| `country` | text DEFAULT 'US' |
| `created_at_ms` | bigint |
| `deployment_type` | text DEFAULT 'saas'::text |
| `device_at_creation` | text |
| `fax_number` | text |
| `gender_identity` | text |
| `hash_salt` | text |
| `last_active_at` | timestamp with time zone |
| `locale` | text DEFAULT 'en-US'::text |
| `onboarding_complete` | integer DEFAULT 0 |
| `phone_number` | text |
| `postal_code` | text |
| `preferred_language` | text DEFAULT 'en'::text |
| `promo_code` | character varying(50) |
| `pronouns` | text |
| `state_province` | text |
| `track_energy_spoons` | integer DEFAULT 0 |
| `user_hash` | text |

## Review bucket: common-name columns never read

Generic column names where direct grep found no hits. `near_table=yes` means the column and table names co-occur somewhere in the corpus within 400 characters (weak evidence the column *is* referenced in its table's context).

_None._

## Implicit / framework columns never read

These are `tenant_id`, `created_at`, `updated_at`, `deleted_at`. Zero direct hits is usually benign — they're set by DB defaults, maintained by triggers, or serialized generically. **Do not drop these based on this report.**

_None._

## Tables with NO unused columns

`appointments`, `diet_catalog`, `dietary_settings`, `document_annotations`, `document_folders`, `document_pages`, `garm_hr`, `garm_rr`, `garm_sleep`, `garm_sleep_events`, `garm_stress`, `garmin_sync_jobs`, `health_blood_work`, `health_conditions`, `health_family_history`, `health_food_logv2`, `health_metrics`, `health_social_history`, `health_surgical_history`, `health_vaccinations`, `healthkit_import_jobs`, `hkit_activity_summaries`, `hkit_allergies`, `hkit_clinical_records`, `hkit_immunizations`, `hkit_lab_observations`, `hkit_record_types`, `hkit_records`, `hkit_sources`, `hkit_sync_anchors`, `hkit_user_profile`, `hkit_workouts`, `log_promotions`, `meal_items`, `meals`, `mobile_events`, `projected_reminders`, `stack_inputs`, `stacks`, `timeframes`, `user_devices`

## How to re-run this audit

```
python3 DataModel3/unused_columns_audit.py
```

The script re-parses the schema and re-scans `UserApp/` each time and overwrites this report. Adjust `SCHEMA`, `USERAPP`, and `SKIP_DIRS` constants at the top of the script to retarget it at another service tree.

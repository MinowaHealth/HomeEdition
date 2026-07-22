# Home Edition Database ERD — healthv10

**Date: 2026-07-21 16:30 PDT**

Generated from `Infrastructure/init/docker-init-home/02-home_schema.sql` (schema version `11.1.0-home`), 75 tables. That file is the schema source of truth; the companion `HomeDatabaseReport.md` carries the full per-table column detail.

Every user-owned table carries `tenant_id` (always `1`) as a fixed app-level scoping convention; per-user privacy is enforced in the application with explicit `user_id` predicates on every query.

## How to read these diagrams

- One diagram per domain — a single 75-table diagram is unreadable.
- `users` is the hub of the whole schema: 67 tables carry the composite FK `(tenant_id, user_id)` → `users(tenant_id, id)`, `ON DELETE CASCADE` unless a diagram notes otherwise. Each domain diagram repeats a slim `users` stub (full definition in Core & authentication).
- Solid lines are real FK constraints; dotted lines are references by convention (a UUID/integer column with no constraint, resolved by the application).
- Entities show PK/FK columns plus a few salient attributes, not the full column list. `vector` attributes are `VECTOR(768)` pgvector embedding columns (see `EmbeddingDesign.md`).

## Core & authentication

```mermaid
erDiagram
    tenants {
        smallint id PK "seeded row: id = 1"
        text name
        text slug UK
        jsonb settings
    }
    users {
        smallint tenant_id PK, FK
        uuid id PK
        text email UK "unique per tenant"
        text display_name
        text password_hash "argon2id"
        boolean totp_enabled
        text home_timezone
        text unit_system "imperial | metric"
        integer track_energy_spoons
    }
    sessions {
        smallint tenant_id PK, FK
        uuid session_id PK
        uuid user_id FK
        timestamptz expires_at
        text session_type
        timestamptz totp_verified_at
    }
    password_reset_tokens {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        text token
        timestamptz used_at
    }
    email_verification_tokens {
        smallint tenant_id PK
        uuid id PK
        text email "no user FK - pre-signup"
        text token
        timestamptz verified_at
    }
    api_tokens {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        varchar token_hash "sha256"
        text token_type
        timestamptz revoked_at
    }
    user_devices {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        text device_id UK "unique per user"
        text platform
        boolean can_embed
    }
    user_preferences {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK, UK
        text theme
        text[] bp_devices "BP meter pick list"
        text timezone_reminder_mode
    }
    tenants ||--o{ users : "tenant_id"
    users ||--o{ sessions : "logs in"
    users ||--o{ password_reset_tokens : ""
    users ||--o{ api_tokens : ""
    users ||--o{ user_devices : ""
    users ||--o| user_preferences : "1:1"
```

`email_verification_tokens` is deliberately unconnected: it exists before the account does. Inserting a `users` row fires the `trg_users_seed_system_folders` trigger, which creates that member's four system folders in the Documents domain.

## System & telemetry

```mermaid
erDiagram
    users {
        smallint tenant_id PK
        uuid id PK "hub - defined in Core"
    }
    schema_version {
        varchar version PK "marker: 11.0.0-home"
        timestamptz applied_at
        text description
    }
    audit_log {
        smallint tenant_id PK
        bigint id PK
        uuid user_id FK
        text action
        jsonb details
        inet ip_address
    }
    feedback {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK "SET NULL on user delete"
        text feedback_type
        text content
        text status
    }
    mobile_events {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        text screen
        text event_text
        vector embedding_event_text
    }
    users ||--o{ audit_log : "acts"
    users ||--o{ feedback : "submits"
    users ||--o{ mobile_events : "emits"
```

`schema_version` is global (no `tenant_id`, no FKs). `feedback.user_id` is `ON DELETE SET NULL` so feedback survives account removal — the one non-CASCADE in this domain.

## Health tracking — medications & supplements

```mermaid
erDiagram
    users {
        smallint tenant_id PK
        uuid id PK "hub - defined in Core"
    }
    timeframes {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        text name
        time time_of_day
        text frequency
    }
    health_inputs {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        uuid timeframe_id FK
        text name "unique-active per user"
        text input_type
        vector embedding_name
    }
    stacks {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        uuid timeframe_id FK
        text name "unique-active per user"
    }
    stack_inputs {
        smallint tenant_id PK
        uuid id PK
        uuid stack_id FK
        uuid health_input_id FK
        text dosage_override
    }
    remedies {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        uuid condition_id FK "to health_conditions"
        text name
        integer effectiveness_rating
    }
    remedy_inputs {
        smallint tenant_id PK
        uuid id PK
        uuid remedy_id FK
        uuid input_id FK
        text dosage_for_remedy
    }
    health_input_log {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        uuid input_id FK "nullable"
        uuid stack_id FK "nullable"
        text free_text "or catalog input_id"
        timestamptz promoted_at
    }
    log_promotions {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        text source_table
        uuid source_log_id "by convention"
        uuid suggested_catalog_id "by convention"
        text status
    }
    health_input_acquisitions {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        uuid health_input_id FK "nullable, SET NULL"
        text item_name
        date acquired_date
        numeric quantity
    }
    users ||--o{ timeframes : ""
    users ||--o{ health_inputs : ""
    users ||--o{ stacks : ""
    users ||--o{ health_input_log : ""
    users ||--o{ health_input_acquisitions : ""
    health_inputs ||--o{ health_input_acquisitions : "SET NULL"
    timeframes ||--o{ health_inputs : "SET NULL"
    timeframes ||--o{ stacks : "SET NULL"
    stacks ||--o{ stack_inputs : ""
    health_inputs ||--o{ stack_inputs : ""
    remedies ||--o{ remedy_inputs : ""
    health_inputs ||--o{ remedy_inputs : ""
    health_inputs ||--o{ health_input_log : "nullable"
    stacks ||--o{ health_input_log : "SET NULL"
    health_input_log ||..o{ log_promotions : "source_log_id"
```

Cross-domain FKs: `remedies.condition_id` → `health_conditions` (Clinical history, SET NULL). `log_promotions` also soft-references `health_food_logv2` and `health_food_itemsv2` (Dietary & food) via `source_table` + `source_log_id` / `suggested_catalog_id` — no FK constraints, because each column spans two possible target tables. A `health_input_log` row holds either a catalog `input_id` or `free_text` (CHECK-enforced). `health_input_acquisitions` is the supply-arrival journal: a catalog-linked arrival bumps `health_inputs.current_quantity`, dose logging decrements it.

## Scheduling & reminders

```mermaid
erDiagram
    users {
        smallint tenant_id PK
        uuid id PK "hub - defined in Core"
    }
    timeframes {
        smallint tenant_id PK
        uuid id PK "defined in Health tracking"
    }
    stacks {
        smallint tenant_id PK
        uuid id PK "defined in Health tracking"
    }
    health_inputs {
        smallint tenant_id PK
        uuid id PK "defined in Health tracking"
    }
    reminders {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        text title
        text category
        text time "HH:mm"
        text frequency
        uuid health_input_id "by convention"
    }
    projected_reminders {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        uuid stack_id FK "exactly one of"
        uuid health_input_id FK "stack or input"
        uuid timeframe_id FK
        time scheduled_time
        text timezone_mode
    }
    appointments {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        text title
        timestamptz appointment_datetime
        text status
    }
    appointment_prep {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK "no ON DELETE action"
        date appointment_date
        jsonb health_data_snapshot
    }
    users ||--o{ reminders : ""
    users ||--o{ appointments : ""
    users ||--o{ appointment_prep : ""
    timeframes ||--o{ projected_reminders : "derived from"
    stacks ||--o{ projected_reminders : "source (xor)"
    health_inputs ||--o{ projected_reminders : "source (xor)"
```

The `timeframes` / `stacks` / `health_inputs` stubs are defined in the Health tracking domain; the three `projected_reminders` FKs shown are real constraints (all CASCADE), and a CHECK enforces exactly one of `stack_id` / `health_input_id`. `reminders.health_input_id` is a bare UUID with no FK. `appointment_prep`'s FK to `users` carries no ON DELETE action (the only user FK in the schema besides `feedback` and `households` that is not CASCADE).

## Dietary, food & household

```mermaid
erDiagram
    users {
        smallint tenant_id PK
        uuid id PK "hub - defined in Core"
    }
    health_food_itemsv2 {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        text name
        numeric calories
        vector embedding_name
        integer fdc_id "reserved, unpopulated"
    }
    health_food_logv2 {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        uuid food_item_id FK "nullable"
        uuid timeframe_id FK "to timeframes"
        text free_text "or catalog food_item_id"
        timestamptz promoted_at
    }
    meals {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        text name
        text meal_type
    }
    meal_items {
        smallint tenant_id PK
        uuid id PK
        uuid meal_id FK
        uuid food_item_id FK
        numeric servings
    }
    dietary_settings {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        text_array diet_codes "values from diet_catalog"
        date effective_date
        date end_date "NULL = current"
    }
    diet_catalog {
        smallint tenant_id PK
        text code PK "21 seeded diets"
        text category
        text parent_diet_code FK "self-reference"
        boolean is_clinical
    }
    households {
        smallint tenant_id PK
        uuid id PK
        uuid created_by FK "RESTRICT"
        text name
        text household_type
    }
    household_members {
        smallint tenant_id PK
        uuid household_id PK, FK
        uuid user_id PK, FK
        text role
        timestamptz left_at "NULL = active"
    }
    users ||--o{ health_food_itemsv2 : ""
    users ||--o{ health_food_logv2 : ""
    users ||--o{ meals : ""
    users ||--o{ dietary_settings : ""
    users ||--o{ households : "created_by"
    users ||--o{ household_members : ""
    health_food_itemsv2 ||--o{ health_food_logv2 : "nullable"
    health_food_itemsv2 ||--o{ meal_items : ""
    meals ||--o{ meal_items : ""
    diet_catalog ||--o{ diet_catalog : "parent_diet_code"
    diet_catalog ||..o{ dietary_settings : "diet_codes values"
    households ||--o{ household_members : ""
```

Cross-domain FK: `health_food_logv2.timeframe_id` → `timeframes` (Health tracking, SET NULL). `dietary_settings.diet_codes` values are validated against `diet_catalog.code` in the application — an array column cannot carry an FK. `diet_catalog` is seeded reference data and is the schema's one deliberate household-shared read. `households.created_by` is RESTRICT, not CASCADE.

## Vitals & metrics

```mermaid
erDiagram
    users {
        smallint tenant_id PK
        uuid id PK "hub - defined in Core"
    }
    health_food_logv2 {
        smallint tenant_id PK
        uuid id PK "defined in Dietary and food"
    }
    health_blood_pressure_readings {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        integer systolic
        integer diastolic
        integer pulse
    }
    health_blood_work {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        date test_date
        text test_name
        text loinc_code
    }
    health_metrics {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        text metric_type "37-value CHECK enum"
        numeric value
        uuid source_log_id "by convention"
    }
    health_observations {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        text content
        integer severity
        vector embedding_content
    }
    daily_energy {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        date date UK "unique per user per day"
        integer starting_spoons
        integer current_spoons
    }
    users ||--o{ health_blood_pressure_readings : ""
    users ||--o{ health_blood_work : ""
    users ||--o{ health_metrics : ""
    users ||--o{ health_observations : ""
    users ||--o{ daily_energy : ""
    health_food_logv2 ||..o{ health_metrics : "nutrition projection"
```

`health_metrics.source_log_id` is an FK-by-convention: the nutrition projector stamps it with the `health_food_logv2` row a projected `metric_type = 'nutrition'` metric came from, and dedupe logic branches on whether it is NULL (partial unique index for external imports, provenance lookup for projected rows).

## Clinical history

```mermaid
erDiagram
    users {
        smallint tenant_id PK
        uuid id PK "hub - defined in Core"
    }
    health_conditions {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        text name
        text icd10_code
        text status
        vector embedding_condition
    }
    health_allergies {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        text allergen
        text severity
        vector embedding_allergy_full
    }
    health_family_history {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        text relationship
        text condition_name
    }
    health_social_history {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        text category
        text status
    }
    health_surgical_history {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        text procedure_name
        date procedure_date
    }
    health_vaccinations {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        text vaccine_name
        date administered_date
    }
    users ||--o{ health_conditions : ""
    users ||--o{ health_allergies : ""
    users ||--o{ health_family_history : ""
    users ||--o{ health_social_history : ""
    users ||--o{ health_surgical_history : ""
    users ||--o{ health_vaccinations : ""
```

Cross-domain FK into this domain: `remedies.condition_id` → `health_conditions` (Health tracking, SET NULL). `health_allergies` is manual entry from any platform; `hkit_allergies` (HealthKit domain) is the import-side counterpart.

## Contacts

```mermaid
erDiagram
    users {
        smallint tenant_id PK
        uuid id PK "hub - defined in Core"
    }
    user_provider_contacts {
        smallint tenant_id PK, FK
        uuid id PK
        uuid user_id FK
        text display_name
        text phone
        text practitioner_type
        text relationship_type
        text verification_status
        varchar npi_number
    }
    users ||--o{ user_provider_contacts : "personal contact book"
```

Each member's private address book of their own doctors, dentists, therapists, and other practitioners. `tenant_id` carries a direct FK to `tenants` here (one of three tables that do). The NPI columns hold optional registry-lookup results for user-entered contacts.

## Documents & embeddings

```mermaid
erDiagram
    users {
        smallint tenant_id PK
        uuid id PK "hub - defined in Core"
    }
    document_folders {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        uuid parent_id FK "self-reference, RESTRICT"
        text name UK "unique per parent (live)"
        boolean is_system "Documents, Fax, AI Sessions, Episode Reports"
    }
    documents {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        uuid folder_id FK "RESTRICT"
        text filename
        text ocr_status
        text storage_tier
        vector embedding_content
        tsvector fts "generated, doc-level FTS"
        jsonb provenance "AI-written docs"
    }
    document_pages {
        smallint tenant_id PK
        uuid id PK
        uuid document_id FK
        uuid user_id FK
        integer page_number UK "unique per document"
        text ocr_text
    }
    document_annotations {
        smallint tenant_id PK
        uuid id PK
        uuid document_id FK
        uuid user_id FK
        text body
        vector embedding_body
    }
    users ||--o{ document_folders : ""
    users ||--o{ documents : ""
    document_folders ||--o{ document_folders : "parent_id"
    document_folders ||--o{ documents : "RESTRICT"
    documents ||--o{ document_pages : ""
    documents ||--o{ document_annotations : ""
```

All four tables also carry the standard user FK (`document_pages` and `document_annotations` reference both their document and the owning user). The `Documents`, `Fax`, `AI Sessions`, and `Episode Reports` system folders are created per user by the `trg_users_seed_system_folders` trigger; RESTRICT on `documents.folder_id` and `document_folders.parent_id` keeps non-empty folders from being dropped.

## Garmin

```mermaid
erDiagram
    users {
        smallint tenant_id PK
        uuid id PK "hub - defined in Core"
    }
    garmin_credentials {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK, UK "1:1"
        text oauth1_token
        text email
        timestamptz last_sync
    }
    garmin_sync_jobs {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        text job_type
        text status
        jsonb progress
    }
    garm_daily_summ {
        smallint tenant_id PK, FK
        uuid user_id PK, FK
        date calendar_date PK
        integer total_steps
        integer resting_heart_rate
        integer total_kcals
    }
    garm_hr {
        smallint tenant_id PK, FK
        uuid user_id PK, FK
        timestamptz timestamp PK
        integer heart_rate
    }
    garm_rr {
        smallint tenant_id PK, FK
        uuid user_id PK, FK
        timestamptz timestamp PK
        numeric respiratory_rate
    }
    garm_stress {
        smallint tenant_id PK, FK
        uuid user_id PK, FK
        timestamptz timestamp PK
        integer garm_stress
    }
    garm_sleep {
        smallint tenant_id PK, FK
        uuid user_id PK, FK
        date calendar_date PK
        integer deep_sleep_secs
        integer sleep_score
    }
    garm_sleep_events {
        smallint tenant_id PK, FK
        uuid user_id PK, FK
        timestamptz start_time PK
        text sleep_type
    }
    garm_upload_date {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        date upload_date
        text status
    }
    data_sync_log {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        text source "garmin, healthkit"
        uuid job_id "by convention"
        text status "completed or failed"
        jsonb detail
    }
    users ||--o| garmin_credentials : "1:1"
    users ||--o{ garmin_sync_jobs : ""
    users ||--o{ data_sync_log : ""
    users ||--o{ garm_daily_summ : ""
    users ||--o{ garm_hr : ""
    users ||--o{ garm_rr : ""
    users ||--o{ garm_stress : ""
    users ||--o{ garm_sleep : ""
    users ||--o{ garm_sleep_events : ""
    users ||--o{ garm_upload_date : ""
```

The `garm_*` time-series tables use natural composite PKs — `(tenant_id, user_id, timestamp)` or `(tenant_id, user_id, calendar_date)` — instead of surrogate UUIDs; `user_id` is simultaneously part of the PK and of the FK to `users`. These are the highest-volume tables in the database. `data_sync_log` is the cross-ecosystem append-only sync history (Garmin and HealthKit runs), surfaced by `/all-logs` as `type='sync'`.

## HealthKit

```mermaid
erDiagram
    users {
        smallint tenant_id PK
        uuid id PK "hub - defined in Core"
    }
    hkit_record_types {
        integer id PK "global lookup, no tenant_id"
        text type_identifier UK
        text category
    }
    hkit_sources {
        smallint tenant_id PK
        integer id PK
        uuid user_id FK
        text source_name
        text source_bundle_id UK "unique per user"
    }
    hkit_records {
        smallint tenant_id PK
        bigint id PK
        uuid user_id FK
        integer record_type_id FK
        integer source_id "by convention"
        numeric value
        timestamptz start_date
    }
    hkit_activity_summaries {
        smallint tenant_id PK
        bigint id PK
        uuid user_id FK
        date date UK "unique per user"
        integer exercise_time
        integer move_time "wheelchair ring"
    }
    hkit_workouts {
        smallint tenant_id PK
        bigint id PK
        uuid user_id FK
        text workout_type
        numeric duration_seconds
    }
    hkit_user_profile {
        smallint tenant_id PK
        integer id PK
        uuid user_id FK, UK "1:1"
        date date_of_birth
        boolean wheelchair_use
    }
    hkit_clinical_records {
        smallint tenant_id PK
        integer id PK
        uuid user_id FK
        text fhir_resource_type
        text fhir_identifier UK "dedupe on re-import"
        jsonb raw_fhir
    }
    hkit_lab_observations {
        smallint tenant_id PK
        integer id PK
        uuid user_id FK
        integer clinical_record_id "by convention"
        text loinc_code
        numeric value_quantity
    }
    hkit_allergies {
        smallint tenant_id PK
        integer id PK
        uuid user_id FK
        integer clinical_record_id "by convention"
        text allergen
    }
    hkit_immunizations {
        smallint tenant_id PK
        integer id PK
        uuid user_id FK
        integer clinical_record_id "by convention"
        text vaccine_name
    }
    hkit_medications {
        smallint tenant_id PK
        integer id PK
        uuid user_id FK
        integer clinical_record_id "by convention"
        text medication_name
    }
    hkit_sync_anchors {
        smallint tenant_id PK, FK
        uuid user_id PK, FK
        text device_id PK
        text sample_type PK
        text anchor
    }
    healthkit_import_jobs {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        text status
        integer processed_records
    }
    hkit_record_types ||--o{ hkit_records : "record_type_id"
    hkit_sources ||..o{ hkit_records : "source_id"
    hkit_clinical_records ||..o| hkit_lab_observations : "one per parent"
    hkit_clinical_records ||..o| hkit_allergies : "one per parent"
    hkit_clinical_records ||..o| hkit_immunizations : "one per parent"
    hkit_clinical_records ||..o| hkit_medications : "one per parent"
    users ||--o{ hkit_sources : ""
    users ||--o{ hkit_records : ""
    users ||--o{ hkit_clinical_records : ""
    users ||--o| hkit_user_profile : "1:1"
    users ||--o{ healthkit_import_jobs : ""
```

`hkit_records.record_type_id` is the only real FK between HealthKit tables; `source_id` and the four `clinical_record_id` columns are by-convention pointers (dotted), each extraction table limited to one row per parent clinical record by a partial unique index. `hkit_activity_summaries`, `hkit_workouts`, `hkit_sync_anchors`, and the remaining tables all carry the standard user FK (lines omitted above for legibility). See `AppleHealthKitERD.md` for the HealthKit-specific deep dive and `HealthKitDataModel.md` / `AppleHealthKitDataModel.md` for field-level mapping.

## Mobile sync

```mermaid
erDiagram
    users {
        smallint tenant_id PK
        uuid id PK "hub - defined in Core"
    }
    tenants {
        smallint id PK "defined in Core"
    }
    sync_queue {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        text table_name
        uuid record_id
        text operation
        text status
    }
    schema_versions {
        smallint tenant_id PK
        uuid id PK
        uuid user_id FK
        text device_id UK "unique per user"
        integer schema_version
        timestamptz last_sync
    }
    data_corrections {
        uuid id PK "PK is id alone"
        smallint tenant_id FK
        uuid user_id FK
        text record_type
        text corrected_field
        text new_value
    }
    users ||--o{ sync_queue : ""
    users ||--o{ schema_versions : ""
    users ||--o{ data_corrections : ""
    tenants ||--o{ data_corrections : "tenant_id"
```

`schema_versions` (per-device, user-scoped) is distinct from the global `schema_version` marker table in System & telemetry. `data_corrections` is the only user-data table whose PK is `id` alone rather than `(tenant_id, id)`, and one of the three tables with a direct FK to `tenants`. Most user tables also carry `sqlite_id` / `synced_at` columns as the mobile-sync bookkeeping fields; `sync_queue` is the transport queue itself.

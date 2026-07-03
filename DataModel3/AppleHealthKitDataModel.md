# Apple HealthKit Data Model — healthv10 (Home Edition)

**Date: 2026-07-03 03:45 PDT**

**Source of Truth**: `Infrastructure/init/docker-init-home/02-home_schema.sql`
**ERD companion**: `AppleHealthKitERD.md`

---

## Executive Summary

Home Edition ingests Apple HealthKit data through two paths: **live sync** (mobile app → `POST /api/v1/healthkit/sync`) and **bulk import** (Apple Health XML export ZIP → `POST /api/v1/healthkit/upload`). Both paths land data in 13 PostgreSQL tables in the `healthv10` database, in a HealthKit-native, source-faithful shape.

The 13 tables store:

- **Generic health samples** (steps, heart rate, blood glucose, etc.) — `hkit_records`
- **Workouts** (type, duration, energy, distance) — `hkit_workouts`
- **Apple Watch activity rings** (daily move/exercise/stand goals) — `hkit_activity_summaries`
- **Clinical records** from connected health institutions (FHIR R4 JSON) — `hkit_clinical_records` with extracted sub-tables for labs, medications, immunizations, and allergies
- **User characteristics** (DOB, biological sex, blood type) — `hkit_user_profile`
- **Device/app registry** — `hkit_sources`
- **Import job tracking** — `healthkit_import_jobs`
- **Per-device incremental-sync anchors** (`HKAnchoredObjectQuery` tokens) — `hkit_sync_anchors`

The `hkit_*` tables are the **source layer**. External-source data lands here first and is projected into the derived `health_*` tables (e.g. weight into `health_metrics`, blood pressure into `health_blood_pressure_readings`) — source data is never written directly into `health_*`.

**Scoping convention**: every user-owned table carries `tenant_id` (always `1` — the fixed app-level scoping convention that keeps the schema shape uniform) plus `user_id`. Privacy between household members is per-user and enforced in the application: every query binds an explicit `tenant_id = 1 AND user_id = %s` predicate. The one exception is `hkit_record_types`, a global lookup table with no per-user rows.

---

## 1. Architecture

### 1.1 Ingest Flow

```
                 Apple HealthKit Store (on-device)
        Quantity Samples │ Category Samples │ Correlations
        Workouts │ Characteristics │ Clinical Records │ Rings
                              │
          ┌───────────────────┴────────────────────┐
          ▼                                        ▼
   Live sync (mobile app)                 Bulk import (export ZIP)
   POST /api/v1/healthkit/sync            POST /api/v1/healthkit/upload
          │                                        │
          │                               healthkit_import_jobs
          │                               (pending → processing →
          │                                completed | failed)
          │                                        │
          │                               healthkit_worker.py
          │                               (in-process daemon thread)
          │                                        │
          ▼                                        ▼
   healthkit_writer.py                    healthkit_importer.py
   (write_v2_payload)                     (export.xml + FHIR JSON)
          │                                        │
          └───────────────────┬────────────────────┘
                              ▼
             healthv10 (PostgreSQL 18 + pgvector)

             hkit_records ← generic samples (high-volume)
             hkit_workouts ← workout sessions
             hkit_activity_summaries ← daily rings
             hkit_clinical_records ← raw FHIR R4 JSON
               ├→ hkit_lab_observations (extracted)
               ├→ hkit_medications (extracted)
               ├→ hkit_immunizations (extracted)
               └→ hkit_allergies (extracted)
             hkit_user_profile ← characteristics
             hkit_sources ← device/app registry
             hkit_sync_anchors ← per-device anchor tokens
             healthkit_import_jobs ← job tracking
                              │
                              ▼ (projection, selective)
             health_metrics, health_blood_pressure_readings, …
```

### 1.2 Two Ingest Paths

| Path | Endpoint | Trigger | Data Source | Code |
|------|----------|---------|-------------|------|
| **Live sync** | `POST /api/v1/healthkit/sync` (also `/api/v2/...`) | Mobile app background sync | HealthKit queries on-device, normalized JSON payload | `app.py::sync_healthkit` → `healthkit_writer.py::write_v2_payload` |
| **Bulk import** | `POST /api/v1/healthkit/upload` | User uploads the Apple Health export ZIP | `export.xml` + `clinical-records/*.json` | `routes/integrations.py::upload_healthkit` → `healthkit_worker.py` → `healthkit_importer.py` |

Both paths write the same canonical shape into the same tables, so replaying either one is a no-op (every insert carries an `ON CONFLICT` clause matching the target table's natural dedup key). Type identifiers are stored as the raw `HKQuantityTypeIdentifierXxx` / `HKCategoryTypeIdentifierXxx` strings — no server-side enum rewriting.

All background work runs **in-process**: the upload handler starts a daemon thread (`healthkit_worker.queue_healthkit_import`) — there is no message broker and no external worker. Each import job writes its own log file (`/app/logs/import_{job_id}.log`), and job progress is polled via `GET /api/v1/healthkit/jobs/<job_id>` and listed via `GET /api/v1/healthkit/jobs`. Single-field corrections to already-synced samples go through `PUT /api/v1/healthkit/correct`.

### 1.3 Incremental Sync Anchors

The live-sync path persists opaque `HKAnchoredObjectQuery` anchor tokens per `(tenant_id, user_id, device_id, sample_type)` in `hkit_sync_anchors`. The mobile client sends its tokens on every sync; the server UPSERTs them and always returns the full per-device anchor map in the response. A reinstalled client (which has lost local state) can POST an empty payload with just its `device_id` and resume incremental `HKAnchoredObjectQuery` walks from the server-stored cursors, avoiding a full re-scan of the user's HealthKit history. See §3.4.

### 1.4 Timestamp Parsing

`healthkit_importer.py::parse_timestamp` accepts both timestamp dialects that appear in an Apple Health export:

- **HealthKit sample format** — `2024-12-11 12:22:00 -0800` (`%Y-%m-%d %H:%M:%S %z`, with a no-timezone fallback)
- **FHIR ISO 8601** — `2025-06-17T23:14:00Z` (from clinical records: `effectiveDateTime`, `authoredOn`, `occurrenceDateTime`, `receivedDate`)

All timestamps are stored in UTC (`TIMESTAMPTZ`) and localized to the user's `home_timezone` for display.

---

## 2. Table Inventory

| # | Table | Per-User | PK | Purpose |
|---|-------|:---:|----|---------|
| 1 | `hkit_record_types` | No (global lookup) | `id` integer IDENTITY | type_identifier → display_name, category, unit |
| 2 | `hkit_sources` | Yes | `(tenant_id, id)` integer IDENTITY | Devices/apps that recorded data |
| 3 | `hkit_records` | Yes | `(tenant_id, id)` bigint IDENTITY | Generic health samples — the high-volume table |
| 4 | `hkit_activity_summaries` | Yes | `(tenant_id, id)` bigint IDENTITY | Daily Apple Watch activity rings |
| 5 | `hkit_workouts` | Yes | `(tenant_id, id)` bigint IDENTITY | Workout sessions with type, duration, energy |
| 6 | `hkit_user_profile` | Yes | `(tenant_id, id)` integer IDENTITY | Characteristics (DOB, sex, blood type); one row per user |
| 7 | `hkit_clinical_records` | Yes | `(tenant_id, id)` integer IDENTITY | Raw FHIR R4 JSON from connected institutions |
| 8 | `hkit_lab_observations` | Yes | `(tenant_id, id)` integer IDENTITY | Extracted lab values with LOINC codes |
| 9 | `hkit_medications` | Yes | `(tenant_id, id)` integer IDENTITY | Extracted medication records |
| 10 | `hkit_immunizations` | Yes | `(tenant_id, id)` integer IDENTITY | Extracted vaccine records |
| 11 | `hkit_allergies` | Yes | `(tenant_id, id)` integer IDENTITY | Extracted allergy records |
| 12 | `healthkit_import_jobs` | Yes | `(tenant_id, id)` uuid | Background import job tracking |
| 13 | `hkit_sync_anchors` | Yes | `(tenant_id, user_id, device_id, sample_type)` | Per-device `HKAnchoredObjectQuery` anchor tokens |

Every per-user table declares `FOREIGN KEY (tenant_id, user_id) REFERENCES users(tenant_id, id) ON DELETE CASCADE` — deleting a user removes all of their HealthKit data.

---

## 3. Schema Detail — Core Infrastructure

### 3.1 hkit_record_types (Global Lookup)

**Purpose**: Maps Apple's `HKQuantityTypeIdentifier*` / `HKCategoryTypeIdentifier*` strings to display names and units. A household-shared reference table — no `tenant_id`, no `user_id`, no per-user rows.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | integer | PK, GENERATED ALWAYS AS IDENTITY | |
| `type_identifier` | text | NOT NULL, UNIQUE | e.g. `HKQuantityTypeIdentifierStepCount` |
| `display_name` | text | | e.g. `Step Count` |
| `category` | text | | e.g. `quantity`, `category` |
| `unit` | text | | e.g. `count`, `bpm`, `mg/dL` |

**Population**: Rows are created on-demand by `get_or_create_record_type()` (both in `healthkit_importer.py` and `healthkit_writer.py`, the latter with a per-request cache). Not pre-seeded.

### 3.2 hkit_sources (Device/App Registry)

**Purpose**: Tracks which devices and apps produced health data for each user.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `tenant_id` | smallint | PK, NOT NULL, DEFAULT 1 | |
| `id` | integer | PK, GENERATED ALWAYS AS IDENTITY | |
| `user_id` | uuid | NOT NULL, FK→users ON DELETE CASCADE | |
| `source_name` | text | NOT NULL | e.g. "Apple Watch", "MyFitnessPal" |
| `source_bundle_id` | text | | e.g. "com.apple.health" |
| `source_version` | text | | App version string |
| `device_name` | text | | Hardware name |
| `device_model` | text | | Hardware model |
| `created_at` | timestamptz | DEFAULT now() | |

**Unique constraint**: `(tenant_id, user_id, source_bundle_id)` — one entry per app per user.

### 3.3 healthkit_import_jobs (Job Tracking)

**Purpose**: Tracks background import jobs created by `POST /api/v1/healthkit/upload`.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `tenant_id` | smallint | PK, NOT NULL, DEFAULT 1 | |
| `id` | uuid | PK, DEFAULT gen_random_uuid() | |
| `user_id` | uuid | NOT NULL, FK→users ON DELETE CASCADE | |
| `status` | text | DEFAULT 'pending' | `pending` → `processing` → `completed` or `failed` |
| `total_records` | integer | | Total records imported (set on completion) |
| `processed_records` | integer | DEFAULT 0 | Records imported so far |
| `error_message` | text | | Error details if failed |
| `started_at` | timestamptz | | When processing began |
| `completed_at` | timestamptz | | When processing finished |
| `created_at` | timestamptz | DEFAULT now() | |

**Lifecycle**: `healthkit_worker.py::process_healthkit_job` sets `processing` + `started_at`, runs the import, then sets `completed` (with counts) or `failed` (with `error_message`), and finally removes the temporary upload directory.

### 3.4 hkit_sync_anchors (Per-Device Anchor State)

**Purpose**: Stores opaque `HKAnchoredObjectQuery` anchor tokens per `(tenant_id, user_id, device_id, sample_type)`. The server never interprets these tokens — it persists them verbatim and hands them back on subsequent syncs so the mobile client can resume incremental HealthKit walks after a reinstall or logout that wipes local state.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `tenant_id` | smallint | PK, NOT NULL, DEFAULT 1 | |
| `user_id` | uuid | PK, FK→users ON DELETE CASCADE | |
| `device_id` | text | PK | Stable per-install device identifier supplied by the mobile client |
| `sample_type` | text | PK | HealthKit type identifier (e.g. `HKQuantityTypeIdentifierStepCount`) |
| `anchor` | text | NOT NULL | Opaque anchor token, typically base64 of Apple's `HKQueryAnchor` |
| `updated_at` | timestamptz | NOT NULL, DEFAULT now() | Touched on every UPSERT |

**Primary key**: `(tenant_id, user_id, device_id, sample_type)` — one anchor per device per sample type.

**Write path**: `healthkit_writer.py::upsert_sync_anchor()`, called from `write_v2_payload()` while processing the `anchors` map of a v2 sync payload. Uses `INSERT ... ON CONFLICT (tenant_id, user_id, device_id, sample_type) DO UPDATE SET anchor = EXCLUDED.anchor, updated_at = now()` so replays are idempotent.

**Read path**: `healthkit_writer.py::get_sync_anchors()`, called from `_sync_healthkit_v2()` after commit. Returns `{sample_type: anchor}` for the `(user, device_id)` currently syncing. The full map is returned on every v2 sync response, including the reinstall-recovery case where the request body is just `{"payload_version": 2, "device_id": "..."}`.

---

## 4. Schema Detail — Health Samples

### 4.1 hkit_records (The Big Table)

**Purpose**: Stores all HealthKit quantity and category samples. This is the highest-volume table — a user with an Apple Watch generates thousands of heart rate, step, and sleep samples per day, hence the `bigint` key.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `tenant_id` | smallint | PK, NOT NULL, DEFAULT 1 | |
| `id` | bigint | PK, GENERATED ALWAYS AS IDENTITY | bigint for volume |
| `user_id` | uuid | NOT NULL, FK→users ON DELETE CASCADE | |
| `record_type_id` | integer | FK→hkit_record_types(id) | What kind of sample |
| `source_id` | integer | | References `hkit_sources.id` (nullable; maintained by the ingest code — no declared FK, since the parent PK is composite) |
| `value` | numeric | | The measurement value |
| `unit` | text | | Unit string (count, bpm, mg/dL, etc.) |
| `start_date` | timestamptz | | Sample start time |
| `end_date` | timestamptz | | Sample end time |
| `metadata` | jsonb | | Apple's HKMetadata key-value pairs |
| `created_at` | timestamptz | DEFAULT now() | |

**Indexes**:

- `idx_hkit_records_tenant_user_type` — btree `(tenant_id, user_id, record_type_id, start_date DESC)` — primary query path
- `idx_hkit_records_dedup` — UNIQUE `(tenant_id, user_id, record_type_id, source_id, start_date, end_date)` — the same record type from the same source at the same time is a duplicate

**What lives in `metadata`**: Blood glucose meal time, heart rate motion context, body temperature sensor location, whether a reading was user-entered, sync identifiers. See §8 for common keys. No metadata key has a dedicated column — all are queryable via JSONB operators.

### 4.2 hkit_activity_summaries (Apple Watch Rings)

**Purpose**: One row per user per day. Stores the Move, Exercise, and Stand ring values and goals.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `tenant_id` | smallint | PK, NOT NULL, DEFAULT 1 | |
| `id` | bigint | PK, GENERATED ALWAYS AS IDENTITY | |
| `user_id` | uuid | NOT NULL, FK→users ON DELETE CASCADE | |
| `date` | date | NOT NULL | One summary per day |
| `active_energy_burned` | numeric | | kcal (Move ring, Active Energy variant) |
| `active_energy_burned_goal` | numeric | | kcal goal |
| `exercise_time` | integer | | Minutes (Exercise ring) |
| `exercise_time_goal` | integer | | Minutes goal |
| `stand_hours` | integer | | Hours stood (Stand ring) |
| `stand_hours_goal` | integer | | Hours goal |
| `move_time` | integer | | Minutes (Move ring, Move Time variant) |
| `move_time_goal` | integer | | Minutes goal for the Move Time variant |
| `created_at` | timestamptz | DEFAULT now() | |

**Unique constraint**: `(tenant_id, user_id, date)`. Both ingest paths use `ON CONFLICT ... DO UPDATE` — re-importing refreshes goals, which can change retroactively if the user modifies their Apple Watch goals.

**Move ring variants**: Apple Watch supports two Move ring modes (`HKActivitySummary.appleMoveTime` / `appleMoveTimeGoal`, iOS 11+). Walking users see Active Energy (kcal burned); wheelchair users — and anyone who explicitly chose "Move Time" — see minutes of movement instead. A given row has `active_energy_burned` populated **or** `move_time` populated, not both.

### 4.3 hkit_workouts

**Purpose**: Stores workout sessions. The `workout_type` column stores Apple's `HKWorkoutActivityType` enum name as text (e.g. `running`, `swimming`, `HKWorkoutActivityTypeYoga`); the importer stores whatever `workoutActivityType` Apple provides, so all activity types are supported.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `tenant_id` | smallint | PK, NOT NULL, DEFAULT 1 | |
| `id` | bigint | PK, GENERATED ALWAYS AS IDENTITY | |
| `user_id` | uuid | NOT NULL, FK→users ON DELETE CASCADE | |
| `workout_type` | text | NOT NULL | `HKWorkoutActivityType` name |
| `source_id` | integer | | References `hkit_sources.id` (no declared FK) |
| `start_date` | timestamptz | | Workout start |
| `end_date` | timestamptz | | Workout end |
| `duration_seconds` | numeric | | Total elapsed time (the importer converts minute-denominated export durations to seconds) |
| `total_distance` | numeric | | Meters |
| `total_energy_burned` | numeric | | kcal |
| `metadata` | jsonb | | Events, sub-activities, swimming stats, indoor/outdoor flag |
| `created_at` | timestamptz | | DEFAULT now() |

**Index**: `idx_hkit_workouts_tenant_user_date` — btree `(tenant_id, user_id, start_date DESC)`

### 4.4 hkit_user_profile (Characteristics)

**Purpose**: Stores the user characteristics read from HealthKit (the `Me` element of an export). One row per user, UPSERTed on every import.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `tenant_id` | smallint | PK, NOT NULL, DEFAULT 1 | |
| `id` | integer | PK, GENERATED ALWAYS AS IDENTITY | |
| `user_id` | uuid | NOT NULL, FK→users, UNIQUE (tenant_id, user_id) | One per user |
| `date_of_birth` | date | | From HK characteristic |
| `biological_sex` | text | | `female`, `male`, `other`, `notSet` |
| `blood_type` | text | | `aPositive`, `aNegative`, `bPositive`, etc. |
| `fitzpatrick_skin_type` | text | | `I` through `VI` |
| `wheelchair_use` | boolean | | |
| `updated_at` | timestamptz | DEFAULT now() | |

**Relationship to `users`**: The `users` table also has `biological_sex`, `date_of_birth`, and related fields. These are independently maintained — `hkit_user_profile` reflects what HealthKit reports, `users` reflects what the user entered in the app. They may differ.

---

## 5. Schema Detail — Clinical Records (FHIR)

### 5.1 hkit_clinical_records (Raw FHIR Storage)

**Purpose**: Stores the complete FHIR R4 JSON from health institutions connected via Apple Health Records (iOS 12+). The bulk-import path loads each `clinical-records/*.json` file referenced by the export's `ClinicalRecord` elements.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `tenant_id` | smallint | PK, NOT NULL, DEFAULT 1 | |
| `id` | integer | PK, GENERATED ALWAYS AS IDENTITY | |
| `user_id` | uuid | NOT NULL, FK→users ON DELETE CASCADE | |
| `fhir_resource_type` | text | NOT NULL | `AllergyIntolerance`, `Condition`, `Immunization`, `Observation`, `MedicationRequest`, `Procedure` |
| `fhir_identifier` | text | | FHIR resource ID |
| `fhir_source_url` | text | | The institution's FHIR endpoint URL |
| `display_name` | text | | Human-readable name |
| `received_date` | timestamptz | | When HealthKit received it |
| `raw_fhir` | jsonb | | Complete FHIR R4 JSON |
| `created_at` | timestamptz | DEFAULT now() | |

**Indexes**:

- `idx_hkit_clinical_tenant_user` — btree `(tenant_id, user_id)`
- `ux_hkit_clinical_records_fhir_id` — partial UNIQUE `(tenant_id, user_id, fhir_identifier) WHERE fhir_identifier IS NOT NULL` — a re-imported FHIR resource carries the same `fhir_identifier` as the original, so importing the same Apple Health export twice cannot double the row count

**A conflict-handling subtlety**: the importer's INSERT uses `ON CONFLICT ... DO UPDATE SET fhir_resource_type = EXCLUDED.fhir_resource_type` (a deliberate no-op) rather than `DO NOTHING`, so that `RETURNING id` still yields the existing row's id on the conflict path — the sub-table extractions below need that parent id. The conflict target must repeat the index's partial `WHERE fhir_identifier IS NOT NULL` predicate exactly.

### 5.2 Extraction Pipeline

`healthkit_importer.py` parses `raw_fhir` and inserts into specialized sub-tables based on `fhir_resource_type`:

| FHIR Resource Type | Extracted To | Extractor Function |
|--------------------|--------------|--------------------|
| `Observation` | `hkit_lab_observations` | `extract_lab_observation()` |
| `MedicationRequest` | `hkit_medications` | `extract_medication()` |
| `Immunization` | `hkit_immunizations` | `extract_immunization()` |
| `AllergyIntolerance` | `hkit_allergies` | `extract_allergy()` |
| `Condition` | (stored in raw_fhir only) | No extractor |
| `Procedure` | (stored in raw_fhir only) | No extractor |

All four extract tables share the same dedup rule: a **partial UNIQUE index on `(tenant_id, user_id, clinical_record_id) WHERE clinical_record_id IS NOT NULL`**, meaning each parent clinical record contributes at most one extracted row, and each extractor's INSERT uses `ON CONFLICT ... DO NOTHING` against it (the conflict target repeats the partial `WHERE` exactly). If a future extractor learns to emit multiple sub-rows per parent (e.g. multi-component lab panels), the index needs a discriminator column added.

The `clinical_record_id` column references `hkit_clinical_records.id`; because the parent PK is composite `(tenant_id, id)`, the reference is maintained by the extraction code rather than a declared FK.

### 5.3 hkit_lab_observations

**Purpose**: Lab values extracted from FHIR `Observation` resources. LOINC-coded where available (`code.coding[]` with system `http://loinc.org`). Value comes from `valueQuantity` (numeric + unit) or `valueString`; the reference range is flattened to a single text field (`"70-100 mg/dL"`, `">= 3.5"`, `"<= 5.0"`); interpretation comes from the first `interpretation` coding; `effective_date` from `effectiveDateTime`.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `tenant_id` | smallint | PK, NOT NULL, DEFAULT 1 | |
| `id` | integer | PK, GENERATED ALWAYS AS IDENTITY | |
| `user_id` | uuid | NOT NULL, FK→users ON DELETE CASCADE | |
| `clinical_record_id` | integer | | References hkit_clinical_records.id (nullable) |
| `loinc_code` | text | | e.g. `2345-7` (Glucose) |
| `display_name` | text | | e.g. "Glucose [Mass/volume] in Blood" |
| `value_quantity` | numeric | | Numeric result |
| `value_unit` | text | | e.g. `mg/dL`, `mmol/L` |
| `value_string` | text | | For non-numeric results |
| `reference_range` | text | | e.g. `70-100 mg/dL` |
| `interpretation` | text | | e.g. `N` (normal), `H` (high), `L` (low) |
| `effective_date` | timestamptz | | When the test was performed |
| `created_at` | timestamptz | DEFAULT now() | |

**Indexes**: `idx_hkit_labs_tenant_user` — btree `(tenant_id, user_id)`; `ux_hkit_lab_observations_parent` — the partial unique dedup index described in §5.2.

### 5.4 hkit_medications

Extracted from FHIR `MedicationRequest`: name from `medicationReference.display` (falling back to `medicationCodeableConcept.text`), dosage from `dosageInstruction[0].text`, `status` and `authoredOn` verbatim.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `tenant_id` | smallint | PK, NOT NULL, DEFAULT 1 | |
| `id` | integer | PK, GENERATED ALWAYS AS IDENTITY | |
| `user_id` | uuid | NOT NULL, FK→users ON DELETE CASCADE | |
| `clinical_record_id` | integer | | References hkit_clinical_records.id |
| `medication_code` | text | | RxNorm code |
| `medication_name` | text | NOT NULL | e.g. "Metformin 500mg" |
| `dosage` | text | | e.g. "500 mg twice daily" |
| `status` | text | | `active`, `completed`, `stopped` |
| `authored_date` | timestamptz | | When prescribed |
| `created_at` | timestamptz | DEFAULT now() | |

**Dedup**: `ux_hkit_medications_parent` (see §5.2).

### 5.5 hkit_immunizations

Extracted from FHIR `Immunization`: `vaccineCode.text` / first coding, `occurrenceDateTime`, `lotNumber`.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `tenant_id` | smallint | PK, NOT NULL, DEFAULT 1 | |
| `id` | integer | PK, GENERATED ALWAYS AS IDENTITY | |
| `user_id` | uuid | NOT NULL, FK→users ON DELETE CASCADE | |
| `clinical_record_id` | integer | | References hkit_clinical_records.id |
| `vaccine_code` | text | | CVX code |
| `vaccine_name` | text | NOT NULL | e.g. "COVID-19 mRNA" |
| `administered_date` | timestamptz | | |
| `lot_number` | text | | |
| `created_at` | timestamptz | DEFAULT now() | |

**Dedup**: `ux_hkit_immunizations_parent` (see §5.2).

### 5.6 hkit_allergies

Extracted from FHIR `AllergyIntolerance`: allergen from `code.text` / first coding display, reaction from `reaction[0].manifestation[0]`, severity from `criticality`.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `tenant_id` | smallint | PK, NOT NULL, DEFAULT 1 | |
| `id` | integer | PK, GENERATED ALWAYS AS IDENTITY | |
| `user_id` | uuid | NOT NULL, FK→users ON DELETE CASCADE | |
| `clinical_record_id` | integer | | References hkit_clinical_records.id |
| `allergen` | text | NOT NULL | e.g. "Penicillin" |
| `reaction` | text | | e.g. "hives, anaphylaxis" |
| `severity` | text | | FHIR criticality: `low`, `high`, `unable-to-assess` |
| `onset_date` | timestamptz | | |
| `created_at` | timestamptz | DEFAULT now() | |

**Dedup**: `ux_hkit_allergies_parent` (see §5.2). Note this table is HealthKit-only; manually entered allergies live in `health_allergies`.

---

## 6. Deduplication Strategy

### 6.1 Database-Level

| Table | Mechanism |
|-------|-----------|
| `hkit_records` | UNIQUE `idx_hkit_records_dedup` on `(tenant_id, user_id, record_type_id, source_id, start_date, end_date)` + `ON CONFLICT DO NOTHING` |
| `hkit_activity_summaries` | UNIQUE `(tenant_id, user_id, date)` + `ON CONFLICT DO UPDATE` (goal refresh) |
| `hkit_user_profile` | UNIQUE `(tenant_id, user_id)` + `ON CONFLICT DO UPDATE` |
| `hkit_clinical_records` | Partial UNIQUE on `(tenant_id, user_id, fhir_identifier)` + no-op `DO UPDATE` for `RETURNING id` |
| Four FHIR extract tables | Partial UNIQUE on `(tenant_id, user_id, clinical_record_id)` + `ON CONFLICT DO NOTHING` |
| `hkit_sync_anchors` | Composite PK + `ON CONFLICT DO UPDATE` (anchor replacement) |
| `hkit_sources` | UNIQUE `(tenant_id, user_id, source_bundle_id)`; lookups are get-or-create |

The net effect: replaying the same sync payload or re-uploading the same export ZIP is idempotent.

### 6.2 Client-Level

The mobile app filters duplicate samples before sending (keyed on type, dates, value, and source), which reduces bandwidth — but the database indexes above are the actual guarantee.

---

## 7. Relationship to Non-HealthKit Tables

`hkit_*` is the source layer; `health_*` is derived. HealthKit data is projected into `health_*` selectively, and never written there directly by external clients:

| HealthKit Table/Type | Derived Table | Relationship |
|---------------------|---------------|--------------|
| `hkit_records` (weight) | `health_metrics` (metric_type='weight') | **Projected**: the sync path also writes weight into `health_metrics` with `source = 'healthkit'` |
| `hkit_records` (blood pressure) | `health_blood_pressure_readings` | **Projected**: the sync path writes the systolic/diastolic correlation into the dedicated BP table |
| `hkit_records` (dietary*) | `health_food_itemsv2` / `health_food_logv2` | **No bridge**: HealthKit nutrition arrives as individual nutrient samples; the food tables use a catalog + log model. HealthKit's `HKCorrelation.food` grouping is not preserved — "these nutrients came from one banana" cannot be reconstructed. |
| `hkit_allergies` | `health_allergies` | **No bridge**: FHIR-sourced vs. manually entered; both are shown to the user, neither writes into the other |
| `hkit_medications` | `health_inputs` (input_type='medication') | **No bridge**: FHIR-sourced meds vs. user-managed med list |
| `hkit_workouts` | — | HealthKit-only today |

Fuller `hkit_*` → `health_*` projection (beyond weight and blood pressure) is a planned writer responsibility; until it lands, the `hkit_*` layer is the queryable record of everything HealthKit sent.

---

## 8. Apple HealthKit Metadata Keys

HealthKit attaches a `[String: Any]` metadata dictionary to samples. Commonly encountered keys, all stored as JSONB in the `metadata` column of `hkit_records` or `hkit_workouts`:

| Key | Value Type | Relevant To |
|-----|-----------|-------------|
| `HKMetadataKeyWasUserEntered` | Bool | All types |
| `HKMetadataKeyTimeZone` | String | All types |
| `HKMetadataKeyHeartRateMotionContext` | Int (0=notSet, 1=sedentary, 2=active) | Heart rate |
| `HKMetadataKeyBodyTemperatureSensorLocation` | Int (0–11) | Temperature |
| `HKMetadataKeyBloodGlucoseMealTime` | Int (1=preprandial, 2=postprandial) | Blood glucose |
| `HKMetadataKeyFoodType` | String | Dietary samples |
| `HKMetadataKeySyncIdentifier` / `HKMetadataKeySyncVersion` | String / Int | All types |
| `HKMetadataKeyIndoorWorkout` | Bool | Workouts |
| `HKMetadataKeyAverageSpeed` / `HKMetadataKeyElevationAscended` | HKQuantity | Workouts |
| `HKMetadataKeySwimmingLocationType` | Int (1=pool, 2=openWater) | Swimming workouts |
| `HKMetadataKeyVO2MaxTestType` | Int (1–3) | VO2 Max |
| `HKMetadataKeyWasTakenInLab` | Bool | Lab results |

---

## 9. API Surface

The HealthKit endpoints are documented in `APIDocumentation/UserAPI.md` and the OpenAPI contract (`APIDocumentation/openapi.yaml`):

| Endpoint | Purpose |
|----------|---------|
| `POST /api/v1/healthkit/sync` (and `/api/v2/healthkit/sync`) | Live sync — samples, workouts, activity summaries, characteristics, anchors |
| `POST /api/v1/healthkit/upload` | Bulk import — Apple Health export ZIP; returns a job id |
| `GET /api/v1/healthkit/jobs` | List the caller's import jobs |
| `GET /api/v1/healthkit/jobs/<job_id>` | Poll one job's status/progress |
| `PUT /api/v1/healthkit/correct` | Correct a single field on an already-synced sample |

All endpoints resolve the authenticated `user_id` and every query binds it explicitly, per the household trust model.

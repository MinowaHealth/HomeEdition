# Data Sources and Derived Tables

**Date**: 2026-04-10
**Time**: 18:30 PT
**Purpose**: Canonical statement of the source-vs-synthesis rule. Referenced from CLAUDE.md's `Source vs Derived Tables` critical rule; this file is the single source — do not duplicate the body into CLAUDE.md.

---

## Data Sources and Derived Tables

The application ingests health data from multiple independent sources. Each source has its own set of namespaced tables in the database — those tables are the **canonical record of what that source said**, preserved in the source's native shape. They are not merged, they are not normalized across sources, and they are not the "correct" view of the user's health.

The `health_*` tables (`health_metrics`, `health_blood_pressure_readings`, `health_conditions`, `health_allergies`, etc.) are a **derived synthesis** — they represent reality as reconciled from one or more input sources, and are what the UI and MCP tools read from.

### Data Sources and Their Canonical Tables

| Source | Canonical tables | Notes |
|--------|------------------|-------|
| Apple HealthKit (live mobile sync or exported-file import) | `hkit_*` (12 tables) | Source of truth for all HealthKit-origin data, regardless of which path it came in |
| Garmin Connect | `garm_*` (namespaced) | High-volume wearable telemetry, written by the Garmin sync worker |
| Manual web UI entry | Writes directly to `health_*` tables | The user is the source; there is no prior source table |
| Other devices (future) | Will get their own namespaced tables | Do **not** squeeze a new device's data into an existing source's tables |

### The Rule

**Never write data from a specific external source directly into a `health_*` table and consider the job done.** The flow is always:

1. Data lands in the source-specific table first (`hkit_*`, `garm_*`, etc.), preserving the source's native shape, provenance, and full sample set faithfully
2. A projector — either a database trigger or a synchronous post-write in the same transaction — derives the reality-level row in the corresponding `health_*` table, tagged with a `source` column so multiple sources can coexist in the derived table
3. The reverse direction does not happen — manual-entry rows written directly to `health_*` are not pushed back into source-specific tables, and rows reconciled from one source are not re-projected into another source's tables

This is why HealthKit has its own `hkit_records`, why Garmin has its own `garm_hr`, and why `health_metrics` exists at all: `health_metrics` is the shared "what do we believe is true" layer, not the landing zone for any individual source.

### Worked Example: Blood Pressure

- A BP reading from HealthKit lands in `hkit_records` as a correlated pair (systolic + diastolic rows sharing a `metadata.correlation_id`). A projector writes a consolidated row to `health_blood_pressure_readings` tagged `source='healthkit'`.
- A BP reading typed into the web UI writes directly to `health_blood_pressure_readings` tagged `source='manual'` — there is no prior source table.
- A BP reading from a Garmin device (were one to exist) would land in a `garm_*` table first, then project to `health_blood_pressure_readings` tagged `source='garmin'`.

`health_blood_pressure_readings` holds all three rows. The `hkit_*` and `garm_*` tables hold only what their respective sources actually sent.

### Anti-Pattern (Recognize and Reject)

Writing HealthKit data directly into `health_metrics` / `health_blood_pressure_readings` and bypassing `hkit_*` entirely. This loses the HealthKit-native structure (correlations, characteristics, activity summaries, full provenance, metadata), silently drops distinctions the source made (e.g., `resting_heart_rate` vs `heart_rate`), and makes any two ingestion paths into the same source impossible to keep consistent.

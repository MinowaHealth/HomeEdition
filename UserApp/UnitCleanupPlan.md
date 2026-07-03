# Unit Handling — Home Edition Review

**Date:** 2026-06-17 12:10 PDT · Research note, no code changes. (Supersedes the pre-conversion 2026-03-16 draft, which described the enterprise schema.)

## The Problem

Unit handling in Home Edition is inconsistent and largely unvalidated. The same concept — "what unit is this value measured in?" — is stored differently across tables, with no shared lookup and no conversion layer. On a single-household box this is not a tenant-isolation or clinical-safety concern; it is a data-quality concern. The two consumers that suffer are the household member reading their own history in the web UI, and the **MCP/AI assistant** (UserMCP → Claude), which has to interpret raw values like `"dosage_taken": "2"` with no reliable unit attached.

Note up front: this review found that the conversion already resolved the schema's worst unit problems in `health_inputs` (see below). What remains is mostly in vitals, food, and lab tables.

---

## Current State: Where Units Live

### health_inputs (medications/supplements) — mostly resolved

The pre-conversion draft called this "the worst offender" (a dead `default_unit` column, dual JSONB storage, and a schema↔API name mismatch). **Those are fixed.** The current schema has proper, separate columns that the routes read and write directly:

- `default_dosage text` — the amount (the API name matches the column; the old `dosage`/`default_dosage` mismatch is gone).
- `default_unit text` — a real column now, written on INSERT and UPDATE in `routes/health_inputs.py` and `health_inputs_v2.py`. It is no longer stuffed into `custom_fields`.
- `form text` and `route text` both exist as distinct columns.
- `custom_fields jsonb` now holds only `category`, `brand`, and `take_with_food` — not the unit.

One residual nit: `default_dosage` is free text, so a value like `"10 mg"` can still arrive with amount and unit jammed together even though `default_unit` exists to hold the unit separately. There is no numeric `dosage_amount` column, so dose math (e.g. "double the dose") isn't possible without parsing the string.

### health_metrics (vitals, wearables)

`unit text` with **no constraint or validation**. Only `metric_type` is constrained (a CHECK against an allowed list); the unit itself is free text. Different metric types imply different unit systems:

| metric_type | Typical units | Default in code | Problem |
|-------------|--------------|-----------------|---------|
| temperature | F, C | F (hardcoded) | No household-member preference |
| weight | lbs, kg | lbs (hardcoded) | No household-member preference |
| heart_rate | bpm | (implied, not stored) | Unit omitted entirely |
| steps | count | (implied, not stored) | Unit omitted entirely |
| sleep | hours | hours | Garmin syncs may use minutes |
| nutrition | kcal | kcal | Assumed but not validated |

### health_food_itemsv2 (food catalog)

- `serving_unit text` — reasonable for serving definitions.
- Macro columns embed units in their names: `sodium_mg`, `potassium_mg` — fine until someone stores a value in grams.
- `custom_nutrients jsonb` — completely unstructured. `{"vitamin_d": 2000}` could be IU, mcg, or ng/mL. No unit metadata.

### health_food_logv2 (food logging)

- Optional `unit` column (guarded with a `table_has_column()` check).
- `servings` is a count multiplier, not a weight/volume — "I ate 200 g" and "I ate 2 servings" both collapse into `servings`.

### health_blood_work (lab results)

- `unit text` — free text, no validation.
- The same test from different labs may use different unit systems (mg/dL vs mmol/L for glucose). A household still collects results from more than one lab.
- `reference_range text` (e.g. "70-100") is meaningless without knowing the unit system.
- No conversion is possible without a lookup mapping test names to unit families.

### health_input_log (medication logging)

- `dosage_taken text` — stores what was taken (e.g. "2" or "10 mg").
- The unit is derived by joining back to `health_inputs`, so a log row's meaning depends on the current state of its parent input. If a medication's unit changes later, historical logs become ambiguous.

---

## Defensive Column Checks (schema instability)

The code still makes heavy use of `table_has_column()` for unit-related (and other) fields — 44 calls in `routes/logging_routes.py` alone, plus more across `analytics.py`, `embeddings.py`, `search.py`, `vitals.py`, and `utils.py`. Examples:

```python
has_unit = table_has_column(conn, 'health_food_logv2', 'unit')
```

These are a code smell: they mean the code doesn't trust its own schema, and each one is a runtime `information_schema` query on the request path. On a single-box appliance where the schema is fixed at deploy time (`02-home_schema.sql`), most of these guards are unnecessary and could be removed once the schema is treated as authoritative.

---

## Frontend Hardcoding

The web UI (`index.html`) hardcodes unit lists in several places:

- **Medication units**: `mg, g, ml, spray, application, tbd` (still present).
- **Temperature**: defaults to F, dropdown offers F or C.
- **Weight**: defaults to lbs, dropdown offers lbs or kg.
- **Food serving units**: a short hardcoded list (the earlier `1/4 cup` / `1/2 cup` entries are no longer present — the list has drifted, which is itself a reason to source it from one place).

No user preference is stored or respected, and defaults are US-centric.

---

## MCP Layer

UserMCP passes units through as-is from the API — no normalization, no metadata enrichment. When Claude sees `"dosage_taken": "2"` next to `"default_unit": "mg"`, it has to guess whether "2" means "2 mg" or "2 tablets of the default dose." Improving unit fidelity is the single biggest lever on how well the household's AI assistant can answer questions about intake, vitals, and labs.

---

## What's Actually Broken vs. What's Messy

**Actually broken** (data-quality risk):
- `custom_nutrients` JSONB in food items has no unit metadata — values are uninterpretable.
- Lab results from different sources can't be compared (no unit normalization).
- `health_metrics.unit` is unvalidated, so a bad/empty unit can be stored silently.

**Messy but functional** (technical debt):
- Hardcoded US defaults (F, lbs) — works for the current household.
- Defensive column checks — work, but add latency and complexity that a fixed appliance schema doesn't need.
- Combined amount+unit in `default_dosage` — no numeric separation, so no dose math.
- No shared unit-conversion utilities — not needed until trend/aggregation features want them.

---

## Design Questions (decide before planning fixes)

1. **Unit storage model** — single source of truth per table? Options: (a) proper columns with CHECK constraints, (b) a shared `units` lookup table (id, category, abbreviation, full_name, conversion_factor), (c) keep JSONB but with a documented schema.

2. **Separation of amount and unit** — split `default_dosage` into a numeric amount + unit, to enable dose math? Requires migrating existing combined strings.

3. **Household unit preferences** — where to store? The `user_preferences` table now exists (it already carries things like `timezone_reminder_mode`), so a per-member `unit_system` / temperature / weight preference is a natural fit there rather than frontend-only.

4. **Historical data** — when units are fixed, what happens to existing rows with ambiguous or missing units? Backfill, or leave as-is behind a "legacy" flag?

5. **Lab result normalization** — is a `lab_test_types` reference table (test name → expected unit family + conversion factors) worth it for a single household? It is a large effort; for a home appliance it may be over-scoped versus simply storing the unit reliably and letting the MCP/UI present it.

6. **Defensive-check cleanup** — can the `table_has_column()` guards be retired now that the schema is fixed at deploy time? This is a Home-Edition-specific simplification with no enterprise equivalent.

---

## Severity / Impact Assessment

Impact is framed for a single household and its MCP/AI assistant — there are no provider tools or multi-tenant consumers in Home Edition.

| Issue | Data Risk | Household / MCP Impact | Fix Effort |
|-------|-----------|------------------------|------------|
| `custom_nutrients` no units | High (data unusable) | High (nutrition questions to the AI assistant) | High |
| Lab result unit normalization | Medium (mis-comparison) | Medium (the member's own trend reading; AI answers) | Very High |
| `health_metrics` no unit validation | Medium (bad data possible) | Medium (wrong displays) | Medium |
| Combined amount+unit in `default_dosage` | Low | Low (no dose math) | Medium |
| Defensive column checks | Low (performance) | None | Medium |
| No household unit preferences | Low | Medium (non-US members) | Low |

---

## Next Steps

The `health_inputs` cleanup that dominated the original draft is done. A realistic Home-Edition order of attack for what remains:

- **Phase 1** — Add unit validation/defaults to `health_metrics` (temperature, weight), and store a household-member unit preference in `user_preferences`.
- **Phase 2** — Give food `custom_nutrients` a documented unit schema so nutrition values are interpretable by the UI and the MCP.
- **Phase 3** — Retire the unnecessary `table_has_column()` guards now that the appliance schema is authoritative.
- **Phase 4** — Lab-result normalization, only if trend/correlation features or MCP insight quality justify the (large) effort.

Each phase should be planned separately after the design questions above are resolved.

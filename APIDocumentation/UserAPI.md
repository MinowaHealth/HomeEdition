# User API Reference

**Date**: 2026-03-23
**Base URL**: `https://localhost` (pilot) / `https://localhost` (production)
**Auth**: All endpoints require `@require_auth` unless noted. See [Authentication.md](Authentication.md).
**Source**: [`UserApp/webapp/`](../UserApp/webapp/)

> **Auth & account endpoints** (login, logout, signup, password reset, 2FA setup/verify/disable) are documented in [Authentication.md](Authentication.md), not repeated here.

---

## Health Inputs

Medications, supplements, alternatives, and treatments.

**Source**: [`routes/health_inputs.py`](../UserApp/webapp/routes/health_inputs.py)

### GET /api/v1/health-inputs

Returns all health inputs for the authenticated user.

**Sort order**: `sticky` first, then `detected`, then alphabetical by name.

**Response (200):**
```json
[
  {
    "id": "uuid",
    "name": "Lisinopril",
    "input_type": "medication",
    "default_dosage": "10mg",
    "form": "tablet",
    "is_active": true,
    "instructions": "Take with food",
    "custom_fields": {},
    "category": null,
    "default_unit": "mg",
    "brand": "Generic",
    "take_with_food": 1,
    "frequent_status": "sticky",
    "doses_per_day": 2
  }
]
```

### POST /api/v1/health-inputs

Create a new health input.

**Request:**
```json
{
  "name": "Lisinopril",
  "input_type": "medication",
  "default_dosage": "10mg",
  "form": "tablet",
  "is_active": true,
  "notes": "For blood pressure",
  "category": null,
  "default_unit": "mg",
  "brand": "Generic",
  "take_with_food": true,
  "frequent_status": "sticky",
  "doses_per_day": 1
}
```

Required: `name`, `input_type` (medication | supplement | alternative | treatment)

Optional:
- `frequent_status` — `null` (default), `"detected"` (system-set), or `"sticky"` (user-pinned, never auto-demoted)
- `doses_per_day` — `null` (unspecified, frontend treats as 1), `-1` (PRN/as-needed), `1`–`4` (fixed daily doses). Invalid values return 400.
- `default_unit` — canonical vocabulary: `ug`, `mg`, `g`, `ml`, `oz` (mass/volume), `iu` (potency), `spray`, `patch`, `application`, `drop`, `puff`, `tablet`, `capsule`, `unit` (dose forms). Common aliases are normalized server-side (`mcg`/`µg` → `ug`, `IU` → `iu`, `mL` → `ml`, case-folding, plurals like `tablets` → `tablet`). Anything else returns 400 with code `INVALID_UNIT`. Empty string and `null` both store null. The same rules apply on PUT when the field is present.

**Response (201):**
```json
{
  "id": "uuid",
  "message": "Health input created"
}
```

### PUT /api/v1/health-inputs/:id

Update a health input. Same body as POST (all fields optional).

Set `frequent_status` to `"sticky"` to pin an input, or `null` to unpin.
Set `doses_per_day` to change dosing frequency, or `null` to clear.

**Response (200):** `{"message": "Health input updated"}`
**Response (404):** `{"error": "Health input not found"}`

### DELETE /api/v1/health-inputs/:id

**Response (200):** `{"message": "Health input deleted"}`

---

## Stacks

Time-based bundles of health inputs (e.g., "Morning Meds").

### GET /api/v1/stacks

**Response (200):**
```json
[
  {
    "id": "uuid",
    "name": "Morning Meds",
    "timeframe_name": "Wake Up",
    "is_active": true,
    "inputs": [
      {
        "input_id": "uuid",
        "input_name": "Lisinopril",
        "dosage_override": null
      }
    ]
  }
]
```

### POST /api/v1/stacks

**Request:**
```json
{
  "name": "Morning Meds",
  "timeframe_id": "uuid-or-null",
  "is_active": true,
  "inputs": [
    {"input_id": "uuid", "dosage_override": "20mg"}
  ]
}
```

Required: `name`

**Response (201):** `{"id": "uuid", "message": "Stack created"}`

### PUT /api/v1/stacks/:id

Same body as POST. Replaces the input list entirely.

**Response (200):** `{"message": "Stack updated"}`

### DELETE /api/v1/stacks/:id

**Response (200):** `{"message": "Stack deleted"}`

---

## Timeframes

User-defined times of day (wake, breakfast, bedtime, etc.).

### GET /api/v1/timeframes

**Response (200):**
```json
[
  {
    "id": "uuid",
    "name": "Wake Up",
    "time_of_day": "07:00:00",
    "sort_order": 1,
    "is_active": true
  }
]
```

### POST /api/v1/timeframes

**Request:**
```json
{
  "name": "Wake Up",
  "time_of_day": "07:00:00",
  "sort_order": 1,
  "is_active": true
}
```

Required: `name`

**Response (201):** `{"id": "uuid", "message": "Timeframe created"}`

### PUT /api/v1/timeframes/:id

**Response (200):** `{"message": "Timeframe updated"}`

### DELETE /api/v1/timeframes/:id

**Response (200):** `{"message": "Timeframe deleted"}`

---

## Food Items

Food database with nutritional info.

**Source**: [`routes/food.py`](../UserApp/webapp/routes/food.py)

### GET /api/v1/food-items

**Response (200):**
```json
[
  {
    "id": "uuid",
    "name": "Banana",
    "brand": null,
    "barcode": null,
    "calories": 105,
    "protein_g": 1.3,
    "carbs_g": 27,
    "fat_g": 0.4,
    "is_favorite": false,
    "created_at": "2026-02-24T10:00:00",
    "updated_at": "2026-02-24T10:00:00"
  }
]
```

### POST /api/v1/food-items

**Request:**
```json
{
  "name": "Banana",
  "brand": null,
  "barcode": null,
  "calories": 105,
  "protein_g": 1.3,
  "carbs_g": 27,
  "fat_g": 0.4,
  "is_favorite": false
}
```

Required: `name`

**Response (201):** `{"id": "uuid", "message": "Food item created"}`

### PUT /api/v1/food-items/:id

**Response (200):** `{"message": "Food item updated"}`

### DELETE /api/v1/food-items/:id

**Response (200):** `{"message": "Food item deleted"}`

---

## Meals

Pre-defined meal templates (collections of food items).

### GET /api/v1/meals

**Response (200):**
```json
[
  {
    "id": "uuid",
    "name": "Breakfast Bowl",
    "description": "Oats with fruit",
    "is_favorite": true,
    "items": [
      {
        "food_item_id": "uuid",
        "food_name": "Banana",
        "servings": 1.0
      }
    ]
  }
]
```

### POST /api/v1/meals

**Request:**
```json
{
  "name": "Breakfast Bowl",
  "description": "Oats with fruit",
  "is_favorite": true,
  "items": [
    {"food_item_id": "uuid", "servings": 1.0}
  ]
}
```

Required: `name`

**Response (201):** `{"id": "uuid", "message": "Meal created"}`

### PUT /api/v1/meals/:id

**Response (200):** `{"message": "Meal updated"}`

### DELETE /api/v1/meals/:id

**Response (200):** `{"message": "Meal deleted"}`

---

## Activity Logging

Log consumption of health inputs, food, stacks, and meals.

**Source**: [`routes/logging_routes.py`](../UserApp/webapp/routes/logging_routes.py)

### POST /api/v1/log-stack

Log all inputs in a stack at once.

**Request:**
```json
{
  "stack_id": "uuid",
  "timestamp": "2026-02-24T07:30:00"
}
```

**Response (201):**
```json
{
  "message": "Stack logged successfully",
  "stack_id": "uuid",
  "inputs_found": 3,
  "inputs_logged": 3
}
```

### POST /api/v1/log-health-input

Log a single health input intake. Supports both catalog items and freeform text.

**Request (catalog item):**
```json
{
  "timestamp": "2026-02-24T07:30:00",
  "input_id": "uuid",
  "dosage": "10mg"
}
```

**Request (freeform):**
```json
{
  "timestamp": "2026-02-24T07:30:00",
  "free_text": "Vitamin D",
  "free_dosage": "5000 IU"
}
```

Required: `timestamp`, plus either `input_id` or `free_text`

**Response (201):** `{"id": "uuid", "message": "Health input logged successfully"}`

### POST /api/v1/log-meal

Log all food items in a meal at once.

**Request:**
```json
{
  "meal_id": "uuid",
  "timestamp": "2026-02-24T12:00:00"
}
```

**Response (201):**
```json
{
  "message": "Meal logged successfully",
  "meal_id": "uuid",
  "items_found": 2,
  "items_logged": 2
}
```

### POST /api/v1/log-food-item

Log a single food item. Supports catalog items and freeform text.

**Request (catalog item):**
```json
{
  "timestamp": "2026-02-24T12:00:00",
  "food_item_id": "uuid",
  "servings": 1.5
}
```

**Request (freeform):**
```json
{
  "timestamp": "2026-02-24T12:00:00",
  "free_text": "Large coffee with cream",
  "calories": 120
}
```

**Response (201):** `{"id": "uuid", "message": "Food item logged successfully"}`

### GET /api/v1/health-input-log

Recent health input log entries.

**Query params:**
- `start_date` — Start date (`YYYY-MM-DD`, optional)
- `end_date` — End date (`YYYY-MM-DD`, optional)
- `input_id` — UUID of a specific health input (optional, filters to that item only)
- `limit` — Max rows returned (default 100, max 1000)

**Response (200):**
```json
[
  {
    "id": "uuid",
    "timestamp": "2026-02-24T07:30:00",
    "dosage_taken": "10mg",
    "free_text": null,
    "free_dosage": null,
    "input_name": "Lisinopril",
    "default_unit": "mg",
    "stack_name": "Morning Meds",
    "is_freeform": false
  }
]
```

### PUT /api/v1/health-input-log/:id

**Request:**
```json
{
  "timestamp": "2026-02-24T08:00:00",
  "dosage": "20mg",
  "input_id": "uuid",
  "free_text": null,
  "free_dosage": null
}
```

**Response (200):** `{"message": "Log entry updated"}`

### DELETE /api/v1/health-input-log/:id

**Response (200):** `{"message": "Log entry deleted"}`

### GET /api/v1/food-log

Recent food log entries.

**Response (200):**
```json
[
  {
    "id": "uuid",
    "timestamp": "2026-02-24T12:00:00",
    "servings": 1.5,
    "food_item_id": "uuid",
    "food_name": "Banana",
    "is_freeform": false
  }
]
```

### PUT /api/v1/food-log/:id

**Response (200):** `{"message": "Food log entry updated"}`

### DELETE /api/v1/food-log/:id

**Response (200):** `{"message": "Food log entry deleted"}`

### GET /api/v1/all-logs

Combined view of all recent log entries across all types.

**Response (200):**
```json
[
  {
    "id": "uuid",
    "timestamp": "2026-02-24T07:30:00",
    "type": "health_input",
    "description": "Lisinopril 10mg",
    "stack": "Morning Meds",
    "source": null,
    "input_name": "Lisinopril",
    "dosage_taken": "10mg",
    "is_freeform": false
  },
  {
    "id": "uuid",
    "timestamp": "2026-02-24T06:00:00",
    "type": "heart_rate",
    "description": "72 bpm",
    "source": "garmin"
  }
]
```

The `type` field distinguishes: `health_input`, `food`, `blood_pressure`, `temperature`, `weight`, `steps`, `heart_rate`, `sleep`, `nutrition`, `medication`.

---

## Log Promotions

AI/fuzzy-match suggestions linking freeform log entries to catalog items.

### GET /api/v1/log-promotions

**Query params:** `status` (optional: pending | accepted | dismissed | auto_linked)

**Response (200):**
```json
[
  {
    "id": "uuid",
    "source_table": "health_input_log",
    "source_log_id": "uuid",
    "suggested_catalog_table": "health_inputs",
    "suggested_catalog_id": "uuid",
    "free_text_original": "Vitamin D",
    "match_confidence": 0.92,
    "match_method": "fuzzy",
    "status": "pending",
    "resolved_at": null,
    "created_at": "2026-02-24T10:00:00"
  }
]
```

### POST /api/v1/log-promotions

**Request:**
```json
{
  "source_table": "health_input_log",
  "source_log_id": "uuid",
  "suggested_catalog_table": "health_inputs",
  "suggested_catalog_id": "uuid",
  "free_text_original": "Vitamin D",
  "match_confidence": 0.92,
  "match_method": "fuzzy"
}
```

**Response (201):** `{"id": "uuid", "message": "Promotion suggestion created"}`

### PUT /api/v1/log-promotions/:id

Accept or dismiss a promotion. On `accepted`, the source log entry is backfilled with the catalog FK and `promoted_at` timestamp.

**Request:**
```json
{
  "status": "accepted"
}
```

**Response (200):** `{"message": "Promotion accepted"}`

### DELETE /api/v1/log-promotions/:id

**Response (200):** `{"message": "Promotion deleted"}`

---

## Vitals

Blood pressure, temperature, weight, and health metrics.

**Source**: [`routes/vitals.py`](../UserApp/webapp/routes/vitals.py)

### GET /api/v1/blood-pressure

**Response (200):**
```json
[
  {
    "id": "uuid",
    "systolic": 120,
    "diastolic": 80,
    "pulse": 72,
    "timestamp": "2026-02-24T08:00:00"
  }
]
```

### POST /api/v1/blood-pressure

**Request:**
```json
{
  "timestamp": "2026-02-24T08:00:00",
  "systolic": 120,
  "diastolic": 80,
  "heart_rate": 72
}
```

Required: `timestamp`, `systolic`, `diastolic`

**Response (201):** `{"message": "Blood pressure logged successfully"}`

### DELETE /api/v1/blood-pressure/:id

**Response (200):** `{"message": "Blood pressure reading deleted"}`

### GET /api/v1/temperature

**Response (200):**
```json
[
  {
    "id": "uuid",
    "temperature": 98.6,
    "unit": "F",
    "timestamp": "2026-02-24T08:00:00"
  }
]
```

### POST /api/v1/temperature

**Request:**
```json
{
  "timestamp": "2026-02-24T08:00:00",
  "temperature": 98.6,
  "unit": "F"
}
```

Required: `timestamp`, `temperature`. Unit defaults to `"F"`.

**Response (201):** `{"message": "Temperature logged successfully"}`

### GET /api/v1/weight

**Response (200):**
```json
[
  {
    "id": "uuid",
    "weight": 165.5,
    "unit": "lbs",
    "timestamp": "2026-02-24T08:00:00"
  }
]
```

### POST /api/v1/weight

**Request:**
```json
{
  "timestamp": "2026-02-24T08:00:00",
  "weight": 165.5,
  "unit": "lbs"
}
```

Required: `timestamp`, `weight`. Unit defaults to `"lbs"`.

**Response (201):** `{"message": "Weight logged successfully"}`

### DELETE /api/v1/weight/:id

Delete a specific weight reading.

**Response (200):** `{"message": "Weight reading deleted"}`
**Response (404):** `{"error": "Weight reading not found"}`

### DELETE /api/v1/health-metrics/:id

Generic delete for any health metric (temperature, weight, etc.).

**Response (200):** `{"message": "Health metric deleted"}`

---

## Clinical History

Conditions, allergies, lab results, family history, social history, and vaccinations.

**Source**: [`routes/clinical_history.py`](../UserApp/webapp/routes/clinical_history.py)

### Health Conditions

#### GET /api/v1/conditions

Returns all health conditions for the authenticated user.

**Response (200):**
```json
[
  {
    "id": "uuid",
    "name": "Essential hypertension",
    "icd10_code": "I10",
    "diagnosed_date": "2019-07-15",
    "status": "managed",
    "severity": "mild",
    "treating_doctor": "Dr. Torella",
    "notes": "Controlled on Lisinopril",
    "custom_fields": null,
    "created_at": "2026-03-03T12:00:00+00:00",
    "updated_at": "2026-03-03T12:00:00+00:00"
  }
]
```

#### GET /api/v1/conditions/:id

**Response (200):** Single condition object. **Response (404):** `{"error": "Condition not found"}`

#### POST /api/v1/conditions

**Request:**
```json
{
  "name": "Essential hypertension",
  "icd10_code": "I10",
  "diagnosed_date": "2019-07-15",
  "status": "managed",
  "severity": "mild",
  "treating_doctor": "Dr. Torella",
  "notes": "Controlled on Lisinopril"
}
```

Required: `name`. Status constraint: active | managed | resolved | monitoring.

**Response (201):** `{"id": "uuid", "message": "Condition created"}`

#### PUT /api/v1/conditions/:id

Same body as POST. **Response (200):** `{"message": "Condition updated"}`

#### DELETE /api/v1/conditions/:id

**Response (200):** `{"message": "Condition deleted"}`

---

### Health Allergies

#### GET /api/v1/allergies

**Response (200):**
```json
[
  {
    "id": "uuid",
    "allergen": "Penicillin",
    "allergy_type": "medication",
    "reaction": "Maculopapular rash",
    "severity": "moderate",
    "onset_date": "2015-03-01",
    "status": "active",
    "notes": "Use alternative antibiotics",
    "source": "manual",
    "custom_fields": null
  }
]
```

#### GET /api/v1/allergies/:id

**Response (200):** Single allergy object. **Response (404):** `{"error": "Allergy not found"}`

#### POST /api/v1/allergies

**Request:**
```json
{
  "allergen": "Penicillin",
  "allergy_type": "medication",
  "reaction": "Maculopapular rash",
  "severity": "moderate",
  "onset_date": "2015-03-01",
  "status": "active",
  "notes": "Use alternative antibiotics",
  "source": "manual"
}
```

Required: `allergen`. `allergy_type`: medication | food | environmental | insect | other. `severity`: mild | moderate | severe | life-threatening.

**Response (201):** `{"id": "uuid", "message": "Allergy created"}`

#### PUT /api/v1/allergies/:id

Same body as POST. **Response (200):** `{"message": "Allergy updated"}`

#### DELETE /api/v1/allergies/:id

**Response (200):** `{"message": "Allergy deleted"}`

---

### Blood Work (Lab Results)

#### GET /api/v1/blood-work

**Response (200):**
```json
[
  {
    "id": "uuid",
    "test_date": "2026-01-17",
    "test_name": "LDL Cholesterol",
    "value": 118.0,
    "unit": "mg/dL",
    "reference_range": "0-100",
    "is_abnormal": true,
    "lab_name": "Arkham Regional Laboratory",
    "loinc_code": "2089-1",
    "panel_name": "Lipid Panel",
    "notes": "Ordered by Dr. Torella"
  }
]
```

#### GET /api/v1/blood-work/:id

**Response (200):** Single result object. **Response (404):** `{"error": "Blood work result not found"}`

#### POST /api/v1/blood-work

**Request:**
```json
{
  "test_date": "2026-01-17",
  "test_name": "LDL Cholesterol",
  "value": 118.0,
  "unit": "mg/dL",
  "reference_range": "0-100",
  "is_abnormal": true,
  "lab_name": "Arkham Regional Laboratory",
  "loinc_code": "2089-1",
  "panel_name": "Lipid Panel",
  "notes": "Ordered by Dr. Torella"
}
```

Required: `test_name`, `test_date`.

**Response (201):** `{"id": "uuid", "message": "Blood work result created"}`

#### PUT /api/v1/blood-work/:id

Same body as POST. **Response (200):** `{"message": "Blood work result updated"}`

#### DELETE /api/v1/blood-work/:id

**Response (200):** `{"message": "Blood work result deleted"}`

---

### Family History

#### GET /api/v1/family-history

**Response (200):**
```json
[
  {
    "id": "uuid",
    "relationship": "father",
    "relative_name": null,
    "relative_age": null,
    "vital_status": "deceased",
    "cause_of_death": null,
    "condition_name": "Ischemic heart disease",
    "icd10_code": "I25.10",
    "age_at_onset": 58,
    "notes": "Fatal MI at age 62"
  }
]
```

#### GET /api/v1/family-history/:id

**Response (200):** Single entry object. **Response (404):** `{"error": "Family history entry not found"}`

#### POST /api/v1/family-history

**Request:**
```json
{
  "relationship": "father",
  "condition_name": "Ischemic heart disease",
  "icd10_code": "I25.10",
  "age_at_onset": 58,
  "vital_status": "deceased",
  "notes": "Fatal MI at age 62"
}
```

Required: `relationship`.

**Response (201):** `{"id": "uuid", "message": "Family history entry created"}`

#### PUT /api/v1/family-history/:id

Same body as POST. **Response (200):** `{"message": "Family history entry updated"}`

#### DELETE /api/v1/family-history/:id

**Response (200):** `{"message": "Family history entry deleted"}`

---

### Social History

#### GET /api/v1/social-history

**Response (200):**
```json
[
  {
    "id": "uuid",
    "category": "tobacco_use",
    "status": "never",
    "detail": "Non-smoker",
    "quantity": null,
    "start_date": null,
    "end_date": null,
    "notes": null
  }
]
```

#### GET /api/v1/social-history/:id

**Response (200):** Single entry object. **Response (404):** `{"error": "Social history entry not found"}`

#### POST /api/v1/social-history

**Request:**
```json
{
  "category": "tobacco_use",
  "status": "never",
  "detail": "Non-smoker",
  "quantity": null
}
```

Required: `category`. Common categories: tobacco_use, alcohol_use, drug_use, employment, education, marital_status, living_situation, exercise.

**Response (201):** `{"id": "uuid", "message": "Social history entry created"}`

#### PUT /api/v1/social-history/:id

Same body as POST. **Response (200):** `{"message": "Social history entry updated"}`

#### DELETE /api/v1/social-history/:id

**Response (200):** `{"message": "Social history entry deleted"}`

---

### Vaccinations

#### GET /api/v1/vaccinations

**Response (200):**
```json
[
  {
    "id": "uuid",
    "vaccine_name": "Influenza, seasonal, injectable, preservative free",
    "administered_date": "2025-09-24",
    "lot_number": null,
    "site": "left deltoid",
    "administered_by": "Dr. Torella",
    "location": null,
    "next_dose_due": null,
    "reaction_notes": null
  }
]
```

#### GET /api/v1/vaccinations/:id

**Response (200):** Single record object. **Response (404):** `{"error": "Vaccination record not found"}`

#### POST /api/v1/vaccinations

**Request:**
```json
{
  "vaccine_name": "Influenza, seasonal, injectable, preservative free",
  "administered_date": "2025-09-24",
  "site": "left deltoid",
  "administered_by": "Dr. Torella"
}
```

Required: `vaccine_name`.

**Response (201):** `{"id": "uuid", "message": "Vaccination record created"}`

#### PUT /api/v1/vaccinations/:id

Same body as POST. **Response (200):** `{"message": "Vaccination record updated"}`

#### DELETE /api/v1/vaccinations/:id

**Response (200):** `{"message": "Vaccination record deleted"}`

---

## Observations

Free-text health notes and observations.

### GET /api/v1/observations

**Response (200):**
```json
[
  {
    "id": "uuid",
    "observation": "Mild headache after lunch",
    "timestamp": "2026-02-24T14:30:00",
    "source_type": "manual",
    "category": "symptom"
  }
]
```

### POST /api/v1/observations

**Request:**
```json
{
  "observation": "Mild headache after lunch",
  "timestamp": "2026-02-24T14:30:00"
}
```

Required: `observation`

**Response (201):** `{"id": "uuid", "message": "Observation created"}`

### PUT /api/v1/observations/:id

Full replacement — updates observation text and timestamp, re-embeds the text for semantic search.

**Request:**
```json
{
  "observation": "Updated observation text",
  "timestamp": "2026-02-24T15:00:00"
}
```

**Response (200):** `{"message": "Observation updated"}`

### PATCH /api/v1/observations/:id

Partial metadata update — sets `mental_health_flag` without re-embedding. Use this instead of PUT when you only need to flag/unflag an observation, especially in bulk operations where re-embedding unchanged text would waste tokens.

**Request:**
```json
{
  "mental_health_flag": true
}
```

**Response (200):** `{"message": "Observation updated"}`

### DELETE /api/v1/observations/:id

**Response (200):** `{"message": "Observation deleted"}`

---

## Analytics

Heatmaps, queries, and diagnostics.

**Source**: [`routes/analytics.py`](../UserApp/webapp/routes/analytics.py)

### GET /api/v1/your-week

7-day heatmap of steps, sleep, stress, and heart rate (from Garmin data).

**Response (200):**
```json
{
  "today": "2026-02-24",
  "days": [
    {
      "date": "2026-02-24",
      "day_name": "Mon",
      "steps": 8500,
      "sleep_hours": {"22": 30.0, "23": 60.0, "0": 60.0},
      "stress_hours": {"8": 42.5, "9": 38.0},
      "hr_hours": {"8": 72.0, "9": 68.5}
    }
  ]
}
```

Hours are 0-23 keys with minute/average values.

### GET /api/v1/sleep-heatmap

28-day sleep heatmap organized by week.

**Response (200):**
```json
{
  "weeks": [
    {
      "label": "This Week",
      "data": [
        {
          "date": "2026-02-24",
          "day_name": "Mon",
          "hours": {"22": 30.0, "23": 60.0, "0": 60.0}
        }
      ]
    }
  ],
  "today": "2026-02-24"
}
```

### GET /api/v1/stress-heatmap

28-day stress heatmap. Same structure as sleep-heatmap.

### GET /api/v1/lab-results

Latest lab results from HealthKit clinical records.

**Response (200):**
```json
{
  "results": [
    {
      "id": "uuid",
      "name": "Glucose",
      "loinc_code": "2345-7",
      "value": 95.5,
      "unit": "mg/dL",
      "reference_range": "70-100",
      "interpretation": "normal",
      "date": "2026-02-20T10:00:00"
    }
  ],
  "count": 12
}
```

### POST /api/v1/health-query

Query health data by type and date range. Used by MCP and analytics dashboards.

**Request:**
```json
{
  "kind": "steps",
  "start": "2026-02-17T00:00:00",
  "end": "2026-02-24T23:59:59"
}
```

`kind` values: `steps`, `heart_rate`, `sleep`, `weight`, `temperature`, `blood_oxygen`, `respiratory_rate`, `blood_pressure`, `food`, `blood_glucose`, `active_energy`, `resting_energy`, `exercise_time`, `stand_hours`, `flights_climbed`, `distance`, `stress`

**Response (200):** Array of records (format varies by kind):
```json
[
  {
    "kind": "steps",
    "start_time": "2026-02-24T00:00:00",
    "end_time": "2026-02-24T23:59:59",
    "value": 8500,
    "unit": "count",
    "source": "garmin"
  }
]
```

For `blood_pressure`, adds `systolic`, `diastolic`, `heart_rate` fields.
For `food`, adds `food_name`, `calories`, `protein_g`, `carbs_g`, `fat_g`, `is_freeform`, etc.

### GET /api/v1/diagnostics/table-counts

Record counts per table (diagnostic/admin use).

**Response (200):**
```json
{
  "tables": [
    {"table": "health_metrics", "count": 4200000, "error": null},
    {"table": "garm_stress", "count": 1200000, "error": null}
  ],
  "total_tables": 64,
  "database": "healthv10"
}
```

---

## Garmin Integration

**Source**: [`routes/integrations.py`](../UserApp/webapp/routes/integrations.py)

### POST /api/v1/garmin/connect

Authenticate with Garmin Connect.

**Request:**
```json
{
  "email": "user@garmin.com",
  "password": "garmin-password"
}
```

**Response (200):**
```json
{
  "success": true,
  "message": "Connected to Garmin",
  "email": "user@garmin.com"
}
```

### GET /api/v1/garmin/status

**Response (200):**
```json
{
  "connected": true,
  "email": "user@garmin.com",
  "last_sync": "2026-02-24T06:00:00",
  "created_at": "2026-01-15T10:00:00"
}
```

### POST /api/v1/garmin/disconnect

**Response (200):** `{"success": true, "message": "Disconnected from Garmin"}`

### POST /api/v1/garmin/sync

Trigger background sync job. Defaults to last 7 days if no dates provided.

**Request:**
```json
{
  "from_date": "2026-02-17",
  "to_date": "2026-02-24"
}
```

**Response (202):**
```json
{
  "job_id": "uuid",
  "status": "pending",
  "sync_from": "2026-02-17",
  "sync_to": "2026-02-24",
  "message": "Garmin sync job queued"
}
```

### GET /api/v1/garmin/jobs

List sync jobs.

**Response (200):**
```json
{
  "jobs": [
    {
      "job_id": "uuid",
      "job_type": "full_sync",
      "status": "completed",
      "start_date": "2026-02-17",
      "end_date": "2026-02-24",
      "progress": {"days_processed": 7, "days_total": 7},
      "completed_at": "2026-02-24T06:05:00",
      "created_at": "2026-02-24T06:00:00"
    }
  ]
}
```

### GET /api/v1/garmin/jobs/:id

**Response (200):** Single job object (same shape as above).

---

## HealthKit Integration

### POST /api/v1/healthkit/sync

Ingest HealthKit samples directly from mobile app.

**Request:**
```json
{
  "samples": [
    {
      "type": "heart_rate",
      "start_time": "2026-02-24T10:30:00",
      "end_time": "2026-02-24T10:30:00",
      "value": 72,
      "unit": "bpm",
      "source": "Apple Watch"
    },
    {
      "type": "blood_pressure",
      "start_time": "2026-02-24T08:00:00",
      "systolic": 120,
      "diastolic": 80,
      "heart_rate": 72
    }
  ]
}
```

**Response (200):**
```json
{
  "inserted": 42,
  "inserted_bp": 2,
  "skipped": 1,
  "received": 45
}
```

### POST /api/v1/healthkit/upload

Upload a HealthKit export ZIP file. Processed asynchronously.

**Content-Type**: `multipart/form-data`
**Field**: `file` (ZIP)

**Response (202):**
```json
{
  "job_id": "uuid",
  "status": "pending",
  "message": "HealthKit import queued for processing",
  "estimated_time": "2-5 minutes"
}
```

### GET /api/v1/healthkit/jobs

**Response (200):**
```json
{
  "jobs": [
    {
      "job_id": "uuid",
      "status": "completed",
      "total_records": 15000,
      "processed_records": 15000,
      "completed_at": "2026-02-24T06:10:00",
      "created_at": "2026-02-24T06:05:00"
    }
  ]
}
```

### GET /api/v1/healthkit/jobs/:id

**Response (200):** Single job object.

### PUT /api/v1/healthkit/correct

Correct a synced health record (e.g., unnamed exercise or food from a third-party app). Logs the correction to `data_corrections` for audit and emits a local `data_corrected` analytics event.

**Allowed fields**: `activityType` (on `health_metrics`), `food_name` (on `health_food_logv2`).

**Request:**
```json
{
  "sample_id": "uuid-of-health-record",
  "field": "activityType",
  "new_value": "Running"
}
```

**Response (200):**
```json
{"ok": true}
```

**Error responses:**
- `400` — Missing `sample_id`, `field`, or `new_value`, or field not in allowlist
- `404` — No record found matching `sample_id` for current user

**Audit trail:** Each correction inserts a row in `data_corrections` with the original value, new value, record type, and timestamp. See [`DataModel3/HomeDatabaseERD.md`](../DataModel3/HomeDatabaseERD.md) for schema.

---

## Embedding Sync

Mobile devices that support on-device embedding send pre-computed 768-dimensional vectors alongside content. Devices that cannot embed locally (late-2010s hardware, model not yet downloaded) send text only — the server generates embeddings via cage/vllm. See [EmbeddingDesign.md](../DataModel3/EmbeddingDesign.md) for full architecture.

### POST /api/v1/sync-embeddings

Upload pre-computed embeddings from the mobile device. Called during normal sync cycles.

**Request:**
```json
{
  "device_capabilities": {
    "can_embed": true,
    "embed_model": "nomic-embed-text-v1.5",
    "embed_dimensions": 768,
    "embed_model_version": "1.5.0-onnx-int4"
  },
  "embeddings": [
    {
      "table": "health_observations",
      "record_id": "uuid",
      "content": "Felt dizzy standing up after lunch",
      "embedding": [0.0123, -0.0456, ...],
      "text_hash": "sha256-of-source-text"
    },
    {
      "table": "health_observations",
      "record_id": "uuid",
      "content": "Feeling groggy after switching to generic Synthroid",
      "embedding": null
    }
  ]
}
```

- When `embedding` is present: server stores both the text and the pre-computed vector (skips server-side embedding)
- When `embedding` is null: server generates the embedding from the `content` field via cage/vllm
- `text_hash` allows the server to detect stale embeddings when content is edited
- `table` must be one of the embedding-enabled tables (see EmbeddingDesign.md Section 3)

Required: `device_capabilities`, `embeddings` (array, max 100 per request)

**Response (200):**
```json
{
  "processed": 8,
  "embedded_server_side": 2,
  "embedded_client_side": 6,
  "errors": []
}
```

**Response (400):**
```json
{
  "error": "Unknown table: foo_bar"
}
```

### POST /api/v1/semantic-search

Search across embedding-enabled tables by meaning. The query text is embedded server-side (or the client can provide a pre-computed query vector).

**Request:**
```json
{
  "query": "blood pressure medication side effects",
  "tables": ["health_observations", "care_messages"],
  "limit": 5,
  "min_similarity": 0.7,
  "date_after": "2025-11-01T00:00:00"
}
```

Alternatively, provide a pre-computed query vector:
```json
{
  "query_embedding": [0.0123, -0.0456, ...],
  "tables": ["health_observations"],
  "limit": 5
}
```

- `tables`: optional filter — defaults to all Tier 1 tables (observations, care messages)
- `limit`: max results per table (default 5, max 20)
- `min_similarity`: cosine similarity threshold (default 0.7, range 0.0–1.0)
- `date_after`: optional recency filter

**Response (200):**
```json
{
  "results": [
    {
      "table": "health_observations",
      "id": "uuid",
      "content": "Feeling dizzy after taking lisinopril",
      "similarity": 0.89,
      "timestamp": "2026-02-20T14:30:00"
    },
    {
      "table": "care_messages",
      "id": "uuid",
      "content": "Let's discuss switching your BP medication",
      "similarity": 0.82,
      "timestamp": "2026-02-18T10:00:00"
    }
  ],
  "query_embedded_by": "server"
}
```

`query_embedded_by`: "server" or "client" — indicates whether the query was embedded server-side or the client provided a pre-computed vector.

---

## Mobile Events (v2)

In-app event logging from React Native clients. Events are append-only and server-authoritative (no SQLite sync). Supports optional pre-computed embedding vectors for semantic search.

**Source**: [`routes/mobile_events_v2.py`](../UserApp/webapp/routes/mobile_events_v2.py)

### POST /api/v2/mobile-events

Log an in-app event. The mobile client assembles `event_text` as a human-readable description of user behavior (e.g., "User tapped sync button on SettingsSync screen").

**Request:**
```json
{
  "event_text": "User tapped sync button",
  "device_type": "ios 18.0",
  "screen": "SettingsSync",
  "duration_ms": 312,
  "status": "success",
  "error_code": null,
  "embedding": [0.123, -0.456, ...]
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `event_text` | Yes | Human-readable event description (non-blank) |
| `device_type` | No | Assembled from `Platform.OS` + `Platform.Version` |
| `screen` | No | React Navigation route name (from `RootStackParamList`) |
| `duration_ms` | No | Client-measured duration in milliseconds (e.g., API call time, sync duration) |
| `status` | No | Outcome: `"success"`, `"error"`, or `"timeout"` |
| `error_code` | No | HTTP status string or error name when `status` is `"error"` |
| `embedding` | No | Pre-computed 768-dim float array (client-side Ollama). If omitted, server generates via Ollama. If server embedding fails, event is still stored without embedding. |

**Response (201):**
```json
{
  "id": "uuid",
  "message": "Event logged",
  "embedded_by": "server"
}
```

`embedded_by`: "server" (Ollama generated), "client" (pre-computed vector accepted), or absent (no embedding stored).

**Error Responses:**
- `400`: Missing or blank `event_text`
- `401`: Missing or invalid auth token
- `500`: Database insert failure

---

## Provider Delegation (User-Side) — REMOVED in Home Edition

> **Removed.** Home Edition is a single-household appliance with no providers, delegation, or organizations. The `/api/v1/providers*` endpoints (`GET /providers`, `GET /providers/available`, `POST /providers/grant`, `POST /providers/revoke`, `GET /providers/:provider_id`) and the NPI-verification endpoints **no longer exist**. The mobile client must feature-gate these by server edition: Home Edition returns `404` for these paths.
>
> The personal contact book (`user_provider_contacts`) is retained as plain CRUD elsewhere in the app, but it grants no data access — it is just an address book.

---

## Documents (UserDocs)

Medical document management: upload, metadata, download, soft-delete, and annotations.

**Source**: [`routes/documents.py`](../UserApp/webapp/routes/documents.py)

### POST /api/v1/documents/upload

Upload a document file. Multipart form data, 5 MB limit.

**Content-Type**: `multipart/form-data`

| Field | Required | Description |
|-------|----------|-------------|
| `file` | Yes | The document file (PDF, image, or plain text) |
| `title` | No | Display name (defaults to filename) |
| `category` | No | User classification label |

Allowed MIME types: `application/pdf`, `image/*`, `text/plain`.

**Response (201):**
```json
{
  "id": "uuid",
  "filename": "lab-results.pdf",
  "mime_type": "application/pdf",
  "file_size_bytes": 245760,
  "source": "upload",
  "ocr_status": "pending",
  "quality_label": null,
  "title": "Lab Results March 2026",
  "category": "labs",
  "tags": null,
  "created_at": "2026-03-15T10:00:00+00:00"
}
```

**Errors:**
- 400: `{"error": "No file provided"}`, `{"error": "Empty filename"}`, `{"error": "Empty file"}`
- 413: `{"error": "File too large (max 5 MB)"}`
- 415: `{"error": "File type not allowed: application/zip"}`

### GET /api/v1/documents

List the user's documents (paginated, excludes soft-deleted).

**Query params:**

| Param | Default | Max | Description |
|-------|---------|-----|-------------|
| `limit` | 50 | 200 | Results per page |
| `offset` | 0 | — | Pagination offset |

**Response (200):**
```json
{
  "documents": [
    {
      "id": "uuid",
      "filename": "lab-results.pdf",
      "mime_type": "application/pdf",
      "file_size_bytes": 245760,
      "source": "upload",
      "ocr_status": "pending",
      "quality_label": null,
      "page_count": null,
      "title": "Lab Results March 2026",
      "category": "labs",
      "tags": null,
      "created_at": "2026-03-15T10:00:00+00:00",
      "updated_at": "2026-03-15T10:00:00+00:00"
    }
  ],
  "total": 12,
  "limit": 50,
  "offset": 0
}
```

### GET /api/v1/documents/:id

Document detail including page list (pages populated after OCR processing in Phase 2).

**Response (200):**
```json
{
  "id": "uuid",
  "filename": "lab-results.pdf",
  "mime_type": "application/pdf",
  "file_size_bytes": 245760,
  "source": "upload",
  "ocr_status": "complete",
  "quality_label": "green",
  "page_count": 3,
  "title": "Lab Results March 2026",
  "category": "labs",
  "tags": ["bloodwork", "annual"],
  "ocr_text_full": "Patient: John Doe ...",
  "created_at": "2026-03-15T10:00:00+00:00",
  "updated_at": "2026-03-15T10:00:00+00:00",
  "pages": [
    {
      "id": "uuid",
      "page_number": 1,
      "ocr_text": "Page 1 extracted text...",
      "ocr_confidence": 0.92,
      "quality_label": "green",
      "image_path": "/data/userdocs/1/uuid/uuid/page_001.png"
    }
  ]
}
```

**Response (404):** `{"error": "Document not found"}`

### GET /api/v1/documents/:id/download

Download the original document file.

**Response (200):** Binary file with `Content-Disposition: attachment`.

**Response (404):** `{"error": "Document not found"}` or `{"error": "File not found on storage"}`

### PATCH /api/v1/documents/:id

Update document metadata (title, category, tags).

**Request:**
```json
{
  "title": "Updated Title",
  "category": "imaging",
  "tags": ["xray", "shoulder"]
}
```

All fields optional. At least one required.

**Response (200):**
```json
{
  "id": "uuid",
  "filename": "scan.pdf",
  "title": "Updated Title",
  "category": "imaging",
  "tags": ["xray", "shoulder"],
  "updated_at": "2026-03-15T11:00:00+00:00"
}
```

**Errors:**
- 400: `{"error": "No data provided"}`, `{"error": "No valid fields to update"}`
- 404: `{"error": "Document not found"}`

### DELETE /api/v1/documents/:id

Soft-delete a document (sets `deleted_at`, preserves file on disk).

**Response (200):** `{"deleted": true, "id": "uuid"}`
**Response (404):** `{"error": "Document not found"}`

---

## Document Annotations

Household members can annotate documents at the document or page level.

**Source**: [`routes/documents.py`](../UserApp/webapp/routes/documents.py)

### POST /api/v1/documents/:id/annotations

Create an annotation. Page-level if `page_number` is provided, otherwise document-level.

**Request:**
```json
{
  "body": "Note the elevated LDL on page 2",
  "page_number": 2
}
```

Required: `body` (non-blank). Optional: `page_number` (integer, null = document-level).

**Response (201):**
```json
{
  "id": "uuid",
  "document_id": "uuid",
  "author_type": "user",
  "author_id": "uuid",
  "page_number": 2,
  "body": "Note the elevated LDL on page 2",
  "created_at": "2026-03-15T12:00:00+00:00",
  "updated_at": "2026-03-15T12:00:00+00:00"
}
```

**Errors:**
- 400: `{"error": "body is required"}`
- 404: `{"error": "Document not found"}`

### GET /api/v1/documents/:id/annotations

List annotations on a document (paginated). Includes author display name resolved via user join.

**Query params:**

| Param | Default | Max | Description |
|-------|---------|-----|-------------|
| `limit` | 50 | 200 | Results per page |
| `offset` | 0 | — | Pagination offset |

**Response (200):**
```json
{
  "annotations": [
    {
      "id": "uuid",
      "document_id": "uuid",
      "author_type": "user",
      "author_id": "uuid",
      "page_number": 2,
      "body": "Follow up with lipid panel in 3 months",
      "created_at": "2026-03-15T14:00:00+00:00",
      "updated_at": "2026-03-15T14:00:00+00:00",
      "author_name": "Alex"
    }
  ],
  "total": 5,
  "limit": 50,
  "offset": 0
}
```

### PATCH /api/v1/documents/:id/annotations/:ann_id

Update an annotation's body. Only the original author can edit.

**Request:**
```json
{
  "body": "Updated annotation text"
}
```

Required: `body` (non-blank).

**Response (200):**
```json
{
  "id": "uuid",
  "body": "Updated annotation text",
  "updated_at": "2026-03-15T15:00:00+00:00"
}
```

**Errors:**
- 400: `{"error": "body is required"}`
- 404: `{"error": "Annotation not found or not owned by you"}`

### DELETE /api/v1/documents/:id/annotations/:ann_id

Delete an annotation. The annotation author can delete their own; the document owner can delete any annotation (moderator role).

**Response (200):** `{"deleted": true, "id": "uuid"}`

**Errors:**
- 403: `{"error": "Only annotation author or document owner can delete"}`
- 404: `{"error": "Annotation not found"}`

---

## Feedback

**Source**: [`routes/admin.py`](../UserApp/webapp/routes/admin.py)

Feedback from the web UI and mobile app is stored in the `feedback` table and logged locally. (Home Edition has no Slack integration — the enterprise webhook posting was removed.)

### GET /api/v1/feedback

**Query params:** `screen` or `page` (optional filter)

**Response (200):**
```json
{
  "feedback": [
    {
      "id": "uuid",
      "feedback_type": "bug",
      "content": "Login button not working",
      "page_context": "login",
      "app_version": "1.2.0",
      "status": "pending",
      "date": "2026-02-24T10:00:00"
    }
  ]
}
```

### POST /api/v1/feedback

**Request:**
```json
{
  "feedback": "Login button not working",
  "feedback_type": "bug",
  "page_context": "login",
  "app_version": "1.2.0",
  "browser": "Chrome 122"
}
```

Required: `feedback` (or `content`)

`feedback_type`: bug | feature | general | praise

`browser`: optional, auto-populated by the web feedback widget.

Logged locally (failures never affect the HTTP response).

**Response (201):** `{"id": "uuid", "message": "Feedback created"}`

### PUT /api/v1/feedback/:id

**Response (200):** `{"message": "Feedback updated"}`

### DELETE /api/v1/feedback/:id

**Response (200):** `{"message": "Feedback deleted"}`

### Web Widget

The floating feedback button is loaded via `shared/feedback-widget.js` in `index.html`. It sends Cookie-authenticated POST requests to `/api/v1/feedback` with `feedback`, `feedback_type`, `page`, and `browser` fields.

---

## Dietary Settings

User dietary preferences with history tracking. Each PUT creates a new active row and deactivates the previous one, preserving a complete change history.

**Source**: [`routes/dietary_settings.py`](../UserApp/webapp/routes/dietary_settings.py)

### GET /api/v1/dietary-settings

Returns the active dietary setting. With `?history=true`, returns all settings ordered by `effective_date` DESC.

**Query params:** `history` (optional, `true` | `1` | `yes`)

**Response (200) — single active setting:**
```json
{
  "id": "uuid",
  "diet_type": "mediterranean",
  "dietary_restrictions": "gluten-free",
  "calorie_target": 2000,
  "protein_target_g": 120.0,
  "carb_target_g": 200.0,
  "fat_target_g": 65.0,
  "meal_count_per_day": 3,
  "notes": "Adjusted for training block",
  "is_active": true,
  "effective_date": "2026-03-20",
  "end_date": null,
  "created_at": "2026-03-20T14:00:00+00:00",
  "updated_at": "2026-03-20T14:00:00+00:00"
}
```

Returns `null` if no active setting exists.

**Response (200) — with `?history=true`:** Array of all settings (active and inactive), newest first.

### POST /api/v1/dietary-settings

Create initial dietary settings. Returns 409 if an active setting already exists (use PUT to update).

**Request:**
```json
{
  "diet_type": "mediterranean",
  "dietary_restrictions": "gluten-free",
  "calorie_target": 2000,
  "protein_target_g": 120,
  "carb_target_g": 200,
  "fat_target_g": 65,
  "meal_count_per_day": 3,
  "notes": "Starting plan",
  "effective_date": "2026-03-20"
}
```

All fields optional. `meal_count_per_day` defaults to 3. `effective_date` defaults to today.

**Response (201):** `{"id": "uuid", "message": "Dietary settings created"}`

**Error (409):** `{"error": "Active dietary settings already exist. Use PUT to update."}`

### PUT /api/v1/dietary-settings

Update dietary settings by creating a new active row. The current active setting is deactivated (sets `is_active=false`, `end_date`). Same request body as POST.

**Response (200):** `{"id": "uuid", "message": "Dietary settings updated"}`

### DELETE /api/v1/dietary-settings/:id

Delete a dietary settings record (typically a historical entry). If the active setting is deleted, no automatic reactivation occurs.

**Response (200):** `{"message": "Dietary setting deleted"}`

---

## Reminders

Medication, appointment, and health activity reminders with CRUD, completion, and snooze.

**Source**: [`routes/reminders.py`](../UserApp/webapp/routes/reminders.py)

### GET /api/v1/reminders

Returns all reminders for the authenticated user, ordered by `time`.

**Query params:** `category` (optional filter — must be one of the valid categories)

**Valid categories:** `activity`, `appointment`, `health-check`, `hydration`, `medication`

**Response (200):**
```json
[
  {
    "id": "uuid",
    "user_id": "uuid",
    "title": "Take Lisinopril",
    "category": "medication",
    "time": "08:30",
    "frequency": "daily",
    "custom_days": null,
    "timezone": "America/New_York",
    "snooze_minutes": null,
    "privacy_level": "normal",
    "notes": "With breakfast",
    "enabled": true,
    "completed": false,
    "completed_at": null,
    "snoozed_until": null,
    "last_triggered": null,
    "health_input_id": "uuid",
    "created_at": "2026-03-23T06:00:00+00:00",
    "updated_at": "2026-03-23T06:00:00+00:00"
  }
]
```

**Error (400):** Invalid category value.

### POST /api/v1/reminders

Create a new reminder.

**Request:**
```json
{
  "title": "Take Lisinopril",
  "time": "08:30",
  "category": "medication",
  "frequency": "daily",
  "custom_days": [1, 2, 3, 4, 5],
  "timezone": "America/New_York",
  "snooze_minutes": 10,
  "privacy_level": "normal",
  "notes": "With breakfast",
  "enabled": true,
  "health_input_id": "uuid"
}
```

Required: `title`, `time` (HH:mm format, e.g. `"08:30"`)

Optional with defaults:
- `category` — defaults to `"medication"`. Valid: `activity`, `appointment`, `health-check`, `hydration`, `medication`
- `frequency` — defaults to `"daily"`. Valid: `custom`, `daily`, `monthly`, `once`, `weekly`
- `privacy_level` — defaults to `"normal"`. Valid: `hidden`, `normal`, `private`
- `custom_days` — integer array, 0=Sun through 6=Sat (used when frequency is `"custom"`)
- `enabled` — defaults to `true`

**Response (201):** `{"id": "uuid", "message": "Created"}`

**Errors (400):** Missing title/time, invalid time format, invalid category/frequency.

### PUT /api/v1/reminders/:id

Partial update — only provided fields are changed. Same field validation as POST.

**Request:** Any subset of the POST fields.

**Response (200):** `{"message": "Reminder updated"}`

**Error (400):** Invalid UUID, no data, invalid field values. **Error (404):** Reminder not found.

### DELETE /api/v1/reminders/:id

**Response (200):** `{"message": "Reminder deleted"}`

**Error (400):** Invalid UUID. **Error (404):** Reminder not found.

### POST /api/v1/reminders/:id/complete

Mark a reminder as completed. Sets `completed=true` and `completed_at` to current UTC time.

**Response (200):** `{"message": "Reminder completed"}`

**Error (400):** Invalid UUID. **Error (404):** Reminder not found.

### POST /api/v1/reminders/:id/snooze

Snooze a reminder. Sets `snoozed_until` to now + minutes.

**Request:**
```json
{
  "minutes": 15
}
```

`minutes` defaults to 10. Must be between 1 and 1440 (24 hours).

**Response (200):** `{"message": "Reminder snoozed"}`

**Error (400):** Invalid UUID, invalid minutes value. **Error (404):** Reminder not found.

---

## Correlation Report (Stub)

Generates mock correlation insights from recent health data. Currently returns template-based text with hardcoded confidence scores — not real statistical analysis. Intended as a UI scaffold while the real analytics engine is built.

**Source**: [`routes/correlation_report.py`](../UserApp/webapp/routes/correlation_report.py)

### GET /api/v1/correlation-report

Counts records from `health_metrics` (steps, sleep, weight) and `health_input_log` from the past 30 days, then returns templated insight objects.

**Response (200):**
```json
{
  "summary": "Based on your data from the past 30 days (42 total records), we found 3 notable correlations in your health patterns.",
  "insights": [
    {
      "title": "Sleep & Steps",
      "description": "Based on 15 step records and 10 sleep records, longer sleep duration appears to correlate with higher step counts the following day.",
      "recommendation": "Aim for 7-9 hours of sleep to support an active lifestyle.",
      "confidence": 0.85
    }
  ],
  "generatedAt": "2026-03-23T12:00:00+00:00"
}
```

Returns up to 4 insight types depending on available data: Sleep & Steps, Medication & Energy, Activity & Weight, Sleep & Weight. Returns an empty `insights` array with an "insufficient data" summary when no records exist.

---

## Miscellaneous

### GET /api/v1/session

Returns authenticated user info. See [Authentication.md](Authentication.md).

**Response (200):**
```json
{
  "id": "uuid",
  "user_id": "uuid",
  "email": "user@example.com",
  "username": "user@example.com",
  "tenant_id": 1,
  "database": "healthv10",
  "created_at": "2026-01-15T10:30:00+00:00",
  "is_developer": false
}
```

### GET /api/v1/is-developer

Check whether the authenticated user has the developer flag. The developer flag is admin-set only (`admin.py set-developer <email>`). Used by mobile and web clients to gate developer-only UI and features.

**Response (200):**
```json
{
  "is_developer": false
}
```

### GET /api/v1/config

**Response (200):** `{"theme": "default"}`

### GET /api/v1/mcp-config

Returns MCP (Model Context Protocol) server connection configuration for the current user. Includes a ready-to-use Claude Desktop config block.

**Source**: [`app.py`](../UserApp/webapp/app.py)

**Response (200):**
```json
{
  "database_name": "healthv10",
  "mcp_user": "user@example.com",
  "host": "localhost",
  "port": "13282",
  "connection_string": "http://localhost:13282/sse",
  "claude_desktop_config": {
    "minowa": {
      "command": "npx",
      "args": [
        "-y", "supergateway",
        "--sse", "http://localhost:13282/sse",
        "--header", "authorization:Bearer YOUR_API_KEY"
      ]
    }
  },
  "note": "Replace YOUR_API_KEY with an API key generated below."
}
```

The `host` and `port` are derived from the `MCP_BASE_URL` environment variable. The `claude_desktop_config` block can be pasted directly into Claude Desktop's `claude_desktop_config.json`.

---

## API Keys

Long-lived API keys for MCP or integration use. Keys use the `hbk_` prefix and are stored as SHA-256 hashes — the raw key is returned only once at creation time.

**Source**: [`app.py`](../UserApp/webapp/app.py)

### POST /api/v1/api-keys

Create a new API key.

**Request:**
```json
{
  "label": "My MCP key"
}
```

`label` is optional (defaults to `"MCP"`, max 100 characters).

**Response (201):**
```json
{
  "id": "uuid",
  "key": "hbk_abc123...",
  "label": "My MCP key",
  "key_prefix": "hbk_abc12345",
  "created_at": "2026-03-30T12:00:00+00:00"
}
```

**Important**: The `key` field is returned **only in this response**. It is never stored or retrievable again. The user must copy it immediately.

**Error (409):** `{"error": "..."}` — Maximum active API keys reached.

### GET /api/v1/api-keys

List all active API keys for the authenticated user. Never returns the full key or hash — only the prefix, label, and timestamps.

**Response (200):**
```json
[
  {
    "id": "uuid",
    "key_prefix": "hbk_abc12345",
    "label": "My MCP key",
    "created_at": "2026-03-30T12:00:00+00:00",
    "last_used_at": "2026-03-30T14:30:00+00:00"
  }
]
```

### DELETE /api/v1/api-keys/:key_id

Revoke an API key (soft delete — sets `revoked_at`). Only the key owner can revoke their own keys.

**Response (200):** `{"success": true}`

**Error (404):** `{"error": "Key not found or already revoked"}`

---

## Fax

Send and receive faxes via SignalWire integration. Inbound faxes can be accepted (creating a document record and triggering OCR) or rejected.

**Source**: [`routes/fax.py`](../UserApp/webapp/routes/fax.py)

### POST /api/v1/fax/send

Send a document as a fax.

**Request:**
```json
{
  "document_id": "uuid",
  "to_number": "+15551234567",
  "from_number": "+15555550100"
}
```

Required: `document_id`, `to_number` (E.164 format).
Optional: `from_number` (defaults to system default).

The document must exist in the user's documents (scoped to the authenticated user) and have a PDF file on disk.

**Response (201):**
```json
{
  "id": "uuid",
  "status": "queued",
  "provider_ref": "provider-reference-id",
  "queued_at": "2026-03-30T12:00:00+00:00"
}
```

**Error (400):** `{"error": "document_id and to_number are required"}`
**Error (400):** `{"error": "No PDF file found for this document"}`
**Error (404):** `{"error": "Document not found"}`

### GET /api/v1/fax/outbox

List sent faxes for the authenticated user, sorted by most recent first.

**Query parameters:**
- `limit` — Max records (default 50, max 100)
- `offset` — Pagination offset (default 0)

**Response (200):**
```json
{
  "faxes": [
    {
      "id": "uuid",
      "document_id": "uuid",
      "to_fax_number": "+15551234567",
      "status": "delivered",
      "provider_ref": "provider-reference-id",
      "retry_count": 0,
      "error_message": null,
      "queued_at": "2026-03-30T12:00:00+00:00",
      "sent_at": "2026-03-30T12:01:00+00:00",
      "delivered_at": "2026-03-30T12:03:00+00:00",
      "failed_at": null
    }
  ],
  "count": 1
}
```

**Status values**: `queued`, `sending`, `delivered`, `failed`

### GET /api/v1/fax/inbox

List received faxes for the authenticated user, sorted by most recent first.

**Query parameters:**
- `limit` — Max records (default 50, max 100)
- `offset` — Pagination offset (default 0)

**Response (200):**
```json
{
  "faxes": [
    {
      "id": "uuid",
      "from_number": "+15559876543",
      "to_number": "+15555550100",
      "page_count": 3,
      "status": "received",
      "document_id": null,
      "raw_file_path": "/data/faxes/inbound/file.pdf",
      "received_at": "2026-03-30T11:00:00+00:00",
      "processed_at": null
    }
  ],
  "count": 1
}
```

**Status values**: `received`, `processing`, `accepted`, `rejected`

### POST /api/v1/fax/inbox/:fax_id/accept

Accept a received fax. This:
1. Creates a `documents` record linked to the user
2. Copies the raw fax file to userdocs storage
3. Queues the document for OCR processing (runs in-process on a background thread)
4. Updates the fax status to `accepted`

**Response (200):**
```json
{
  "fax_id": "uuid",
  "status": "accepted",
  "document_id": "uuid"
}
```

The `document_id` can be used with the [Documents](#documents-userdocs) endpoints to view OCR results, annotations, etc.

**Error (404):** `{"error": "Fax not found"}`
**Error (409):** `{"error": "Fax already accepted"}` — Fax was already accepted or rejected.

### POST /api/v1/fax/inbox/:fax_id/reject

Reject a received fax. Sets the fax status to `rejected`.

**Response (200):**
```json
{
  "fax_id": "uuid",
  "status": "rejected"
}
```

**Error (404):** `{"error": "Fax not found"}`
**Error (409):** `{"error": "Fax already rejected"}` — Fax was already accepted or rejected.

### POST /api/v1/fax/webhook

**Server-to-server only** — not called by mobile or web clients.

Receives inbound fax notifications from the fax provider (SignalWire or local shim). Authenticated via provider-specific webhook signature verification, not `@require_auth`.

Uses an admin database connection (no per-user scoping — the user's identity isn't established pre-auth) to look up the user by fax number and insert the `fax_inbound` record. Falls back to `FAX_DEFAULT_USER_ID` environment variable during shim testing.

**Response (201):**
```json
{
  "id": "uuid",
  "status": "received",
  "received_at": "2026-03-30T11:00:00+00:00"
}
```

**Error (401):** `{"error": "Unauthorized"}` — Webhook signature verification failed.
**Error (404):** `{"error": "No user found for this fax number"}` — No user matched and no default user configured.

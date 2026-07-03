# UserMCP Qualitative Testing Runbook
## 2026-03-10T16:00Z

How to evaluate whether the UserMCP server returns health data that is actually useful for healthcare planning — not just structurally correct.

**Attention Conservation Notice**
For: the maintainer (and future testers once the system stabilizes)
What: Step-by-step scenarios for evaluating MCP tool responses as a real user would
Action: Run each scenario against a live server, record observations in the log at the end
Skip if: You are looking for unit tests or curl smoke tests — see `tests/` for those


## How This Document Works

The existing test stack (pytest unit tests, curl smoke tests) validates that endpoints return the right shapes and status codes. This document picks up where those leave off: given real data from a real user, can Claude actually help you plan a doctor visit, spot a medication gap, or make sense of a week's sleep?

Each scenario is a prompt you send to Claude (via Claude Desktop with the UserMCP connected), followed by criteria for evaluating the response. You are the judge. The question is always: "Would I trust this enough to bring it to an appointment?"

**Prerequisites for all scenarios:**

- UserMCP running and connected to Claude Desktop (see `mcp.json` or `README.md`)
- At least 30 days of real health data in the system (Garmin sync, HealthKit, manual logs)
- Active medications and supplements configured with stacks and timeframes
- A bearer token for a real user account (not the test@example.com stub)


## Scenario 1: Appointment Prep Snapshot

**Purpose:** Can the snapshot tool produce a summary a doctor would find useful?

**Prompt to Claude:**
> I have a checkup with my doctor next week. Can you give me a health snapshot for the past 30 days covering heart rate, blood pressure, sleep, stress, weight, and medications?

**Evaluate:**

- Does the response include all six data types, or does it silently drop any?
- Are date ranges correct (30 days back from today, not some other window)?
- Heart rate: Are resting vs. active values distinguishable? Are min/max/avg present?
- Blood pressure: Are systolic and diastolic clearly labeled? Are timestamps present so you can see time-of-day patterns?
- Sleep: Can you tell how many hours per night, and whether deep/REM/light stages are broken out?
- Stress: Are readings granular enough to spot patterns (morning vs. evening), or are they just daily averages?
- Weight: Is the trend direction clear (up, down, stable)?
- Medications: Does it show what you are prescribed to take, not just what you logged?
- Does Claude add the medical disclaimer, or does it skip it?

**Red flags:**

- Empty arrays for data types you know have data
- Timestamps in UTC without localization (you shouldn't have to do timezone math)
- Medication list showing only names with no dosage, frequency, or stack context
- Claude presenting raw JSON instead of a readable summary


## Scenario 2: Medication Adherence Review

**Purpose:** Can the system help you see whether you are taking your meds consistently?

**Prompt to Claude:**
> How has my medication adherence been this past week? Am I missing any doses?

**Evaluate:**

- Does Claude call both `get_health_config` (what you should be taking) and `get_health_data` with `medication_log` (what you actually took)?
- Does it compare the two? A list of logged doses without knowing the prescribed schedule is useless.
- Can you identify specific missed doses by day and time?
- Are stack names included (e.g., "morning stack," "bedtime stack") so you know which bundle was missed?
- Does the response account for timeframes (wake, afternoon, bed), or does it just dump timestamps?

**Red flags:**

- Only showing logged doses with no comparison to the schedule
- Treating supplement_log and medication_log as interchangeable when they shouldn't be
- No mention of stacks or timeframes — just a flat list of pills
- Missing the distinction between "not logged" and "not taken" (the system tracks logging, not ingestion)


## Scenario 3: Sleep Quality Investigation

**Purpose:** Can you use the data to understand a bad week of sleep?

**Prompt to Claude:**
> I've been sleeping terribly this past week. Can you look at my sleep data and see what's going on? Compare it to the week before.

**Evaluate:**

- Does Claude use two date ranges (this week vs. last week) and present a comparison?
- Are sleep stages (deep, light, REM, awake) broken out, or just total hours?
- Can you see night-by-night data, not just averages? Averages hide the bad nights.
- Does Claude correlate with anything useful — stress levels, late meals, caffeine (if tracked)?
- Is the `usermcp://disclaimers` resource consulted? The caveats about wearable sleep accuracy are relevant here.

**Red flags:**

- Only showing averages across the whole period (masks the bad nights)
- Missing sleep_stages entirely (only sleep_summary)
- No acknowledgment that wearable sleep data has known accuracy limits
- Confusing Garmin sleep data timestamps (which may cross midnight) with calendar dates


## Scenario 4: Food and Vitals Correlation

**Purpose:** Can the system help spot whether diet affects your numbers?

**Prompt to Claude:**
> I want to see if what I eat affects my blood pressure. Show me my food log and blood pressure readings for the past two weeks side by side.

**Evaluate:**

- Does Claude fetch both food_log and blood_pressure for the same date range?
- Are food entries timestamped so you can see what was eaten before a BP reading?
- Is the response organized chronologically (or at least grouped by day) so you can scan for patterns?
- Does Claude note the "correlation is not causation" caveat from `usermcp://disclaimers`?
- Is sodium or any other nutritional detail surfaced from the food log, or just meal names?

**Red flags:**

- Food log and BP data fetched for different date ranges
- No temporal alignment — just two separate lists with no attempt to relate them
- Food log entries missing nutritional data (just names, no macros)
- Claude making causal claims ("your sodium intake caused your BP spike") without the caveat


## Scenario 5: Lab Results and Context

**Purpose:** Can Claude present lab results in a way that helps you prepare questions for your doctor?

**Prompt to Claude:**
> What do my latest lab results show? Anything I should ask my doctor about?

**Evaluate:**

- Are results presented with the reference range alongside each value?
- Are out-of-range values clearly flagged?
- Does Claude explain what the test measures in plain language (not just the LOINC code)?
- Is the medical disclaimer present — that this is for discussion with your provider, not self-diagnosis?
- Does Claude suggest specific questions to ask, or just dump the numbers?

**Red flags:**

- Lab results with no reference ranges (just raw numbers)
- LOINC codes shown without human-readable names
- Claude diagnosing conditions based on lab values
- No disclaimer or caveat about self-interpretation


## Scenario 6: Health Config Sanity Check

**Purpose:** Does the system accurately reflect what you are supposed to be taking?

**Prompt to Claude:**
> What medications and supplements am I currently taking? Show me my full health setup.

**Evaluate:**

- Does the response match what you actually have configured in the app?
- Are inactive items clearly marked as inactive (not just omitted)?
- Are stacks shown with their component inputs and timeframes?
- Is dosage information present (amount, unit, frequency)?
- Can you tell from the response alone what to take and when?

**Red flags:**

- Missing items that you know are configured
- Active/inactive status not shown
- Stacks listed without their contents
- Timeframes missing (you see "morning stack" but not that "morning" means 7:00 AM)


## Scenario 7: Edge Cases and Failure Modes

**Purpose:** What happens when data is missing, sparse, or broken?

Run these one at a time:

**7a. Empty date range:**
> Show me my health data for yesterday.
(Pick a day you know has no data — e.g., a day the Garmin wasn't worn.)

Evaluate: Does Claude say "no data found" cleanly, or does it error out? Does it suggest checking device sync status?

**7b. Very long range:**
> Give me a snapshot of the past 6 months.

Evaluate: Does the response handle large datasets gracefully? Is it summarized rather than dumped? Does it take an unreasonable amount of time?

**7c. Unsupported data type:**
> Show me my VO2 max data.

Evaluate: Does Claude explain that this type isn't supported and list what is? Or does it silently return nothing?

**7d. Mixed real and missing data:**
> Show me heart rate, blood pressure, and blood glucose for the past week.
(Assuming you have HR and BP but not glucose.)

Evaluate: Does the response clearly indicate which types had data and which didn't? Or does it just omit glucose with no explanation?


## Scenario 8: Multi-Tool Coordination

**Purpose:** When Claude needs to combine multiple MCP tools to answer a question, does it do so correctly?

**Prompt to Claude:**
> I want to understand my health trends this month. What am I taking, how well am I adhering to my schedule, what does my sleep look like, and are there any lab results I should know about?

**Evaluate:**

- Does Claude call all four relevant tools: `get_health_config`, `get_health_data`, `get_health_snapshot`, and `get_lab_results`?
- Is the combined response coherent, or does it feel like four separate answers pasted together?
- Is there any cross-referencing (e.g., mentioning that a medication might affect a lab value)?
- Is the response organized in a way you could hand to your doctor?

**Red flags:**

- Only calling one or two of the four tools
- Repeating the same data from different tools without noticing the overlap
- No attempt at synthesis — just sequential data dumps
- Response so long it becomes unusable


## Recording Your Observations

After running a scenario, record your findings here. Date each entry.

### Observation Log

```
Date:
Scenario:
Server:     (local / pilotvps / prodvps)
Result:     (pass / partial / fail)
Notes:

---
```

Copy this template for each run. Over time, the log tells us which tools are production-ready and which need work.

```
Date:       2026-03-10
Scenario:   1 — Appointment Prep Snapshot
Server:     Minowa MCP (via Cowork / Claude Desktop)
Result:     FAIL (2 of 6 data types returned data; config has structural gaps)

Findings by criterion:

  Date range:       PASS — 30 days, Feb 9 to Mar 10, correct.
  Heart rate:       FAIL — Empty array. Garmin Fenix 6S user should have data.
  Blood pressure:   PARTIAL — 3 readings, well-structured (sys/dia/pulse/tz),
                    but 22-day gap (Feb 10–Mar 4) not flagged by the system.
  Sleep:            FAIL — Both sleep_summary and sleep_stages empty.
  Stress:           FAIL — Empty. Garmin should have continuous stress data.
  Weight:           PASS — 3 readings, upward trend (180.7→185.1 lbs),
                    unit present. source=null on 1 of 3 entries (minor).
  Medications:      PARTIAL — 60 inputs and 8 stacks returned, but:
                    - doses_per_day is null on ALL 60 inputs
                    - timeframes array is empty (stacks named "Wake Time"
                      etc. but no scheduled times defined)
                    - Test artifacts in production: "Curl Test Vitamin",
                      "Curl Test Stack", "v2 Test Vitamin"
                    - Null dosage on 4 items (Antivert, Lidocaine,
                      Salonpas, Tamiflu)
                    - Apparent duplicates: Kava/kava kava,
                      Iron/iron bisglycinate, Curcumin/curcumin plus
                      piperine, Allegra/fexofenadine
                    - Allegra classified as supplement (should be OTC med)
  Disclaimer:       NOT PRESENT in tool responses. Suggestions resource
                    has one but was not fetched in this flow.

Root cause hypothesis:
  HR, sleep, stress empty suggests Garmin sync data is not reaching
  health-query. Possible causes: Garmin sync not running, data in
  different tables not covered by health-query kinds, or date filtering
  mismatch. Needs investigation at the UserApp API level.

Verdict:
  Not usable for doctor appointment prep in current state. BP and
  weight structure is good. Medication config has the right shape but
  is missing frequency, scheduling, and has data quality issues.
---
```

```
Date:       2026-03-10 (run 2)
Scenario:   1 revised — Appointment Prep with PRN usage
Server:     Minowa MCP (via Cowork / Claude Desktop)
Result:     PARTIAL (intake logs returned; three new issues found)

Prompt change:
  Added "Be sure to get the PRN medication usage" to the original
  Scenario 1 prompt. This forces a call to get_health_data with
  medication_log and supplement_log in addition to the snapshot.

Findings:

  medication_log:    100 entries returned. Timestamps with timezone,
                     dosage_taken, stack_name all present. Good shape.
  supplement_log:    100 entries — IDENTICAL to medication_log.
                     Not filtered by input_type. Both contain
                     Tramadol, Lisinopril, L-theanine, etc.
  PRN detection:     Zofran appeared once (Mar 8) — correctly looks
                     like PRN use. Norco, Paxlovid, Tamiflu,
                     Coricidin, Antivert, Lidocaine: zero entries.
                     Ambiguous — not taken, or not logged?
  Pagination:        Both logs returned exactly 100 entries. Likely
                     a hard cap in the UserApp API. True count for
                     30 days of 8+ stacks should be much higher.
                     Lisinopril shows 4 logs (should be ~30).

New issues identified:

  PLAN-007 (P1): medication_log and supplement_log return identical
    data. The health-input-log endpoint doesn't filter by input_type.
  PLAN-008 (P1): 100-entry pagination cap truncates real adherence
    data. Doctors would see false-low counts.
  PLAN-009 (P2): No PRN flag on log entries. Can't distinguish
    scheduled vs. as-needed doses programmatically.

Verdict:
  The intake log data exists and has the right structure, but the
  duplicate responses and pagination cap make it unreliable for
  adherence analysis. Adding PRN to the prompt was a good test —
  it exposed the medication_log/supplement_log identity problem
  and the truncation issue.
---
```


## Proposed Changes From Testing

Plans identified during qualitative testing. No code changes without approval.

### PLAN-001: Investigate empty Garmin data types (P0)

Heart rate, sleep, and stress all returned empty for a 30-day window on a Garmin Fenix 6S user. This blocks half the appointment-prep scenario.

Investigation steps:
1. Check whether Garmin sync jobs have run recently (`garmin_sync_jobs` table)
2. Verify that `garm_hr`, `garm_sleep_events`, and `garm_stress` tables have data for the test user in the Feb 9–Mar 10 range
3. Check whether health-query's `kind` values for heart_rate, sleep, and stress actually query those Garmin-specific tables, or whether they only query the generic `health_metrics` table
4. If the data exists but health-query doesn't route to it, the fix is in UserApp's health-query endpoint — not in UserMCP

### PLAN-002: Add frequency/scheduling fields to health config response (P1)

`doses_per_day` is null on all 60 inputs. Timeframes array is empty despite stacks being named for times of day. Without frequency and schedule, a doctor can't reconstruct the dosing regimen from the MCP response.

Proposed:
1. In UserApp, ensure `doses_per_day` is populated when health inputs are created or edited
2. Investigate why timeframes are empty — are they not configured for this user, or is the API not returning them?
3. Consider having `get_health_config` include the timeframe name inline with each stack (currently `timeframe_name: null` on every stack)

### PLAN-003: Filter test artifacts from production responses (P1)

"Curl Test Vitamin," "Curl Test Stack," and "v2 Test Vitamin" appear in the health config alongside real medications. Options:
1. Delete the test data from the production user's account
2. Add a `is_test` flag to health_inputs and filter them from API responses
3. Create a dedicated test user account and stop using the production user for curl tests

Option 3 is the cleanest — the test user already exists (`test@example.com`).

### PLAN-004: Data quality annotations in snapshot responses (P2)

When the snapshot has sparse data (e.g., 3 BP readings in 30 days), the system should note the gap. Proposed additions to the snapshot response:
1. Add a `coverage` field per data type: number of readings, first/last date, largest gap
2. Flag data types with fewer than N readings for the requested period (threshold TBD)
3. This helps Claude (and the user) distinguish "no data exists" from "data is sparse"

### PLAN-005: Deduplicate or annotate apparent duplicate inputs (P2)

Several pairs appear to be the same thing under different names (Kava/kava kava, Allegra/fexofenadine, Iron/iron bisglycinate). Proposed:
1. Audit the health_inputs for this user and merge true duplicates
2. Consider adding a `generic_name` or `alias` field so the system can group brand/generic pairs
3. Reclassify Allegra from supplement to medication (it's an OTC antihistamine)

### PLAN-006: Attach medical disclaimer to snapshot/config tool responses (P3)

The disclaimer exists in the `usermcp://disclaimers` resource but isn't included when snapshot or config tools are called. Options:
1. Have the MCP server automatically append a short disclaimer field to snapshot and config responses
2. Rely on Claude's system prompt to add the disclaimer (current behavior — inconsistent)
3. Both: server includes it, Claude's prompt reinforces it

Option 3 is safest for a health application.

### PLAN-007: Separate medication_log and supplement_log responses (P1)

Both `medication_log` and `supplement_log` return the exact same 100 entries because `_fetch_via_input_log` calls GET `/health-input-log` without filtering by `input_type`. Tramadol and Lisinopril appear in the supplement log; L-theanine and NAC appear in the medication log.

Options:
1. Add an `input_type` query parameter to the UserApp `/health-input-log` endpoint and have UserMCP pass `medication` or `supplement` as appropriate
2. Filter client-side in UserMCP after fetching — split the single response by `input_type` (requires the endpoint to return `input_type` on each entry, which it currently doesn't)
3. Option 1 is cleaner because it also reduces response size

### PLAN-008: Remove or raise the 100-entry pagination cap (P1)

Both medication_log and supplement_log returned exactly 100 entries for a 30-day window. With 8 stacks logged multiple times daily, true entry count should be in the hundreds. The truncation makes Lisinopril show 4 logs instead of ~30, which would look like terrible adherence to a doctor.

Options:
1. Check the UserApp `/health-input-log` endpoint for a default `LIMIT 100` and either remove it or raise it significantly for MCP use
2. Implement pagination in UserMCP — fetch in pages until exhausted
3. Option 1 is simpler; option 2 is safer for very long date ranges

### PLAN-009: Add PRN flag or classification to log entries (P2)

There's no way to tell whether a logged dose was scheduled (part of a stack) or taken as-needed (PRN). The `stack_name` field helps — entries with a stack name are likely scheduled, entries without one are more likely PRN — but this is inference, not explicit.

Proposed:
1. Add a `usage_type` field to health_input_log: `scheduled`, `prn`, or `unspecified`
2. Default to `scheduled` when logged via a stack, `prn` when logged individually for a PRN-configured input
3. This would let the MCP response clearly show "Zofran: 1 PRN dose on Mar 8" vs. "Tramadol: 11 scheduled doses"


## What This Document Does Not Cover

- **Unit tests**: See `tests/test_mcp_server.py` (60+ async tests, pytest)
- **HTTP smoke tests**: See `tests/curl-tests.sh` (auth, SSE, endpoints)
- **Remote connectivity**: See `tests/remote-test.sh`

Those test layers validate plumbing. This document validates usefulness.


## Change Log

| Date | Change |
|------|--------|
| 2026-03-10 | Initial version. Eight scenarios covering appointment prep, medication adherence, sleep, food/vitals correlation, lab results, config sanity, edge cases, and multi-tool coordination. |
| 2026-03-10 | Ran Scenario 1 (Appointment Prep). Result: FAIL. 4 of 6 data types empty (HR, sleep, stress, sleep stages). Medication config missing frequency and scheduling. Six proposed changes documented (PLAN-001 through PLAN-006). |
| 2026-03-10 | Ran Scenario 1 revised (with PRN). medication_log and supplement_log return identical data; 100-entry pagination cap truncates adherence data; no PRN flag on entries. Three new plans added (PLAN-007 through PLAN-009). |

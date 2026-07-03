#!/usr/bin/env python3
"""
load_records.py — Load JSON health records into the Minowa Home Edition appliance via API calls.
2026-03-03

Authenticates as each test user and POSTs their clinical data through the
REST API.  Requires that seed-test-data.sh has already run (users exist in DB).

Usage:
    python TestData/load_records.py                         # default: http://localhost (local Mac)
    python TestData/load_records.py --base-url http://192.168.88.101   # home appliance
    python TestData/load_records.py --user rodrigo           # load one user only
    python TestData/load_records.py --dry-run                # show what would be sent

Point this at the appliance on the LAN (default http://localhost). Test data does
not belong there.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

RECORDS_DIR = Path(__file__).parent / "records"
DEFAULT_PASSWORD = "Password2026"
DEFAULT_BASE_URL = "http://localhost"


def api_call(base_url: str, method: str, path: str, body: dict | None = None,
             token: str | None = None, retries: int = 2) -> tuple[int, dict]:
    """Make an API call and return (status_code, response_json).

    Retries on rate-limit (429) or empty responses with exponential backoff.
    """
    url = f"{base_url}{path}"
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    for attempt in range(retries + 1):
        req = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=30) as resp:
                raw = resp.read().decode()
                if not raw.strip():
                    raise ValueError("Empty response body")
                try:
                    resp_body = json.loads(raw)
                except json.JSONDecodeError:
                    # Non-JSON response (HTML error page, rate limit page, etc.)
                    snippet = raw[:200].replace('\n', ' ')
                    return resp.status, {"error": f"Non-JSON response: {snippet}"}
                return resp.status, resp_body
        except HTTPError as e:
            raw = e.read().decode() if e.fp else ""
            if e.code == 429 and attempt < retries:
                wait = 2 ** (attempt + 1)
                print(f"        Rate limited ({e.code}), waiting {wait}s...")
                time.sleep(wait)
                continue
            try:
                resp_body = json.loads(raw) if raw.strip() else {"error": f"HTTP {e.code}"}
            except json.JSONDecodeError:
                resp_body = {"error": f"HTTP {e.code}: {raw[:200]}"}
            return e.code, resp_body
        except (ValueError, URLError) as e:
            if attempt < retries:
                wait = 2 ** (attempt + 1)
                print(f"        Retrying in {wait}s ({e})...")
                time.sleep(wait)
                continue
            return 0, {"error": str(e)}
    return 0, {"error": "retries exhausted"}


def login(base_url: str, email: str, password: str) -> str | None:
    """Login and return bearer token, or None on failure.

    Retries up to 3 times with increasing delays to handle login rate limits
    (5 requests/minute per IP on the pilot system).
    """
    for attempt in range(4):
        status, resp = api_call(base_url, "POST", "/api/v1/login",
                                {"email": email, "password": password})
        if status == 200 and resp.get("success"):
            return resp["token"]
        if status == 429 or "rate" in str(resp.get("error", "")).lower():
            wait = 6 * (attempt + 1)  # 6s, 12s, 18s — enough to clear 5/min window
            print(f"    Login rate limited, waiting {wait}s...")
            time.sleep(wait)
            continue
        print(f"    LOGIN FAILED ({status}): {resp.get('error', resp)}")
        return None
    print("    LOGIN FAILED after retries: rate limit not clearing")
    return None


def load_conditions(base_url: str, token: str, records: list, dry_run: bool) -> int:
    """POST health conditions. Returns count of successful inserts."""
    count = 0
    for rec in records:
        payload = {
            "name": rec["name"],
            "icd10_code": rec.get("icd10_code"),
            "diagnosed_date": rec.get("diagnosed_date"),
            "status": rec.get("status", "active"),
            "severity": rec.get("severity"),
            "treating_doctor": rec.get("treating_doctor"),
            "notes": rec.get("notes"),
            "custom_fields": rec.get("custom_fields"),
        }
        if dry_run:
            print(f"      [DRY RUN] POST /api/v1/conditions: {payload['name']}")
            count += 1
            continue
        status, resp = api_call(base_url, "POST", "/api/v1/conditions", payload, token)
        if status == 201:
            count += 1
        else:
            print(f"      WARN condition '{payload['name']}': {status} {resp}")
    return count


def load_allergies(base_url: str, token: str, records: list, dry_run: bool) -> int:
    count = 0
    for rec in records:
        payload = {
            "allergen": rec["allergen"],
            "allergy_type": rec.get("allergy_type"),
            "reaction": rec.get("reaction"),
            "severity": rec.get("severity"),
            "onset_date": rec.get("onset_date"),
            "status": rec.get("status", "active"),
            "notes": rec.get("notes"),
            "source": rec.get("source", "manual"),
        }
        if dry_run:
            print(f"      [DRY RUN] POST /api/v1/allergies: {payload['allergen']}")
            count += 1
            continue
        status, resp = api_call(base_url, "POST", "/api/v1/allergies", payload, token)
        if status == 201:
            count += 1
        else:
            print(f"      WARN allergy '{payload['allergen']}': {status} {resp}")
    return count


def load_health_inputs(base_url: str, token: str, records: list, dry_run: bool) -> dict[str, str]:
    """POST health inputs. Returns map of original_id -> server_id for stack linking."""
    id_map = {}
    for rec in records:
        payload = {
            "name": rec["name"],
            "input_type": rec["input_type"],
            "default_dosage": rec.get("dosage"),
            "form": rec.get("form"),
            "is_active": rec.get("active", True),
            "notes": rec.get("notes"),
            "default_unit": rec.get("default_unit"),
            "take_with_food": rec.get("take_with_food", False),
        }
        # Pass category from custom_fields if present
        cf = rec.get("custom_fields") or {}
        if cf.get("category"):
            payload["category"] = cf["category"]
        if dry_run:
            print(f"      [DRY RUN] POST /api/v1/health-inputs: {payload['name']}")
            id_map[rec["id"]] = "dry-run-id"
            continue
        status, resp = api_call(base_url, "POST", "/api/v1/health-inputs", payload, token)
        if status == 201:
            id_map[rec["id"]] = resp["id"]
        else:
            print(f"      WARN input '{payload['name']}': {status} {resp}")
    return id_map


def load_timeframes(base_url: str, token: str, records: list, dry_run: bool) -> dict[str, str]:
    """POST timeframes. Returns map of original_id -> server_id."""
    id_map = {}
    for rec in records:
        payload = {
            "name": rec["name"],
            "time_of_day": rec.get("time_of_day"),
            "sort_order": rec.get("sort_order", 0),
            "is_active": rec.get("is_active", True),
        }
        if dry_run:
            print(f"      [DRY RUN] POST /api/v1/timeframes: {payload['name']}")
            id_map[rec["id"]] = "dry-run-id"
            continue
        status, resp = api_call(base_url, "POST", "/api/v1/timeframes", payload, token)
        if status == 201:
            id_map[rec["id"]] = resp["id"]
        else:
            print(f"      WARN timeframe '{payload['name']}': {status} {resp}")
    return id_map


def load_stacks(base_url: str, token: str, stacks: list, stack_inputs: list,
                input_id_map: dict, tf_id_map: dict, dry_run: bool) -> int:
    """POST stacks with their inputs. Returns count of successful stacks."""
    # Build lookup: old_stack_id -> [stack_input records]
    si_by_stack = {}
    for si in stack_inputs:
        sid = si["stack_id"]
        si_by_stack.setdefault(sid, []).append(si)

    count = 0
    for stack in stacks:
        # Map timeframe_id to server-side id
        old_tf_id = stack.get("timeframe_id")
        new_tf_id = tf_id_map.get(old_tf_id) if old_tf_id else None

        # Map stack inputs
        inputs = []
        for si in si_by_stack.get(stack["id"], []):
            old_input_id = si["health_input_id"]
            new_input_id = input_id_map.get(old_input_id)
            if new_input_id:
                inputs.append({
                    "input_id": new_input_id,
                    "dosage_override": si.get("dosage_override"),
                })

        payload = {
            "name": stack["name"],
            "timeframe_id": new_tf_id,
            "is_active": stack.get("is_active", True),
            "inputs": inputs,
        }
        if dry_run:
            print(f"      [DRY RUN] POST /api/v1/stacks: {payload['name']} ({len(inputs)} inputs)")
            count += 1
            continue
        status, resp = api_call(base_url, "POST", "/api/v1/stacks", payload, token)
        if status == 201:
            count += 1
        else:
            print(f"      WARN stack '{payload['name']}': {status} {resp}")
    return count


def load_blood_pressure(base_url: str, token: str, records: list, dry_run: bool) -> int:
    count = 0
    for rec in records:
        payload = {
            "timestamp": rec["measured_at"],
            "systolic": rec["systolic"],
            "diastolic": rec["diastolic"],
            "heart_rate": rec.get("pulse"),
        }
        if dry_run:
            print(f"      [DRY RUN] POST /api/v1/blood-pressure: {payload['systolic']}/{payload['diastolic']}")
            count += 1
            continue
        status, resp = api_call(base_url, "POST", "/api/v1/blood-pressure", payload, token)
        if status == 201:
            count += 1
        else:
            print(f"      WARN BP {payload['systolic']}/{payload['diastolic']}: {status} {resp}")
    return count


def load_blood_work(base_url: str, token: str, records: list, dry_run: bool) -> int:
    count = 0
    for rec in records:
        payload = {
            "test_date": rec["test_date"],
            "test_name": rec["test_name"],
            "value": rec.get("value"),
            "unit": rec.get("unit"),
            "reference_range": rec.get("reference_range"),
            "is_abnormal": rec.get("is_abnormal", False),
            "lab_name": rec.get("lab_name"),
            "loinc_code": rec.get("loinc_code"),
            "panel_name": rec.get("panel_name"),
            "notes": rec.get("notes"),
        }
        if dry_run:
            print(f"      [DRY RUN] POST /api/v1/blood-work: {payload['test_name']}")
            count += 1
            continue
        status, resp = api_call(base_url, "POST", "/api/v1/blood-work", payload, token)
        if status == 201:
            count += 1
        else:
            print(f"      WARN lab '{payload['test_name']}': {status} {resp}")
    return count


def load_weight(base_url: str, token: str, records: list, dry_run: bool) -> int:
    count = 0
    for rec in records:
        payload = {
            "timestamp": rec["recorded_at"],
            "weight": rec["value"],
            "unit": rec.get("unit", "kg"),
        }
        if dry_run:
            print(f"      [DRY RUN] POST /api/v1/weight: {payload['weight']} {payload['unit']}")
            count += 1
            continue
        status, resp = api_call(base_url, "POST", "/api/v1/weight", payload, token)
        if status == 201:
            count += 1
        else:
            print(f"      WARN weight {payload['weight']}: {status} {resp}")
    return count


def load_family_history(base_url: str, token: str, records: list, dry_run: bool) -> int:
    count = 0
    for rec in records:
        payload = {
            "relationship": rec["relationship"],
            "condition_name": rec.get("condition_name"),
            "icd10_code": rec.get("icd10_code"),
            "age_at_onset": rec.get("age_at_onset"),
            "vital_status": rec.get("vital_status"),
            "notes": rec.get("notes"),
        }
        if dry_run:
            print(f"      [DRY RUN] POST /api/v1/family-history: {payload['relationship']} - {payload['condition_name']}")
            count += 1
            continue
        status, resp = api_call(base_url, "POST", "/api/v1/family-history", payload, token)
        if status == 201:
            count += 1
        else:
            print(f"      WARN family_hx '{payload['relationship']}': {status} {resp}")
    return count


def load_social_history(base_url: str, token: str, records: list, dry_run: bool) -> int:
    count = 0
    for rec in records:
        payload = {
            "category": rec["category"],
            "status": rec.get("status"),
            "detail": rec.get("detail"),
            "quantity": rec.get("quantity"),
        }
        if dry_run:
            print(f"      [DRY RUN] POST /api/v1/social-history: {payload['category']}")
            count += 1
            continue
        status, resp = api_call(base_url, "POST", "/api/v1/social-history", payload, token)
        if status == 201:
            count += 1
        else:
            print(f"      WARN social_hx '{payload['category']}': {status} {resp}")
    return count


def load_vaccinations(base_url: str, token: str, records: list, dry_run: bool) -> int:
    count = 0
    for rec in records:
        payload = {
            "vaccine_name": rec["vaccine_name"],
            "administered_date": rec.get("administered_date"),
            "site": rec.get("site"),
            "administered_by": rec.get("administered_by"),
        }
        if dry_run:
            print(f"      [DRY RUN] POST /api/v1/vaccinations: {payload['vaccine_name']}")
            count += 1
            continue
        status, resp = api_call(base_url, "POST", "/api/v1/vaccinations", payload, token)
        if status == 201:
            count += 1
        else:
            print(f"      WARN vaccine '{payload['vaccine_name']}': {status} {resp}")
    return count


def load_observations(base_url: str, token: str, records: list, dry_run: bool) -> int:
    count = 0
    for rec in records:
        payload = {
            "observation": rec["content"],
            "timestamp": rec.get("observed_at"),
            "source_type": rec.get("category", "text"),
        }
        if dry_run:
            print(f"      [DRY RUN] POST /api/v1/observations: {payload['observation'][:60]}...")
            count += 1
            continue
        status, resp = api_call(base_url, "POST", "/api/v1/observations", payload, token)
        if status == 201:
            count += 1
        else:
            print(f"      WARN observation: {status} {resp}")
    return count


def load_user(base_url: str, record_path: Path, dry_run: bool) -> dict:
    """Load all records for a single user. Returns summary dict."""
    with open(record_path) as f:
        data = json.load(f)

    meta = data["meta"]
    email = meta["email"]
    name = meta["display_name"]
    summary = {"user": name, "email": email, "totals": {}}

    print(f"\n  {name} ({email})")

    # Login
    if dry_run:
        print(f"    [DRY RUN] Would login as {email}")
        token = "dry-run-token"
    else:
        token = login(base_url, email, DEFAULT_PASSWORD)
        if not token:
            summary["error"] = "login failed"
            return summary
        print("    Logged in")

    # Load data in dependency order:
    # 1. Conditions, allergies (no deps)
    # 2. Health inputs (no deps, but needed for stacks)
    # 3. Timeframes (no deps, but needed for stacks)
    # 4. Stacks (depends on health_inputs + timeframes)
    # 5. Everything else (no deps)

    sections = [
        ("conditions",   "health_conditions",              load_conditions),
        ("allergies",    "health_allergies",                load_allergies),
        ("blood_work",   "health_blood_work",               load_blood_work),
        ("family_hx",    "health_family_history",            load_family_history),
        ("social_hx",    "health_social_history",            load_social_history),
        ("vaccinations", "health_vaccinations",              load_vaccinations),
        ("bp",           "health_blood_pressure_readings",   load_blood_pressure),
        ("observations", "health_observations",              load_observations),
    ]

    for label, key, loader in sections:
        records = data.get(key, [])
        if records:
            n = loader(base_url, token, records, dry_run)
            summary["totals"][label] = n
            print(f"    {label}: {n}/{len(records)}")

    # Weight (from health_metrics)
    metrics = data.get("health_metrics", [])
    if metrics:
        n = load_weight(base_url, token, metrics, dry_run)
        summary["totals"]["weight"] = n
        print(f"    weight: {n}/{len(metrics)}")

    # Health inputs (need id mapping for stacks)
    inputs = data.get("health_inputs", [])
    input_id_map = {}
    if inputs:
        input_id_map = load_health_inputs(base_url, token, inputs, dry_run)
        summary["totals"]["health_inputs"] = len(input_id_map)
        print(f"    health_inputs: {len(input_id_map)}/{len(inputs)}")

    # Timeframes (need id mapping for stacks)
    timeframes = data.get("timeframes", [])
    tf_id_map = {}
    if timeframes:
        tf_id_map = load_timeframes(base_url, token, timeframes, dry_run)
        summary["totals"]["timeframes"] = len(tf_id_map)
        print(f"    timeframes: {len(tf_id_map)}/{len(timeframes)}")

    # Stacks (depend on health_inputs + timeframes)
    stacks = data.get("stacks", [])
    stack_inputs = data.get("stack_inputs", [])
    if stacks:
        n = load_stacks(base_url, token, stacks, stack_inputs,
                        input_id_map, tf_id_map, dry_run)
        summary["totals"]["stacks"] = n
        print(f"    stacks: {n}/{len(stacks)}")

    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Load JSON health records into the Minowa Home Edition appliance via API calls")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help=f"API base URL (default: {DEFAULT_BASE_URL})")
    parser.add_argument("--user", default=None,
                        help="Load only this user (filename stem, e.g. 'rodrigo-borgia')")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be sent without making API calls")
    args = parser.parse_args()

    # Discover JSON files
    json_files = sorted(RECORDS_DIR.glob("*.json"))
    if not json_files:
        print(f"ERROR: No JSON files found in {RECORDS_DIR}")
        sys.exit(1)

    if args.user:
        # Filter to single user
        target = args.user.lower().replace(" ", "-")
        json_files = [f for f in json_files if target in f.stem.lower()]
        if not json_files:
            print(f"ERROR: No JSON file matching '{args.user}' in {RECORDS_DIR}")
            sys.exit(1)

    mode = "[DRY RUN] " if args.dry_run else ""
    print(f"\n{mode}Loading {len(json_files)} user records via {args.base_url}")
    print("=" * 60)

    start = time.time()
    summaries = []
    errors = []

    for i, json_file in enumerate(json_files):
        try:
            summary = load_user(args.base_url, json_file, args.dry_run)
            summaries.append(summary)
            if "error" in summary:
                errors.append(summary)
        except Exception as e:
            print(f"    ERROR: {e}")
            errors.append({"user": json_file.stem, "error": str(e)})

        # Brief pause between users to stay under login rate limits (5/min)
        if not args.dry_run and i < len(json_files) - 1:
            time.sleep(1.5)

    elapsed = time.time() - start

    # Summary
    print("\n" + "=" * 60)
    total_records = sum(
        sum(s.get("totals", {}).values())
        for s in summaries
    )
    print(f"\n{mode}Done: {len(summaries)} users, {total_records} records, {elapsed:.1f}s")

    if errors:
        print(f"\n  {len(errors)} user(s) had errors:")
        for e in errors:
            print(f"    - {e['user']}: {e.get('error', 'unknown')}")
        sys.exit(1)


if __name__ == "__main__":
    main()

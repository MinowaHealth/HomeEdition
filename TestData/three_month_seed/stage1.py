"""Stage 1 — clinical scaffolding loader from TestData/records/*.json.

Each persona's records file is the authoritative source for their baseline
clinical state (conditions, allergies, medications & supplements, schedules,
medication groupings, family history, social history, vaccinations).

POST topology — order matters because some routes reference earlier IDs:

  1. timeframes        (no FK deps)
  2. health_inputs     (no FK deps in the body — timeframe_id field exists
                        on the route, but records files don't use it; stacks
                        carry the timeframe ref instead)
  3. conditions, allergies, family/social history, vaccinations (no FK deps)
  4. stacks            (refs timeframe + carries embedded stack_inputs
                        which ref health_inputs)

Records files include records-side UUIDs that the routes do NOT accept
(routes generate server-side UUIDs). We map records_id → server_id per
persona during Stage 1 and use that map when forwarding stack_inputs.

Returns PersonaIds per persona; the activity loop reads `stacks` from
this to wire log_stack events to real stack rows.
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .meals import seed_meals_for_persona
from .sources.manual import ManualClient

log = logging.getLogger("seed.stage1")

# Records files live next to TestData/three_month_seed/, not inside it.
RECORDS_DIR = Path(__file__).resolve().parents[1] / "records"

# Records files always carry these fields; routes never accept them
# (server generates id; user_id is read from auth context).
_ALWAYS_DROP = frozenset({"id", "user_id"})


@dataclass
class PersonaIds:
    """Server-assigned IDs captured during Stage 1 for one persona.

    Keys are records-side UUIDs from the source JSON; values are
    server-assigned UUIDs returned by the POST routes. This lets stack
    bodies forward `inputs` arrays with correct server-side FKs.

    `food_items` and `meals` come from Stage 1.5 (meals.py) — generic
    catalog seeded per persona so log_meal events can reference real rows.
    """
    timeframes: dict[str, str] = field(default_factory=dict)
    health_inputs: dict[str, str] = field(default_factory=dict)
    stacks: dict[str, str] = field(default_factory=dict)
    food_items: dict[str, str] = field(default_factory=dict)
    meals: dict[str, str] = field(default_factory=dict)


@dataclass
class Stage1Result:
    """Per-persona captured IDs, keyed by persona email."""
    by_email: dict[str, PersonaIds] = field(default_factory=dict)

    def stack_ids_for(self, email: str) -> list[str]:
        """Convenience for the activity loop's log_stack event: returns
        the server-side stack UUIDs the persona owns, or [] if none."""
        ids = self.by_email.get(email)
        return list(ids.stacks.values()) if ids else []

    def meal_ids_for(self, email: str) -> list[str]:
        """Convenience for the activity loop's log_meal event: returns
        the server-side meal UUIDs the persona owns, or [] if none."""
        ids = self.by_email.get(email)
        return list(ids.meals.values()) if ids else []


def _strip(rec: dict, *, extra_drop: Iterable[str] = ()) -> dict:
    """Drop records-side metadata that routes don't accept."""
    drop = _ALWAYS_DROP | set(extra_drop)
    return {k: v for k, v in rec.items() if k not in drop and v is not None}


def _translate_health_input(rec: dict) -> dict:
    """records-shape → /api/v1/health-inputs body shape.

    Drift: records uses `dosage`/`active`/`route`/`start_date`/
    `prescribing_doctor`/`frequency` — the route expects `default_dosage`/
    `is_active` and ignores the rest.
    """
    body: dict = {
        "name": rec["name"],
        "input_type": rec["input_type"],
    }
    if rec.get("dosage") is not None:
        body["default_dosage"] = rec["dosage"]
    for key in ("default_unit", "brand", "form", "take_with_food", "notes"):
        if rec.get(key) is not None:
            body[key] = rec[key]
    if "active" in rec:
        body["is_active"] = rec["active"]
    return body


def _records_path_for(email: str) -> Path | None:
    """Find records/*.json by matching `meta.email`."""
    for fp in sorted(RECORDS_DIR.glob("*.json")):
        try:
            d = json.loads(fp.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if d.get("meta", {}).get("email") == email:
            return fp
    return None


def _seed_persona(
    client: ManualClient, email: str, doc: dict
) -> PersonaIds:
    """POST one persona's scaffolding in topo order; return captured IDs."""
    ids = PersonaIds()

    # 1. timeframes
    for rec in doc.get("timeframes", []):
        resp = client.post_timeframe(email, _strip(rec))
        if rec.get("id") and "id" in resp:
            ids.timeframes[rec["id"]] = resp["id"]

    # 2. health_inputs
    for rec in doc.get("health_inputs", []):
        resp = client.post_health_input(email, _translate_health_input(rec))
        if rec.get("id") and "id" in resp:
            ids.health_inputs[rec["id"]] = resp["id"]

    # 3. clinical history tables (no FK deps)
    for rec in doc.get("health_conditions", []):
        client.post_condition(email, _strip(rec))
    for rec in doc.get("health_allergies", []):
        client.post_allergy(email, _strip(rec))
    for rec in doc.get("health_family_history", []):
        client.post_family_history(email, _strip(rec))
    for rec in doc.get("health_social_history", []):
        client.post_social_history(email, _strip(rec))
    for rec in doc.get("health_vaccinations", []):
        client.post_vaccination(email, _strip(rec))

    # 4. stacks — group stack_inputs by their records-side stack_id, then
    # forward the join as the route's embedded `inputs` array.
    si_by_stack: dict[str, list[dict]] = {}
    for si in doc.get("stack_inputs", []):
        si_by_stack.setdefault(si["stack_id"], []).append(si)

    for stack_rec in doc.get("stacks", []):
        body: dict = {
            "name": stack_rec["name"],
            "is_active": stack_rec.get("is_active", True),
        }
        rec_tf = stack_rec.get("timeframe_id")
        if rec_tf and rec_tf in ids.timeframes:
            body["timeframe_id"] = ids.timeframes[rec_tf]
        embedded: list[dict] = []
        for si in sorted(si_by_stack.get(stack_rec["id"], []),
                         key=lambda x: x.get("sort_order", 0)):
            server_input = ids.health_inputs.get(si["health_input_id"])
            if server_input:
                embedded.append({"input_id": server_input})
        if embedded:
            body["inputs"] = embedded
        resp = client.post_stack(email, body)
        if "id" in resp:
            ids.stacks[stack_rec["id"]] = resp["id"]

    # 5. meals — generic catalog (foods + meal templates). Per-persona
    # because health_food_itemsv2/meals are per-user scoped (no system-user
    # global catalog yet).
    food_ids, meal_ids = seed_meals_for_persona(client, email)
    ids.food_items.update(food_ids)
    ids.meals.update(meal_ids)
    return ids


def run_stage1(
    client: ManualClient, persona_emails: list[str]
) -> Stage1Result:
    """Per-persona Stage 1 from records/*.json. Personas without a records
    file are skipped with a warning (the seeder still runs activity)."""
    result = Stage1Result()
    for email in persona_emails:
        path = _records_path_for(email)
        if path is None:
            log.warning("Stage 1: no records file for %s — skipping", email)
            continue
        try:
            doc = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            log.error("Stage 1: failed to read %s: %s", path, e)
            continue
        ids = _seed_persona(client, email, doc)
        result.by_email[email] = ids
        log.info(
            "Stage 1 %s: tfs=%d inputs=%d stacks=%d foods=%d meals=%d "
            "(conds=%d algy=%d fam=%d soc=%d vacc=%d)",
            email,
            len(ids.timeframes), len(ids.health_inputs), len(ids.stacks),
            len(ids.food_items), len(ids.meals),
            len(doc.get("health_conditions", [])),
            len(doc.get("health_allergies", [])),
            len(doc.get("health_family_history", [])),
            len(doc.get("health_social_history", [])),
            len(doc.get("health_vaccinations", [])),
        )
    return result

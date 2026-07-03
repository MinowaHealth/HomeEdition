"""Per-day-per-persona orchestrator.

Pure: emits a sequence of (event_kind, body) descriptors. The runner
(__main__.py) is responsible for turning descriptors into REST POSTs.
"""
from __future__ import annotations
from datetime import date, timedelta
from typing import Iterator
import numpy as np

from .personas.base import Persona
from .seed_rng import rng_for


def iter_days(end: date, window_days: int) -> Iterator[date]:
    """Inclusive [end - window_days + 1, end]."""
    for offset in range(window_days):
        yield end - timedelta(days=window_days - 1 - offset)


def _poisson_count(rng: np.random.Generator, mean: float) -> int:
    return int(rng.poisson(mean)) if mean > 0 else 0


def dispatch_persona_day(
    persona: Persona, day: date, seed: int, scale: float = 1.0,
) -> list[tuple[str, dict]]:
    """Return list of (event_kind, body) tuples to POST for this persona on
    this day. Counts derive from activity_profile; narrative_beats are layered
    on top.

    `scale` multiplies all Poisson means — SCALE=3.0 produces ~3x the
    activity volume in the same time window. Narrative beats are NOT scaled
    (they are scripted events at deterministic dates).
    """
    rng = rng_for(seed=seed, persona_id=persona.user_id, source_kind="manual", day=day)
    ap = persona.activity_profile

    events: list[tuple[str, dict]] = []
    # BP — daily
    for _ in range(_poisson_count(rng, ap.bp_per_day * scale)):
        events.append(("bp", {
            "systolic": int(rng.normal(125, 8)),
            "diastolic": int(rng.normal(80, 5)),
            "measured_at": day.isoformat() + "T07:00:00Z",
        }))
    # Weight — weekly (~ daily_mean = per_week/7)
    for _ in range(_poisson_count(rng, (ap.weight_per_week / 7) * scale)):
        events.append(("weight", {
            "value": round(float(rng.normal(165, 8)), 1),
            "measured_at": day.isoformat() + "T07:30:00Z",
        }))
    # Stack logs — daily
    for _ in range(_poisson_count(rng, ap.stack_logs_per_day * scale)):
        events.append(("log_stack", {
            "logged_at": day.isoformat() + "T08:00:00Z",
        }))
    # Meal logs — daily
    for _ in range(_poisson_count(rng, ap.meal_logs_per_day * scale)):
        events.append(("log_meal", {
            "logged_at": day.isoformat() + "T12:30:00Z",
        }))
    # Observations — weekly mean
    for _ in range(_poisson_count(rng, (ap.observations_per_week / 7) * scale)):
        events.append(("observation", {
            "kind": "general",
            "text": "Routine self-observation.",
            "observed_at": day.isoformat() + "T20:00:00Z",
        }))
    # Layer narrative beats. Each beat that matches today fires (unscaled).
    for beat in persona.narrative_beats:
        if beat.when != day:
            continue
        events.append((beat.kind, {"day": day.isoformat(),
                                   "payload": list(beat.payload)}))
    return events

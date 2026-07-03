"""Persona dataclass — frozen, hashable, declarative."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class ActivityProfile:
    """Per-day event-count density profile.

    Counts are interpreted as the *mean* daily count; the timeline driver
    may add small jitter via the persona's RNG.
    """
    bp_per_day: float = 0.0
    weight_per_week: float = 0.0
    stack_logs_per_day: float = 0.0
    meal_logs_per_day: float = 0.0
    observations_per_week: float = 0.0


@dataclass(frozen=True)
class NarrativeBeat:
    """A scripted event in the persona's timeline."""
    when: date
    kind: str           # "injury_day", "migraine_episode", "missed_dose", etc.
    payload: tuple      # frozen tuple of beat-specific fields


@dataclass(frozen=True)
class Persona:
    user_id: str
    email: str
    display_name: str
    activity_profile: ActivityProfile
    narrative_beats: tuple[NarrativeBeat, ...] = field(default_factory=tuple)
    scaffolding_refs: dict = field(default_factory=dict)

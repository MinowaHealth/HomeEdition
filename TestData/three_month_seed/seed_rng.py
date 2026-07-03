"""Deterministic RNG factory keyed by (seed, persona_id, source_kind, [day]).

Changing one persona's profile doesn't cascade-invalidate other personas.
Uses numpy's PCG64 via SeedSequence for high-quality, reproducible streams.

If `day` is provided, each day gets its own RNG stream — required for
per-day variation in the activity loop. If omitted, the stream is fixed
across days (legacy behavior — useful only for tests that compare two
streams).
"""
from __future__ import annotations

import hashlib
from datetime import date

import numpy as np


def _key_to_int(persona_id: str, source_kind: str) -> int:
    """Stable 64-bit hash of (persona_id, source_kind) for SeedSequence spawning."""
    h = hashlib.blake2b(
        f"{persona_id}|{source_kind}".encode(), digest_size=8
    ).digest()
    return int.from_bytes(h, "big")


def rng_for(
    seed: int,
    persona_id: str,
    source_kind: str,
    day: date | None = None,
) -> np.random.Generator:
    """Return a numpy Generator keyed by (seed, persona_id, source_kind, day).

    Pass `day` to vary the stream per day — the seeder's activity loop
    must do this, otherwise every day produces the identical Poisson
    sequence and 90 days collapse to 1 day repeated.
    """
    key = _key_to_int(persona_id, source_kind)
    entropy: list[int] = [seed, key]
    if day is not None:
        entropy.append(day.toordinal())
    ss = np.random.SeedSequence(entropy)
    return np.random.Generator(np.random.PCG64(ss))

"""Canonical ordered tuple of the six Borgia household personas."""
from __future__ import annotations
from .base import Persona
from .borgia import BORGIA

REGISTRY: tuple[Persona, ...] = BORGIA
_BY_ID: dict[str, Persona] = {p.user_id: p for p in REGISTRY}

def get_by_id(user_id: str) -> Persona:
    return _BY_ID[user_id]

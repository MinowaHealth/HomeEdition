"""Shared embedding utilities for the Minowa.ai vector pipeline.

Provides server-side embedding via Ollama (nomic-embed-text-v2-moe, 768 dimensions),
table configuration for embedding-enabled tables, and pgvector helpers.

Used by:
- routes/embeddings.py (sync-embeddings, semantic-search endpoints)
- routes/vitals.py (inline embedding on observation create/update)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text-v2-moe:latest")
EMBEDDING_DIMENSIONS = 768
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "10"))
# Total deadline for get_embedding across all endpoint attempts. CPU-only
# MoE inference for short strings typically lands in a few seconds; 20s
# leaves generous headroom for annotation writes without hanging requests
# indefinitely if Ollama is unreachable.
EMBEDDING_DEADLINE = float(os.getenv("EMBEDDING_DEADLINE", "20"))


# ---------------------------------------------------------------------------
# Ollama endpoint
# ---------------------------------------------------------------------------

def _get_ollama_endpoints() -> list[str]:
    """Return Ollama endpoint(s) to try.

    Every host in the fleet runs Ollama on port 11434. From inside a
    container the host is reachable via host.docker.internal (requires
    `extra_hosts: host.docker.internal:host-gateway` on Linux).
    """
    return [os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")]


# ---------------------------------------------------------------------------
# Table whitelist — central registry of embedding-enabled tables
# ---------------------------------------------------------------------------

EMBEDDING_TABLES = {
    # Tier 1: High-value semantic search + RAG
    "health_observations": {
        "text_column": "content",
        "embed_column": "embedding_content",
        "content_builder": None,
        "timestamp_column": "observed_at",
        "display_column": "content",
    },
    "document_annotations": {
        "text_column": "body",
        "embed_column": "embedding_body",
        "content_builder": None,
        "timestamp_column": "created_at",
        "display_column": "body",
    },
    # Tier 2: Dedup and smart matching
    "health_food_itemsv2": {
        "text_column": "name",
        "embed_column": "embedding_name",
        "content_builder": None,
        "timestamp_column": "created_at",
        "display_column": "name",
    },
    "health_inputs": {
        "text_column": "name",
        "embed_column": "embedding_name",
        "content_builder": None,
        "timestamp_column": "created_at",
        "display_column": "name",
    },
    "health_allergies": {
        "text_column": None,  # multi-column
        "embed_column": "embedding_allergy_full",
        "content_builder": "allergy_to_text",
        "timestamp_column": "created_at",
        "display_column": "allergen",
    },
    "health_conditions": {
        "text_column": None,  # multi-column
        "embed_column": "embedding_condition",
        "content_builder": "condition_to_text",
        "timestamp_column": "created_at",
        "display_column": "name",
    },
    # Tier 3 (operational analytics + semantic search)
    "mobile_events": {
        "text_column": "event_text",
        "embed_column": "embedding_event_text",
        "content_builder": None,
        "timestamp_column": "created_at",
        "display_column": "event_text",
    },
}

# Default tables for semantic-search when 'tables' param is omitted
TIER1_TABLES = [
    "health_observations",
]


# ---------------------------------------------------------------------------
# Core embedding function
# ---------------------------------------------------------------------------

def get_embedding(text: str) -> list[float] | None:
    """Generate a 768-dim embedding via Ollama (synchronous).

    Calls the configured Ollama endpoint. Returns None on failure — caller
    stores NULL in the embedding column.

    Args:
        text: The text to embed. Must be non-empty.

    Returns:
        List of 768 floats, or None if the endpoint fails.
    """
    if not text or not text.strip():
        return None

    import time
    deadline = time.monotonic() + EMBEDDING_DEADLINE

    for endpoint in _get_ollama_endpoints():
        if time.monotonic() > deadline:
            logger.warning("embedding_deadline_exceeded total=%.1fs", EMBEDDING_DEADLINE)
            return None
        remaining = max(0.5, deadline - time.monotonic())
        try:
            resp = httpx.post(
                f"{endpoint}/api/embeddings",
                json={"model": EMBEDDING_MODEL, "prompt": text.strip()},
                timeout=min(OLLAMA_TIMEOUT, remaining),
            )
            resp.raise_for_status()
            embedding = resp.json().get("embedding")
            if embedding and len(embedding) == EMBEDDING_DIMENSIONS:
                return embedding
            logger.warning(
                "embedding_bad_dimensions endpoint=%s len=%s",
                endpoint, len(embedding) if embedding else 0,
            )
        except httpx.HTTPError as exc:
            logger.warning("embedding_failed endpoint=%s error=%s", endpoint, exc)
            continue
    return None


# ---------------------------------------------------------------------------
# Text builders for multi-column tables
# ---------------------------------------------------------------------------

def allergy_to_text(row: dict) -> str:
    """Build embeddable text from allergy record.

    Concatenates allergen, reaction, and notes fields.
    """
    parts = []
    if row.get("allergen"):
        parts.append(row["allergen"])
    if row.get("reaction"):
        parts.append(f"Reaction: {row['reaction']}")
    if row.get("notes"):
        parts.append(row["notes"])
    return ". ".join(parts)


def condition_to_text(row: dict) -> str:
    """Build embeddable text from condition record.

    Concatenates condition name and description.
    """
    parts = []
    if row.get("name"):
        parts.append(row["name"])
    if row.get("description"):
        parts.append(row["description"])
    return ". ".join(parts)


# Map content_builder names to functions
CONTENT_BUILDERS = {
    "allergy_to_text": allergy_to_text,
    "condition_to_text": condition_to_text,
}


# ---------------------------------------------------------------------------
# Validation and hashing
# ---------------------------------------------------------------------------

def validate_embedding_vector(
    embedding: list[Any], expected_dim: int = EMBEDDING_DIMENSIONS
) -> bool:
    """Validate that an embedding vector has the correct shape and values."""
    if not isinstance(embedding, list):
        return False
    if len(embedding) != expected_dim:
        return False
    return all(isinstance(v, (int, float)) for v in embedding)


def compute_text_hash(text: str) -> str:
    """SHA-256 hash of normalized text for staleness detection."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# pgvector connection helpers
# ---------------------------------------------------------------------------

def register_pgvector(conn: Any) -> None:
    """Register pgvector type with the active driver's connection.

    Call once per connection. Safe to call multiple times.
    Must be called BEFORE any queries that read/write VECTOR columns.
    """
    import db_driver
    db_driver.register_pgvector(conn)


def set_ivfflat_probes(conn: Any, probes: int = 10) -> None:
    """Set IVFFlat probes for the current session.

    Per EmbeddingDesign.md Section 5: probes = sqrt(lists).
    Default 10 matches lists=100 for alpha.

    Uses set_config so the integer parameter binds under psycopg3's
    extended-query protocol.
    """
    import db_driver
    cur = conn.cursor()
    db_driver.set_session_var(cur, "ivfflat.probes", str(probes))
    cur.close()

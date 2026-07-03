"""Batched embedding fill via live get_embedding().

Walks 9 embedding-bearing (table, text_col, embed_col) entries, collects
unique source strings, calls get_embedding() once per unique string, then
batch-UPDATEs by primary key.

Column names are verified against 02-healthv10_schema.sql — the schema is
canonical. The plan's original EMBEDDING_COLUMNS had incorrect names
throughout; this file uses the actual schema names.

This module runs as an admin seeder against single-household test data
only; embeddings are derived from already-present seed rows.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterable

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))


def get_embedding(text: str) -> list[float] | None:
    """Lazy proxy to UserApp.webapp.embedding_utils.get_embedding.

    UserApp's module pulls in transitive deps (httpx, etc.) the seeder
    doesn't need for --verify-only or --no-embeddings paths. Importing on
    first call defers the cost. Monkeypatch-friendly: tests replace this
    module attribute directly.
    """
    from UserApp.webapp.embedding_utils import get_embedding as _live
    return _live(text)

# (table, pk_col, text_col, embedding_col) — 9 entries verified against
# Infrastructure/init/docker-init-v10/02-healthv10_schema.sql.
#
# Schema drift from plan's original list:
#   health_inputs.name_embedding       → embedding_name
#   health_inputs.description_embedding→ (no desc embedding col; swapped for soap_objective)
#   health_conditions.name_embedding   → embedding_condition   / name col unchanged
#   health_allergies.allergen_embedding→ embedding_allergy_full / allergen col unchanged
#   health_observations.text           → content (col name); text_embedding → embedding_content
EMBEDDING_COLUMNS: tuple[tuple[str, str, str, str], ...] = (
    ("health_observations",    "id", "content",        "embedding_content"),
    ("document_annotations",   "id", "body",            "embedding_body"),
    ("health_food_itemsv2",    "id", "name",            "embedding_name"),
    ("health_inputs",          "id", "name",            "embedding_name"),
    ("mobile_events",          "id", "event_text",      "embedding_event_text"),
    ("documents",              "id", "title",           "embedding_content"),
    ("health_allergies",       "id", "allergen",        "embedding_allergy_full"),
    ("health_conditions",      "id", "name",            "embedding_condition"),
)


def dedupe_strings(rows: Iterable[tuple]) -> list[str]:
    """Return unique non-empty source strings from (pk, text) rows."""
    seen: set[str] = set()
    out: list[str] = []
    for _pk, text in rows:
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def fill_embeddings(conn: Any) -> dict[str, int]:
    """Walk EMBEDDING_COLUMNS, fill NULL embeddings. Return per-column counts.

    Args:
        conn: psycopg 3 connection (admin). Caller is responsible
              for registering the pgvector adapter (register_vector / register_pgvector)
              on this connection before calling — Task 13 (CLI main) wires this.
              The test uses MagicMock so the adapter is not exercised here.

    Returns:
        Dict mapping "table.embed_col" → row count processed.

    Raises:
        RuntimeError: if get_embedding returns None for any text (Ollama unreachable).
    """
    counts: dict[str, int] = {}
    cache: dict[str, list[float]] = {}

    for table, pk_col, text_col, vec_col in EMBEDDING_COLUMNS:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {pk_col}, {text_col} FROM {table} "
                f"WHERE {vec_col} IS NULL AND {text_col} IS NOT NULL"
            )
            rows = cur.fetchall()

        unique_texts = dedupe_strings(rows)
        for text in unique_texts:
            if text not in cache:
                vec = get_embedding(text)
                if vec is None:
                    raise RuntimeError(
                        f"get_embedding returned None for text in {table}.{text_col}"
                    )
                cache[text] = vec

        with conn.cursor() as cur:
            for batch_start in range(0, len(rows), 100):
                batch = rows[batch_start : batch_start + 100]
                for pk, text in batch:
                    if not text:
                        continue
                    cur.execute(
                        f"UPDATE {table} SET {vec_col} = %s WHERE {pk_col} = %s",
                        (cache[text], pk),
                    )
        counts[f"{table}.{vec_col}"] = len(rows)
    return counts

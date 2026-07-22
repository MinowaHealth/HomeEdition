"""Tests for scripts/backfill_embeddings.py (loaded by path — it lives outside webapp)."""
import importlib.util
import pathlib
from unittest.mock import MagicMock

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "backfill_embeddings.py"
_spec = importlib.util.spec_from_file_location("backfill_embeddings", _SCRIPT)
bf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bf)

USER = "11111111-1111-1111-1111-111111111111"


def _conn_with_rows(rows):
    cur = MagicMock()
    cur.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def test_source_text_text_column_and_builder():
    cfg = {"content_builder": None, "text_column": "name"}
    assert bf.source_text("health_inputs", cfg, {"name": "Metformin"}) == "Metformin"
    assert bf.source_text("health_inputs", cfg, {"name": None}) == ""
    built = bf.source_text("health_allergies", {"content_builder": "allergy_to_text"},
                           {"allergen": "Peanuts", "reaction": "Hives", "notes": None})
    assert "Peanuts" in built and "Hives" in built


def test_source_text_adapts_stale_builder_shapes():
    cond = bf.source_text("health_conditions", {"content_builder": "condition_to_text"},
                          {"name": "Hypertension", "notes": "Stage 1"})
    assert cond == "Hypertension. Stage 1"


def test_builder_cols_match_registry():
    for table in bf.BUILDER_COLS:
        assert bf.EMBEDDING_TABLES[table]["content_builder"] is not None


def test_backfill_dedupes_identical_texts(monkeypatch):
    calls = []
    monkeypatch.setattr(bf, "get_embedding", lambda t: calls.append(t) or [0.1] * 768)
    rows = [
        {"tenant_id": 1, "id": "a", "name": "Metformin"},
        {"tenant_id": 1, "id": "b", "name": "Metformin"},
        {"tenant_id": 1, "id": "c", "name": ""},
    ]
    conn, cur = _conn_with_rows(rows)
    cfg = bf.EMBEDDING_TABLES["health_inputs"]
    n, filled, skipped, failed = bf.backfill_table(
        conn, "health_inputs", cfg, {}, False, USER, 1)
    assert (n, filled, skipped, failed) == (3, 2, 1, 0)
    assert calls == ["Metformin"]  # embedded once, written twice
    updates = [c for c in cur.execute.call_args_list if "UPDATE" in str(c.args[0])]
    assert len(updates) == 2
    # Every write carries explicit tenant/user scoping (household model)
    for c in updates:
        assert c.args[1][1:3] == (1, USER)
    assert conn.commit.called


def test_backfill_failed_embedding_counts_and_continues(monkeypatch):
    monkeypatch.setattr(bf, "get_embedding", lambda t: None)
    conn, _ = _conn_with_rows([{"tenant_id": 1, "id": "a", "name": "X"}])
    cfg = bf.EMBEDDING_TABLES["health_inputs"]
    n, filled, skipped, failed = bf.backfill_table(
        conn, "health_inputs", cfg, {}, False, USER, 1)
    assert (n, filled, skipped, failed) == (1, 0, 0, 1)


def test_dry_run_makes_no_embedding_calls(monkeypatch):
    boom = MagicMock(side_effect=AssertionError("no Ollama calls in dry-run"))
    monkeypatch.setattr(bf, "get_embedding", boom)
    conn, cur = _conn_with_rows([{"tenant_id": 1, "id": "a", "name": "X"}])
    cfg = bf.EMBEDDING_TABLES["health_inputs"]
    n, filled, skipped, failed = bf.backfill_table(
        conn, "health_inputs", cfg, {}, True, USER, 1)
    assert (n, filled, skipped, failed) == (1, 1, 0, 0)
    assert not conn.commit.called

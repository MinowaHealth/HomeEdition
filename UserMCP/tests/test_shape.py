"""Tests for tools/_shape.py — response-shape narrowing helpers.

The helpers exist to defend the MCP↔UserApp trust boundary against
unexpected response shapes while emitting an audit log that doubles as
a chess-problem signal (if `api_shape_drift` never appears in 30 days
of production traffic, the defense is provably dead).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools._shape import as_dict, as_list


def test_as_dict_returns_value_when_dict():
    value = {"a": 1, "b": 2}
    assert as_dict(value, where="test") is value


def test_as_dict_returns_empty_when_not_dict(caplog):
    with caplog.at_level(logging.WARNING):
        result = as_dict([1, 2, 3], where="test.list_input")
    assert result == {}
    assert any(
        "api_shape_drift" in r.message and "test.list_input" in r.message
        for r in caplog.records
    )


def test_as_dict_returns_empty_on_none():
    assert as_dict(None, where="test.none") == {}


def test_as_list_returns_value_when_list():
    value = [1, 2, 3]
    assert as_list(value, where="test") is value


def test_as_list_returns_empty_when_not_list(caplog):
    with caplog.at_level(logging.WARNING):
        result = as_list({"keys": "wrong"}, where="test.dict_input")
    assert result == []
    assert any(
        "api_shape_drift" in r.message and "test.dict_input" in r.message
        for r in caplog.records
    )


def test_as_list_returns_empty_on_none():
    assert as_list(None, where="test.none") == []


def test_where_label_appears_in_log(caplog):
    with caplog.at_level(logging.WARNING):
        as_dict("scalar", where="caller.specific.path")
    assert any(
        "caller.specific.path" in r.message and "expected=dict" in r.message and "got=str" in r.message
        for r in caplog.records
    )

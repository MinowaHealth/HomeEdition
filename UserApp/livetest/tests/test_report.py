"""Tests for livetest.report."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from livetest.report import print_live, write_markdown
from livetest.runner import FlowResult, StepResult


@dataclass
class _FakeCfg:
    run_id: str
    base_url: str
    report_dir: Path


def _make_result(flow_name: str, steps: list[StepResult]) -> FlowResult:
    now = datetime(2026, 4, 10, 22, 0, 0)
    return FlowResult(
        flow=flow_name,
        steps=steps,
        started_at=now,
        finished_at=now,
    )


def test_write_markdown_single_flow_pass(tmp_path: Path):
    cfg = _FakeCfg(
        run_id="abc12345",
        base_url="https://x.example.com",
        report_dir=tmp_path,
    )
    result = _make_result("food_items", [
        StepResult("count rows before", "pass", "", 3),
        StepResult("POST /food-items", "pass", "id=xyz", 45),
        StepResult("verify row exists", "pass", "delta=1", 8),
    ])
    path = write_markdown([result], cfg)
    assert path.exists()
    content = path.read_text()
    assert "# Live Test Report" in content
    assert "abc12345" in content
    assert "https://x.example.com" in content
    assert "food_items" in content
    assert "1/1" in content or "1 / 1" in content
    assert "count rows before" in content
    assert "POST /food-items" in content
    assert "delta=1" in content


def test_write_markdown_mixed_pass_fail(tmp_path: Path):
    cfg = _FakeCfg(
        run_id="def67890",
        base_url="https://x.example.com",
        report_dir=tmp_path,
    )
    r1 = _make_result("food_items", [StepResult("a", "pass")])
    r2 = _make_result("meals", [
        StepResult("a", "pass"),
        StepResult("b", "fail", "delta 0, expected 1", 12),
    ])
    path = write_markdown([r1, r2], cfg)
    content = path.read_text()
    assert "1/2" in content or "1 / 2" in content
    assert "delta 0, expected 1" in content
    assert "✗" in content or "fail" in content.lower()


def test_write_markdown_filename_uses_run_id(tmp_path: Path):
    cfg = _FakeCfg(
        run_id="run1test",
        base_url="https://x",
        report_dir=tmp_path,
    )
    result = _make_result("flow", [StepResult("s", "pass")])
    path = write_markdown([result], cfg)
    assert path.name == "livetest-report-run1test.md"


def test_write_markdown_creates_report_dir_if_missing(tmp_path: Path):
    cfg = _FakeCfg(
        run_id="mkdir01",
        base_url="https://x",
        report_dir=tmp_path / "nested" / "reports",
    )
    result = _make_result("flow", [StepResult("s", "pass")])
    path = write_markdown([result], cfg)
    assert path.exists()
    assert path.parent.name == "reports"


def test_print_live_prints_flow_name_and_steps(capsys):
    result = _make_result("food_items", [
        StepResult("step one", "pass", "ok", 5),
        StepResult("step two", "fail", "boom", 10),
    ])
    print_live(result)
    out = capsys.readouterr().out
    assert "food_items" in out
    assert "step one" in out
    assert "step two" in out
    assert "boom" in out

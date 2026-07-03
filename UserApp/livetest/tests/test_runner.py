"""Tests for livetest.runner — Flow, StepResult, FlowResult."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from livetest.runner import Flow, FlowResult, StepResult


class _DummyFlow(Flow):
    name = "dummy"

    def __init__(self):
        # Bypass the usual __init__ to keep the test hermetic.
        self._cfg = MagicMock()
        self._session = MagicMock()
        self._conn = MagicMock()
        self._user_id = "00000000-0000-0000-0000-000000000000"
        self._step_results: list[StepResult] = []
        self._started_at = None
        self._finished_at = None

    # Expose internals for tests
    @property
    def cfg(self): return self._cfg
    @property
    def session(self): return self._session
    @property
    def conn(self): return self._conn
    @property
    def user_id(self): return self._user_id


def test_step_result_dataclass():
    r = StepResult(name="x", status="pass", message="ok", duration_ms=42)
    assert r.name == "x"
    assert r.status == "pass"
    assert r.message == "ok"
    assert r.duration_ms == 42


def test_flow_result_status_all_pass():
    from datetime import datetime
    now = datetime.now()
    r = FlowResult(
        flow="dummy",
        steps=[StepResult("a", "pass"), StepResult("b", "pass")],
        started_at=now, finished_at=now,
    )
    assert r.status == "pass"


def test_flow_result_status_any_fail():
    from datetime import datetime
    now = datetime.now()
    r = FlowResult(
        flow="dummy",
        steps=[StepResult("a", "pass"), StepResult("b", "fail", "boom")],
        started_at=now, finished_at=now,
    )
    assert r.status == "fail"


def test_flow_step_success_captures_timing():
    f = _DummyFlow()
    with f.step("do thing"):
        pass  # no exception
    assert len(f._step_results) == 1
    sr = f._step_results[0]
    assert sr.name == "do thing"
    assert sr.status == "pass"
    assert sr.duration_ms >= 0


def test_flow_step_captures_assertion_error():
    f = _DummyFlow()
    with f.step("do thing"):
        assert False, "expected failure"
    assert len(f._step_results) == 1
    sr = f._step_results[0]
    assert sr.status == "fail"
    assert "expected failure" in sr.message


def test_flow_step_captures_generic_exception():
    f = _DummyFlow()
    with f.step("do thing"):
        raise RuntimeError("kaboom")
    sr = f._step_results[0]
    assert sr.status == "fail"
    assert "kaboom" in sr.message


def test_flow_step_continues_after_failure():
    """Step failures MUST NOT halt the flow — subsequent steps run."""
    f = _DummyFlow()
    with f.step("first"):
        assert False, "fail the first one"
    with f.step("second"):
        pass  # this must still execute
    assert len(f._step_results) == 2
    assert f._step_results[0].status == "fail"
    assert f._step_results[1].status == "pass"


def test_flow_result_builds_from_accumulated_steps():
    f = _DummyFlow()
    with f.step("a"):
        pass
    with f.step("b"):
        assert False, "nope"
    result = f.result()
    assert isinstance(result, FlowResult)
    assert result.flow == "dummy"
    assert len(result.steps) == 2
    assert result.status == "fail"


def test_flow_result_from_exception_classmethod():
    try:
        raise ValueError("init crashed")
    except ValueError as e:
        r = FlowResult.from_exception("broken_flow", e)
    assert r.flow == "broken_flow"
    assert len(r.steps) == 1
    assert r.steps[0].status == "fail"
    assert "init crashed" in r.steps[0].message
    assert r.status == "fail"

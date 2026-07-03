"""Flow base class, StepResult, FlowResult for the live test harness."""
from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator, Literal


# `xfail` and `xpass` mirror pytest semantics, added 2026-04-29 for Track 4a
# of SecurityHardening.md (security-defense flows where the underlying defect is
# tracked but not yet fixed). When a step is opened with `xfail="reason"`:
#   * AssertionError → status "xfail" (counts as pass for run summary).
#   * step body completes without error → status "xpass" (counts as fail —
#     the defense is now in place; the step's `xfail=` argument should be
#     removed in the same PR that fixes the defect).
Status = Literal["pass", "fail", "skip", "xfail", "xpass"]


@dataclass
class StepResult:
    name: str
    status: Status
    message: str = ""
    duration_ms: int = 0


@dataclass
class FlowResult:
    flow: str
    steps: list[StepResult]
    started_at: datetime
    finished_at: datetime

    @property
    def status(self) -> Literal["pass", "fail"]:
        # xfail counts as pass; xpass counts as fail (the defect is now
        # fixed and the test marker should be removed — strict-xpass).
        if any(s.status in ("fail", "xpass") for s in self.steps):
            return "fail"
        return "pass"

    @classmethod
    def from_exception(cls, flow_name: str, exc: Exception) -> "FlowResult":
        """Build a FlowResult for a flow that crashed before run() completed."""
        now = datetime.now()
        msg = f"{type(exc).__name__}: {exc}"
        return cls(
            flow=flow_name,
            steps=[StepResult(name="<init>", status="fail", message=msg)],
            started_at=now,
            finished_at=now,
        )


class Flow:
    """Base class for per-flow live tests.

    Subclasses set ``name`` and implement ``run()`` as a sequence of
    ``with self.step("name"): ...`` blocks. Step failures do NOT halt
    the flow — they are captured into the step_results list and
    subsequent steps still execute. The final FlowResult is built from
    the accumulated step results.
    """

    name: str = "<unnamed>"

    def __init__(self, cfg: Any, session: Any, conn: Any, user_id: str):
        self._cfg = cfg
        self._session = session
        self._conn = conn
        self._user_id = user_id
        self._step_results: list[StepResult] = []
        self._started_at: datetime | None = None
        self._finished_at: datetime | None = None

    @property
    def cfg(self) -> Any: return self._cfg

    @property
    def session(self) -> Any: return self._session

    @property
    def conn(self) -> Any: return self._conn

    @property
    def user_id(self) -> str: return self._user_id

    @contextmanager
    def step(self, name: str, *, xfail: str | None = None) -> Iterator[None]:
        """Context manager that captures timing and exceptions into a
        StepResult. Assertion errors and unexpected exceptions become
        status='fail' with the exception message; success becomes
        status='pass'. Control always returns to the caller.

        ``xfail`` marks an expected-failure step. Pass a short reason
        (e.g. ``xfail="F1 — CSRFProtect not yet registered"``); the
        runner will:
          * record status 'xfail' if the step body raises AssertionError
            (defect still present — counts as pass for the run);
          * record status 'xpass' if the step body succeeds (defect is
            fixed — counts as fail to force removing the marker).
        Non-Assertion exceptions still become 'fail' regardless of
        the xfail marker — those are bugs in the test or harness, not
        the system-under-test.
        """
        if self._started_at is None:
            self._started_at = datetime.now()
        start = time.perf_counter()
        status: Status = "pass"
        message = ""
        try:
            yield
        except AssertionError as e:
            assertion_msg = str(e) if str(e) else "assertion failed"
            if xfail is not None:
                status = "xfail"
                message = f"xfail ({xfail}): {assertion_msg}"
            else:
                status = "fail"
                message = assertion_msg
        except Exception as e:  # noqa: BLE001 — intentional catch-all
            status = "fail"
            message = f"{type(e).__name__}: {e}"
        else:
            if xfail is not None:
                status = "xpass"
                message = (
                    f"xpass — step succeeded but was marked xfail "
                    f"({xfail}). Remove the xfail= argument; the defense "
                    f"is now in place."
                )
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)
            self._step_results.append(
                StepResult(
                    name=name,
                    status=status,
                    message=message,
                    duration_ms=duration_ms,
                )
            )

    def result(self) -> FlowResult:
        """Assemble the accumulated StepResults into a FlowResult."""
        now = datetime.now()
        return FlowResult(
            flow=self.name,
            steps=list(self._step_results),
            started_at=self._started_at or now,
            finished_at=now,
        )

    def run(self) -> FlowResult:
        """Override in subclasses."""
        raise NotImplementedError

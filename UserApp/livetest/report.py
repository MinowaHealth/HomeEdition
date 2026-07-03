"""Live test reporting: stdout formatter + markdown writer."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from livetest.runner import FlowResult, StepResult


_STATUS_MARK = {
    "pass": "✓",
    "fail": "✗",
    "skip": "-",
    "xfail": "x",   # expected failure — defect tracked, not yet fixed
    "xpass": "X",   # unexpectedly passed — flip the assertion
}


def _colorize(s: str, color_code: str) -> str:
    if not sys.stdout.isatty():
        return s
    return f"\033[{color_code}m{s}\033[0m"


def print_live(result: FlowResult) -> None:
    """Stream a flow's step results to stdout. Colorized if tty."""
    header = f"\n[{result.flow}]"
    print(_colorize(header, "1;34"))  # bold blue
    for step in result.steps:
        mark = _STATUS_MARK.get(step.status, "?")
        if step.status == "pass":
            color = "32"  # green
        elif step.status in ("fail", "xpass"):
            color = "31"  # red — xpass means the marker should be removed
        else:
            color = "33"  # yellow — skip / xfail (informational, not failing)
        marker = _colorize(mark, color)
        line = f"  {marker} {step.name} ({step.duration_ms} ms)"
        if step.message:
            line += f" — {step.message}"
        print(line)
    summary = f"  → {result.status.upper()}"
    print(_colorize(summary, "32" if result.status == "pass" else "31"))


def _render_summary_table(results: list[FlowResult]) -> str:
    rows = ["| Flow | Status | Steps | Duration |", "|---|---|---|---|"]
    for r in results:
        passed = sum(1 for s in r.steps if s.status == "pass")
        xfailed = sum(1 for s in r.steps if s.status == "xfail")
        total = len(r.steps)
        total_ms = sum(s.duration_ms for s in r.steps)
        mark = _STATUS_MARK[r.status]
        # Show xfailed in the per-flow row when any step xfailed, so the
        # summary makes the F1/F4/F5 ratchets visible without forcing a
        # reader into the per-flow detail tables.
        if xfailed:
            cell = f"{passed}/{total} (+{xfailed} xfail)"
        else:
            cell = f"{passed}/{total}"
        rows.append(
            f"| {r.flow} | {mark} {r.status} | {cell} | {total_ms} ms |"
        )
    return "\n".join(rows)


def _render_flow_detail(result: FlowResult) -> str:
    lines = [f"## {result.flow}", "", "| Step | Status | Duration | Message |",
             "|---|---|---|---|"]
    for step in result.steps:
        mark = _STATUS_MARK.get(step.status, "?")
        msg = step.message.replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {step.name} | {mark} {step.status} | {step.duration_ms} ms | {msg} |"
        )
    return "\n".join(lines)


def write_markdown(results: list[FlowResult], cfg: Any) -> Path:
    """Write a markdown report to cfg.report_dir/livetest-report-{run_id}.md.

    Returns the path written. Creates the report directory if missing.
    """
    report_dir: Path = cfg.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)

    total = len(results)
    passed = sum(1 for r in results if r.status == "pass")
    total_ms = sum(sum(s.duration_ms for s in r.steps) for r in results)
    started = min((r.started_at for r in results), default=None)

    lines = [
        f"# Live Test Report — {cfg.run_id}",
        "",
        f"**Target:** {cfg.base_url}",
        f"**Started:** {started.isoformat() if started else 'unknown'}",
        f"**Duration:** {total_ms} ms",
        f"**Result:** {passed}/{total} flows passed",
        "",
        "## Summary",
        "",
        _render_summary_table(results),
        "",
    ]
    for r in results:
        lines.append(_render_flow_detail(r))
        lines.append("")

    path = report_dir / f"livetest-report-{cfg.run_id}.md"
    path.write_text("\n".join(lines))
    return path

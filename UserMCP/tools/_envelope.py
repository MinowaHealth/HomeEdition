"""
Standard MCP response envelope.

Every task-oriented tool wraps its payload through `build_envelope()` so the
response shape is identical regardless of which tool answered. An LLM reading
a response never has to guess whether a field exists — it does, even if
empty — and can use `coverage`, `sources`, and `disclaimer` as a consistent
contract for "what did we actually get, and is it trustworthy?".

Envelope contract (returned as a plain dict; serialized by the caller):

    {
      "data":       { tool-specific payload },
      "coverage":   { window, counts, gaps, truncated },
      "sources":    [ { name, last_sync, record_count }, ... ],
      "disclaimer": "Informational only — not medical advice. ...",
      "next_actions": [ { tool, args, why }, ... ]
    }

`build_envelope()` takes an already-shaped `coverage` block and an
already-fetched `sources` list — it does not derive them. Keeping derivation
in the caller keeps this module stateless and trivially testable.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Union

DateLike = Union[str, date, datetime]


# Short, one-sentence disclaimer — the long form lives in the
# usermcp://disclaimers resource. Changing this text requires a review
# because it gets embedded in every tool response.
DEFAULT_DISCLAIMER = (
    "Informational only — not medical advice. "
    "Discuss changes with your provider."
)


def build_envelope(
    data: Any,
    *,
    coverage: Optional[Dict[str, Any]] = None,
    sources: Optional[List[Dict[str, Any]]] = None,
    next_actions: Optional[List[Dict[str, Any]]] = None,
    disclaimer: Optional[str] = None,
) -> Dict[str, Any]:
    """Wrap `data` in the standard envelope.

    All sub-blocks are optional but always present in the returned dict —
    omitting them yields `{}` or `[]` rather than a missing key, so callers
    never need existence checks. Callers typically build `coverage` via
    `coverage_from_rows()` and `sources` via `tools._sources.fetch_sources()`.
    """
    return {
        "data": data,
        "coverage": coverage or {},
        "sources": sources or [],
        "disclaimer": disclaimer or DEFAULT_DISCLAIMER,
        "next_actions": next_actions or [],
    }


def _as_date(value: DateLike) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    # assume ISO string
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()


def window_block(
    from_: DateLike,
    to: DateLike,
) -> Dict[str, Any]:
    """Build the standard `coverage.window` sub-block.

    Both endpoints are inclusive. `days` is the closed-interval count so
    `from==to` reports 1 day rather than 0 — matches how users describe
    "today" as one day of data.
    """
    f = _as_date(from_)
    t = _as_date(to)
    return {
        "from": f.isoformat(),
        "to": t.isoformat(),
        "days": (t - f).days + 1,
    }


def coverage_from_rows(
    rows: Iterable[Dict[str, Any]],
    *,
    window: Optional[Dict[str, Any]] = None,
    timestamp_field: str = "timestamp",
    source_field: Optional[str] = "source",
    truncated: bool = False,
    gap_threshold_days: int = 2,
) -> Dict[str, Any]:
    """Compute a `coverage` block from a list of rows.

    - `counts.rows` is `len(rows)`.
    - `counts.sources_represented` is the set of distinct non-null values of
      `source_field` (or empty if `source_field` is None).
    - `gaps` is populated from the `window` endpoints plus the sorted
      timestamps in rows: any stretch ≥ `gap_threshold_days` between
      consecutive rows, or between `window.from` and the first row, or
      between the last row and `window.to`, is reported. Pass `window=None`
      to skip front/back edge gaps.
    - `truncated` is a passthrough flag the caller sets when the underlying
      fetch hit a cap.
    """
    row_list = list(rows or [])
    count = len(row_list)

    sources_represented: List[str] = []
    if source_field:
        seen = {
            r.get(source_field)
            for r in row_list
            if r.get(source_field) is not None
        }
        sources_represented = sorted(s for s in seen if isinstance(s, str))

    timestamps: List[date] = []
    for r in row_list:
        ts = r.get(timestamp_field)
        if ts is None:
            continue
        try:
            timestamps.append(_as_date(ts))
        except (TypeError, ValueError):
            continue
    timestamps.sort()

    gaps: List[Dict[str, Any]] = []
    if window and timestamps:
        win_from = _as_date(window["from"])
        win_to = _as_date(window["to"])
        if (timestamps[0] - win_from).days >= gap_threshold_days:
            gaps.append({
                "from": win_from.isoformat(),
                "to": (timestamps[0] - timedelta(days=1)).isoformat(),
                "reason": "no data before first row",
            })
        if (win_to - timestamps[-1]).days >= gap_threshold_days:
            gaps.append({
                "from": (timestamps[-1] + timedelta(days=1)).isoformat(),
                "to": win_to.isoformat(),
                "reason": "no data after last row",
            })
    for i in range(1, len(timestamps)):
        delta = (timestamps[i] - timestamps[i - 1]).days
        if delta >= gap_threshold_days + 1:
            gaps.append({
                "from": (timestamps[i - 1] + timedelta(days=1)).isoformat(),
                "to": (timestamps[i] - timedelta(days=1)).isoformat(),
                "reason": "no data between readings",
            })

    coverage: Dict[str, Any] = {
        "counts": {
            "rows": count,
            "sources_represented": sources_represented,
        },
        "gaps": gaps,
        "truncated": bool(truncated),
    }
    if window:
        coverage["window"] = window
    return coverage


def empty_envelope(
    *,
    reason: str = "no data",
    window: Optional[Dict[str, Any]] = None,
    sources: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Shortcut for the empty-result case.

    Returns a well-formed envelope with `data=[]`, a coverage block that
    reports zero rows, and the `reason` surfaced inside the first gap so
    the LLM can distinguish "no data because query empty" from "no data
    because source not synced".
    """
    gap = [{"reason": reason}]
    if window:
        gap[0]["from"] = window["from"]
        gap[0]["to"] = window["to"]
    return build_envelope(
        [],
        coverage={
            "counts": {"rows": 0, "sources_represented": []},
            "gaps": gap,
            "truncated": False,
            **({"window": window} if window else {}),
        },
        sources=sources or [],
    )

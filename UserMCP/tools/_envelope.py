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

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Union

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
    never need existence checks. Callers typically build `sources` via
    `tools._sources.fetch_sources()`.
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


def resolve_window(
    arguments: Dict[str, Any],
    *,
    default_days: int = 30,
    max_days: int = 90,
    tz: Any = None,
) -> tuple[date, date]:
    """Resolve a `from`/`to` or `days`-lookback window from tool arguments.

    The 90-day cap matches the UserApp `parse_date_range_params` ceiling so
    the endpoint never rejects a request we just built.

    `tz` (ZoneInfo) anchors the `days`-shorthand end at today *in the user's
    home timezone*. Without it, a user west of UTC in the evening gets
    tomorrow as the window end — a silent one-day drift.
    """
    from_str = arguments.get("from")
    to_str = arguments.get("to")
    if from_str and to_str:
        start = datetime.strptime(from_str, "%Y-%m-%d").date()
        end = datetime.strptime(to_str, "%Y-%m-%d").date()
    else:
        days = int(arguments.get("days", default_days) or default_days)
        days = max(1, min(max_days, days))
        end = datetime.now(tz or timezone.utc).date()
        start = end - timedelta(days=days - 1)
    if (end - start).days + 1 > max_days:
        start = end - timedelta(days=max_days - 1)
    return start, end


def extract_list(resp: Any, *keys: str) -> list:
    """Pull the first list found under `keys` from an API response ([] if none)."""
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        for k in keys:
            v = resp.get(k)
            if isinstance(v, list):
                return v
    return []


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

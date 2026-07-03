"""Pagination envelope assertions for live tests.

After the UserAPIPagination rollout, every paginated list endpoint returns
a standard envelope:

    {
        "<key>": [...],                     # the page of rows
        "pagination": {
            "total":    <int>,              # unfiltered post-WHERE count
            "limit":    <int>,              # echoes the request limit
            "offset":   <int>,              # echoes the request offset
            "has_more": <bool>,             # offset + len(items) < total
        }
    }

This helper validates that shape and returns the items list, so flows can
swap a single line:

    items = resp.json()                                  # before
    items = assert_pagination_envelope(resp.json(), 'food_items')  # after

The helper does more than just unwrap — it asserts the envelope is
internally consistent and that the internal `_total` column (used by the
backend's count(*) OVER() trick) did not leak into the items.
"""
from __future__ import annotations

from typing import Any


def assert_pagination_envelope(body: Any, key: str) -> list[dict]:
    """Validate a paginated response envelope and return body[key].

    Raises AssertionError with a descriptive message on any contract
    violation. Returns the items list on success.
    """
    assert isinstance(body, dict), (
        f"expected dict envelope, got {type(body).__name__}: {body!r}"
    )
    assert key in body, (
        f"missing collection key '{key}': have {sorted(body.keys())}"
    )
    assert "pagination" in body, (
        f"missing 'pagination' block: have {sorted(body.keys())}"
    )
    items = body[key]
    assert isinstance(items, list), (
        f"expected list at body['{key}'], got {type(items).__name__}"
    )
    p = body["pagination"]
    for field in ("total", "limit", "offset", "has_more"):
        assert field in p, (
            f"pagination missing '{field}': have {sorted(p.keys())}"
        )
    assert isinstance(p["total"], int) and p["total"] >= 0, (
        f"pagination.total must be a non-negative int, got {p['total']!r}"
    )
    assert isinstance(p["limit"], int) and p["limit"] > 0, (
        f"pagination.limit must be a positive int, got {p['limit']!r}"
    )
    assert isinstance(p["offset"], int) and p["offset"] >= 0, (
        f"pagination.offset must be a non-negative int, got {p['offset']!r}"
    )
    assert isinstance(p["has_more"], bool), (
        f"pagination.has_more must be a bool, got {type(p['has_more']).__name__}"
    )
    assert p["total"] >= len(items), (
        f"pagination.total ({p['total']}) < len(items) ({len(items)}) — "
        f"impossible if total is the unfiltered count"
    )
    expected_has_more = p["offset"] + len(items) < p["total"]
    assert p["has_more"] == expected_has_more, (
        f"pagination.has_more mismatch: backend says {p['has_more']}, "
        f"derived from offset+len<total says {expected_has_more}"
    )
    if items:
        assert "_total" not in items[0], (
            "internal _total column leaked into items — backend should "
            "pop _total before serializing"
        )
    return items

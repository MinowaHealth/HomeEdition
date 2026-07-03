"""
Pagination helper for UserMCP tools.

UserApp standardized all v1 list endpoints on 2026-04-16 to return the envelope
`{entries: [...], pagination: {total, limit, offset, has_more}}`. See
`APIDocumentation/PaginationStandard.md` for the authoritative contract.

This module walks envelope pages until exhausted so MCP tools can present a
complete view of whatever date range the caller asked for. The user-facing
lever is the date range, not page counts — a `max_rows` ceiling exists only
to short-circuit pathological queries (e.g. a year-long window on an
extremely high-volume account).

Resilient to bare-list responses: endpoints that have not migrated to the
envelope (currently `/timeframes`, `POST /health-query`) return a list, and
this helper passes them through unchanged.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

PAGE_SIZE = 200          # UserApp max_limit per PaginationStandard.md
MAX_ROWS = 10_000        # Circuit breaker for pathological queries


async def fetch_all_entries(
    api_client: Any,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    page_size: int = PAGE_SIZE,
    max_rows: int = MAX_ROWS,
) -> Tuple[List[Dict], bool]:
    """
    Fetch all pages of a UserApp envelope list endpoint.

    Reads `pagination.total` from the first response. If total exceeds
    `max_rows`, returns early with the first page and `hit_max_rows=True`
    so the caller can surface a clear "narrow the date range" error. This
    avoids looping for minutes on a query that is asking for too much.

    Otherwise, walks pages via `offset` until `pagination.has_more` is False.

    Args:
        api_client: UserAppClient with async `call_api(path, method, params)`.
        path: Endpoint path (e.g. '/health-input-log').
        params: Non-pagination query params (e.g. date range). Pagination
            params `limit` and `offset` are set by this helper.
        page_size: Rows per request. Defaults to UserApp's max (200).
        max_rows: Circuit-breaker ceiling on total rows. Set above the
            heaviest realistic user volume for the endpoint's use case.

    Returns:
        (entries, hit_max_rows) — the accumulated rows and whether the
        circuit breaker fired. If it fired, `entries` is just the first
        page (not guaranteed to be complete); caller should surface an
        error rather than treat it as a truncated success.

        For bare-list responses (non-migrated endpoints), returns
        (response, False) unchanged.
    """
    base_params = dict(params or {})

    first = await api_client.call_api(
        path,
        method='GET',
        params={**base_params, 'limit': page_size, 'offset': 0},
    )

    if isinstance(first, list):
        return first, False

    if not isinstance(first, dict) or 'entries' not in first:
        logger.warning(f"{path}: unexpected response shape {type(first).__name__}; treating as empty")
        return [], False

    entries: List[Dict] = list(first.get('entries') or [])
    pagination = first.get('pagination') or {}
    total = pagination.get('total', len(entries))

    if isinstance(total, int) and total > max_rows:
        logger.warning(
            f"{path}: total={total} exceeds max_rows={max_rows}; short-circuiting"
        )
        return entries, True

    while pagination.get('has_more'):
        offset = pagination.get('offset', 0) + len(first.get('entries') or [])
        next_page = await api_client.call_api(
            path,
            method='GET',
            params={**base_params, 'limit': page_size, 'offset': offset},
        )

        if not isinstance(next_page, dict):
            logger.warning(f"{path}: page at offset={offset} returned non-dict; stopping")
            break

        page_entries = next_page.get('entries') or []
        entries.extend(page_entries)
        pagination = next_page.get('pagination') or {}
        first = next_page

        if len(entries) >= max_rows:
            logger.warning(
                f"{path}: accumulated {len(entries)} >= max_rows={max_rows}; stopping"
            )
            return entries, True

    return entries, False

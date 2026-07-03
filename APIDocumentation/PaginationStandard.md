# Pagination Standard

**Date:** 2026-04-16 (last updated 2026-05-10)
**Scope:** UserApp v1 list endpoints. v2 blueprints inherit via passthrough.

Every paginated list endpoint in UserApp returns the same envelope so that clients (web UI, mobile, future MCP tooling) deal with **one contract**, not thirteen.

---

## The envelope

```json
{
  "entries": [ ... ],
  "pagination": {
    "total": 1234,
    "limit": 50,
    "offset": 100,
    "has_more": true
  }
}
```

- `entries` — the page of rows. Always the key name; never renamed per-endpoint.
- `pagination.total` — unfiltered post-WHERE count (from `count(*) OVER()` in SQL, or `len(merged)` before slicing in Python).
- `pagination.limit` — the clamped page size actually used for this query.
- `pagination.offset` — the clamped offset actually used.
- `pagination.has_more` — `offset + len(entries) < total`. Computed from `len(entries)`, not `limit`, so the final partial page reports correctly.

---

## Defaults

| Setting | Value |
|---|---|
| `default_limit` | 50 |
| `max_limit` | 200 |
| `default_offset` | 0 |

### Exceptions (documented per-endpoint)

| Endpoint | default | max | Rationale |
|---|---|---|---|
| `/food-items` | 100 | 500 | Catalog browsing, not time-series. Users routinely scroll through the food catalog. |
| `/healthkit/jobs` | 20 | 100 | Preserves the pre-pagination listing size; job rows are short-lived and rarely need deep history. |
| `/garmin/jobs` | 20 | 100 | Same as `/healthkit/jobs`. |
| `/fax/inbox` | 50 | 100 | Tighter `max` than the global 200; rationale to be revisited with upcoming fax work. |
| `/fax/outbox` | 50 | 100 | Same as `/fax/inbox`. |

If you add another exception, document it here and in the endpoint's docstring.

---

## Ordering

- **Activity / log endpoints** — `<timestamp_col> DESC` (most recent first). Examples: `/health-input-log`, `/blood-pressure`, `/temperature`, `/weight`, `/observations`, `/documents`, `/log-promotions`, `/all-logs`.
- **Resource collections** — natural ordering, typically alphabetical by name. Examples: `/meals` (by `name ASC`), `/food-items` (by `name ASC`).

The rule: if a user would say "show me my most recent X," order DESC by timestamp. If they'd say "show me all my X," order by the natural human field (usually name).

---

## Query parameters

| Param | Type | Default | Clamping |
|---|---|---|---|
| `limit` | int | `default_limit` | `[1, max_limit]` |
| `offset` | int | 0 | `>= 0` |

Non-integer values fall back to defaults **silently** (no 400). This is deliberate — the helper is a soft contract, not a validator. Clients that pass garbage get the default page, not an error.

Date-range endpoints also accept `start_date` and `end_date` (YYYY-MM-DD); those are parsed separately via `parse_date_range_params()` and are orthogonal to pagination.

---

## Helpers — do not hand-roll

Both helpers live in [`UserApp/webapp/utils.py`](../UserApp/webapp/utils.py).

### `parse_pagination_params(default_limit=50, max_limit=200) -> (limit, offset)`

Reads `request.args`, clamps, returns `(limit, offset)`. Call at the top of every list endpoint. Override the defaults only for documented exceptions.

### `paginated_response(items, total, limit, offset, key='entries') -> dict`

Builds the envelope. Always pass `key='entries'`. The `key` parameter exists for historical reasons — every current endpoint uses `'entries'`.

### Canonical usage

```python
from utils import parse_pagination_params, paginated_response

@bp.route('/things', methods=['GET'])
@require_auth
def get_things():
    limit, offset = parse_pagination_params()
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT count(*) OVER() AS _total, id, name, created_at
        FROM things
        WHERE ...
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
    """, (..., limit, offset))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    total = rows[0]['_total'] if rows else 0
    for r in rows:
        r.pop('_total', None)
        r['id'] = str(r['id'])
        # ... serialize other fields ...

    return jsonify(paginated_response(rows, total, limit, offset, key='entries'))
```

The `count(*) OVER()` window computes the total in the same query as the page — no second round-trip. This is the cheapest correct option for offset pagination.

---

## Python-side merge endpoints

Some endpoints (currently only `/all-logs`) merge results from multiple heterogeneous sources in Python rather than a single SQL query. For these:

- Compute `total = len(merged)` **before** slicing.
- Slice `merged[offset:offset + limit]` for the page.
- Return `paginated_response(sliced, total, limit, offset, key='entries')`.

The underlying per-source `LIMIT` caps bound worst-case DB load but mean `total` reflects the merged-and-capped set, not the true unfiltered row count. A faithful UNION-as-CTE rewrite would fix that; it is deferred.

---

## Transitional: `X-Truncated` header

A subset of endpoints still emit `X-Truncated: true` alongside the pagination envelope when results were capped (equivalent to `pagination.has_more == true`). This is a legacy dual-signal that predates the standard envelope and is documented as a public contract in [`MobileAPI.md`](MobileAPI.md) and [`DateFiltering-API.md`](DateFiltering-API.md), so the RN client reads it today.

**Endpoints still emitting it (5):**

- `/blood-pressure`, `/temperature`, `/weight` — [`UserApp/webapp/routes/vitals.py`](../UserApp/webapp/routes/vitals.py)
- `/health-input-log`, `/food-log` — [`UserApp/webapp/routes/logging_routes.py`](../UserApp/webapp/routes/logging_routes.py)

**Status (2026-05-10):** the mobile developer is believed to be all but done with the mobile-side work that makes this header removable. This section is a linger — it stays only until we confirm the RN client reads `pagination.has_more` exclusively, then everything below gets executed and this section deleted.

**Retirement steps once confirmed:**

1. Strip `resp.headers['X-Truncated'] = 'true'` from the 5 emit sites.
2. Remove the `X-Truncated` assertions in `test_vitals_routes.py` and `test_logging_routes.py`.
3. Update `MobileAPI.md` and `DateFiltering-API.md` to remove the `X-Truncated` advice and point clients at `pagination.has_more`.
4. Delete this section.

---

## Not yet implemented

- **Cursor / keyset pagination.** Offset pagination is O(n + offset) on the DB side. Deep-scrolling clients will eventually need cursor pagination; not built today. Do not assume it exists.
- **Per-endpoint `has_prev` / `has_next` links.** Clients compute navigation from `total`, `limit`, `offset`, and `has_more`. HATEOAS-style link envelopes are not provided.
- **Consumer-specific page sizes.** There is no per-consumer `max_limit` override. If MCP or analytics tooling needs larger pages in the future, the answer is to add a new parameter (e.g. `?page_profile=bulk`) or a new endpoint, not to raise `max_limit` globally.

---

## Compliance

As of 2026-04-16, all v1 list endpoints in UserApp return this envelope with `key='entries'`. v2 blueprints are passthroughs to v1 and inherit automatically. When adding a new list endpoint:

1. Use `parse_pagination_params()` and `paginated_response(..., key='entries')`.
2. Pick `<col> DESC` or natural-alphabetical ordering per the rule above.
3. If the endpoint needs non-default limits, document the exception here and in the docstring.

Non-paginated endpoints (single-record GETs, POST/PUT/DELETE) are out of scope for this standard.

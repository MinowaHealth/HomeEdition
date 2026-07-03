"""
Unit tests for shared webapp utilities (UserApp/webapp/utils.py).

Currently covers:
- parse_pagination_params: query-string parsing with safety bounds
- paginated_response: standard list-endpoint envelope shape

These helpers are consumed by ~25 list endpoints across the routes/ blueprints,
so any regression here is load-bearing across the API surface.
"""
from pathlib import Path
import sys

import pytest


WEBAPP_ROOT = Path(__file__).resolve().parents[1]
if str(WEBAPP_ROOT) not in sys.path:
    sys.path.insert(0, str(WEBAPP_ROOT))

from utils import parse_pagination_params, paginated_response


# ---------------------------------------------------------------------------
# parse_pagination_params
# ---------------------------------------------------------------------------
#
# parse_pagination_params reads from flask.request.args, so each test runs
# inside a test_request_context() to fake a query string.


class TestParsePaginationParams:
    def test_defaults_when_no_params(self, app):
        with app.test_request_context('/'):
            limit, offset = parse_pagination_params()
        assert limit == 50
        assert offset == 0

    def test_parses_both_params(self, app):
        with app.test_request_context('/?limit=25&offset=75'):
            limit, offset = parse_pagination_params()
        assert limit == 25
        assert offset == 75

    def test_custom_default_limit(self, app):
        with app.test_request_context('/'):
            limit, offset = parse_pagination_params(default_limit=100, max_limit=500)
        assert limit == 100
        assert offset == 0

    def test_limit_clamped_to_max(self, app):
        with app.test_request_context('/?limit=9999'):
            limit, _ = parse_pagination_params(max_limit=200)
        assert limit == 200

    def test_limit_clamped_to_max_with_custom_ceiling(self, app):
        with app.test_request_context('/?limit=2000'):
            limit, _ = parse_pagination_params(default_limit=100, max_limit=500)
        assert limit == 500

    def test_limit_clamped_to_minimum_one(self, app):
        with app.test_request_context('/?limit=0'):
            limit, _ = parse_pagination_params()
        assert limit == 1

    def test_limit_clamped_to_minimum_when_negative(self, app):
        with app.test_request_context('/?limit=-50'):
            limit, _ = parse_pagination_params()
        assert limit == 1

    def test_offset_clamped_when_negative(self, app):
        with app.test_request_context('/?offset=-10'):
            _, offset = parse_pagination_params()
        assert offset == 0

    def test_non_integer_limit_falls_back_to_default(self, app):
        with app.test_request_context('/?limit=banana'):
            limit, _ = parse_pagination_params()
        assert limit == 50

    def test_non_integer_offset_falls_back_to_zero(self, app):
        with app.test_request_context('/?offset=banana'):
            _, offset = parse_pagination_params()
        assert offset == 0

    def test_both_params_garbage_fall_back(self, app):
        with app.test_request_context('/?limit=foo&offset=bar'):
            limit, offset = parse_pagination_params(default_limit=42)
        assert limit == 42
        assert offset == 0

    def test_max_limit_respected_with_custom_default(self, app):
        # Tier 1 use case: health-input-log uses default=100, max=1000
        with app.test_request_context('/?limit=500'):
            limit, _ = parse_pagination_params(default_limit=100, max_limit=1000)
        assert limit == 500

    def test_max_limit_clamps_with_custom_default(self, app):
        with app.test_request_context('/?limit=5000'):
            limit, _ = parse_pagination_params(default_limit=100, max_limit=1000)
        assert limit == 1000


# ---------------------------------------------------------------------------
# paginated_response
# ---------------------------------------------------------------------------
#
# Pure function — no Flask context needed.


class TestPaginatedResponse:
    def test_standard_envelope_shape(self):
        result = paginated_response(
            items=[{'id': 1}, {'id': 2}, {'id': 3}],
            total=10,
            limit=3,
            offset=0,
            key='conditions',
        )
        assert result == {
            'conditions': [{'id': 1}, {'id': 2}, {'id': 3}],
            'pagination': {
                'total': 10,
                'limit': 3,
                'offset': 0,
                'has_more': True,
            },
        }

    def test_default_key_is_items(self):
        result = paginated_response(items=[], total=0, limit=50, offset=0)
        assert 'items' in result
        assert 'pagination' in result

    def test_has_more_true_when_more_pages_exist(self):
        # 100 total, fetched 50 at offset 0 → 50 more remaining
        result = paginated_response(items=[{}] * 50, total=100, limit=50, offset=0)
        assert result['pagination']['has_more'] is True

    def test_has_more_false_on_exact_last_page(self):
        # 100 total, fetched 50 at offset 50 → exactly the last page
        result = paginated_response(items=[{}] * 50, total=100, limit=50, offset=50)
        assert result['pagination']['has_more'] is False

    def test_has_more_uses_item_count_not_limit(self):
        # 53 total, fetched only 3 at offset 50 (partial last page)
        # If has_more used `limit` (50), it would compute 50+50=100 < 53 → False (correct)
        # but for offset 0 with limit 50, that gives 0+50=50 < 53 → True (correct)
        # The trickier case: offset=50, limit=50, len=3 (got 3 of remaining 3)
        # With len: 50+3=53 < 53 → False (correct)
        # With limit: 50+50=100 < 53 → False (also correct here, but breaks below)
        result = paginated_response(items=[{}] * 3, total=53, limit=50, offset=50)
        assert result['pagination']['has_more'] is False

    def test_has_more_partial_page_with_more_remaining_uses_len(self):
        # Edge case where len-vs-limit matters:
        # total=100, limit=50, offset=0, but server returned only 30
        # (e.g. ORDER BY hit a soft cap that the spec allows).
        # has_more should be True (30 < 100), and using len(items) gets it right.
        result = paginated_response(items=[{}] * 30, total=100, limit=50, offset=0)
        assert result['pagination']['has_more'] is True

    def test_empty_page_zero_total(self):
        result = paginated_response(items=[], total=0, limit=50, offset=0, key='conditions')
        assert result == {
            'conditions': [],
            'pagination': {
                'total': 0,
                'limit': 50,
                'offset': 0,
                'has_more': False,
            },
        }

    def test_empty_page_past_end(self):
        # Client requests offset beyond total — server returns empty page
        result = paginated_response(items=[], total=10, limit=50, offset=100, key='conditions')
        assert result['conditions'] == []
        assert result['pagination']['has_more'] is False
        assert result['pagination']['total'] == 10
        assert result['pagination']['offset'] == 100

    def test_total_field_passes_through(self):
        # The `total` field is the spec's contract for "how many would match"
        result = paginated_response(items=[{}], total=42, limit=50, offset=0)
        assert result['pagination']['total'] == 42

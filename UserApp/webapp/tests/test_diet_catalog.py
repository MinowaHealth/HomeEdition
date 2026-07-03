"""
Unit tests for diet_catalog routes.

Routes in routes/diet_catalog.py:
  - GET /api/v1/diet-catalog            (list; optional ?category= filter)
  - GET /api/v1/diet-catalog/<code>     (single diet detail)

Phase 1 scope: read-only surface over the 21 seeded reference rows
(was 23 pre-Phase-2; raw and macrobiotic dropped 2026-05-06).
The routes scope by tenant_id even though diet_catalog has no RLS,
for forward-compat with per-tenant catalog overrides.
"""
import json
from datetime import datetime, timezone
from urllib.parse import quote


class TestListDietCatalog:
    """GET /api/v1/diet-catalog"""

    def test_returns_full_list(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        cur.fetchall.return_value = [
            {
                'code': 'dash', 'display_name': 'DASH (low sodium, low fat)',
                'category': 'nutrient_pattern',
                'description': 'Dietary Approaches to Stop Hypertension',
                'excludes': None,
                'nutrient_targets': {'sodium_mg_max_per_day': 2300},
                'parent_diet_code': None,
                'evidence_level': 'clinical',
                'is_clinical': False,
                'notes': None,
            },
            {
                'code': 'kosher', 'display_name': 'Kosher',
                'category': 'exclusion',
                'description': 'Excludes pork, shellfish, and meat-dairy mixtures',
                'excludes': {'fdc_food_categories': ['Pork Products']},
                'nutrient_targets': None,
                'parent_diet_code': None,
                'evidence_level': 'pattern',
                'is_clinical': False,
                'notes': 'System filters known violators only.',
            },
        ]

        resp = client.get('/api/v1/diet-catalog', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]['code'] == 'dash'
        # JSONB columns surface as nested dicts (psycopg returns them as such).
        assert data[0]['nutrient_targets'] == {'sodium_mg_max_per_day': 2300}
        assert data[1]['excludes']['fdc_food_categories'] == ['Pork Products']

    def test_filters_by_category(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        cur.fetchall.return_value = [
            {
                'code': 'kosher', 'display_name': 'Kosher',
                'category': 'exclusion',
                'description': 'Excludes pork...',
                'excludes': {'fdc_food_categories': ['Pork Products']},
                'nutrient_targets': None,
                'parent_diet_code': None,
                'evidence_level': 'pattern',
                'is_clinical': False,
                'notes': None,
            }
        ]

        resp = client.get(
            '/api/v1/diet-catalog?category=exclusion', headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]['category'] == 'exclusion'

        # Confirm the WHERE category = %s clause was issued.
        executed_sqls = [
            call.args[0] for call in cur.execute.call_args_list if call.args
        ]
        assert any('category = %s' in s for s in executed_sqls)

    def test_invalid_category_returns_400(self, client, auth_headers, mock_db):
        resp = client.get(
            '/api/v1/diet-catalog?category=bogus', headers=auth_headers
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['error'] == 'invalid category'
        assert 'exclusion' in body['valid']
        assert 'nutrient_pattern' in body['valid']
        assert 'medical' in body['valid']
        assert 'lifestyle' in body['valid']


class TestGetDietCatalogEntry:
    """GET /api/v1/diet-catalog/<code>"""

    def test_returns_single_entry(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        cur.fetchone.return_value = {
            'code': 'mediterranean',
            'display_name': 'Mediterranean',
            'category': 'nutrient_pattern',
            'description': 'High vegetables, olive oil, fish; limited red meat',
            'excludes': {'fdc_food_categories_limit': ['Beef Products']},
            'nutrient_targets': {'fiber_g_min_per_day': 25},
            'parent_diet_code': None,
            'evidence_level': 'clinical',
            'is_clinical': False,
            'notes': 'Pattern-based; soft limits.',
            'created_at': datetime(2026, 5, 2, tzinfo=timezone.utc),
        }

        resp = client.get(
            '/api/v1/diet-catalog/mediterranean', headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['code'] == 'mediterranean'
        assert data['nutrient_targets']['fiber_g_min_per_day'] == 25
        assert data['created_at'] == '2026-05-02T00:00:00+00:00'

    def test_unknown_code_returns_404(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        cur.fetchone.return_value = None

        resp = client.get(
            '/api/v1/diet-catalog/totally-fake', headers=auth_headers
        )
        assert resp.status_code == 404
        body = resp.get_json()
        assert body['error'] == 'diet code not found'
        assert body['code'] == 'totally-fake'


class TestDietCatalogPull:
    """GET /api/v1/diet-catalog/pull (RxDB replication)"""

    def _row(self, code, updated_at):
        return {
            'code': code,
            'display_name': code.title(),
            'category': 'exclusion',
            'description': None,
            'excludes': None,
            'nutrient_targets': None,
            'parent_diet_code': None,
            'evidence_level': 'pattern',
            'is_clinical': False,
            'notes': None,
            'updated_at': updated_at,
        }

    def test_returns_documents_and_checkpoint(
        self, client, auth_headers, mock_db
    ):
        conn, cur = mock_db
        ts1 = datetime(2026, 5, 2, 10, 0, tzinfo=timezone.utc)
        ts2 = datetime(2026, 5, 2, 11, 0, tzinfo=timezone.utc)
        cur.fetchall.return_value = [
            self._row('dash', ts1),
            self._row('kosher', ts2),
        ]

        resp = client.get(
            '/api/v1/diet-catalog/pull', headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data['documents']) == 2
        # Catalog is additive-only — _deleted is always False.
        assert all(doc['_deleted'] is False for doc in data['documents'])
        assert data['checkpoint'] == {
            'updated_at': '2026-05-02T11:00:00+00:00',
            'code': 'kosher',
        }

    def test_uses_checkpoint_in_query(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        cur.fetchall.return_value = []

        checkpoint = quote(json.dumps({
            'updated_at': '2026-05-02T10:00:00+00:00',
            'code': 'dash',
        }))
        resp = client.get(
            f'/api/v1/diet-catalog/pull?checkpoint={checkpoint}',
            headers=auth_headers,
        )
        assert resp.status_code == 200

        executed_sqls = [
            call.args[0] for call in cur.execute.call_args_list if call.args
        ]
        assert any('(updated_at, code) >' in s for s in executed_sqls)

    def test_no_checkpoint_does_full_scan(
        self, client, auth_headers, mock_db
    ):
        conn, cur = mock_db
        cur.fetchall.return_value = []

        resp = client.get(
            '/api/v1/diet-catalog/pull', headers=auth_headers
        )
        assert resp.status_code == 200

        executed_sqls = [
            call.args[0] for call in cur.execute.call_args_list if call.args
        ]
        # Without a checkpoint, the (updated_at, code) > cursor must be absent.
        assert not any('(updated_at, code) >' in s for s in executed_sqls)

    def test_invalid_checkpoint_returns_400(
        self, client, auth_headers, mock_db
    ):
        resp = client.get(
            '/api/v1/diet-catalog/pull?checkpoint=not-json',
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert 'checkpoint' in resp.get_json()['error']

    def test_invalid_batch_size_returns_400(
        self, client, auth_headers, mock_db
    ):
        resp = client.get(
            '/api/v1/diet-catalog/pull?batchSize=abc',
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert 'batchSize' in resp.get_json()['error']

    def test_zero_batch_size_returns_400(
        self, client, auth_headers, mock_db
    ):
        resp = client.get(
            '/api/v1/diet-catalog/pull?batchSize=0',
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_empty_result_keeps_input_checkpoint(
        self, client, auth_headers, mock_db
    ):
        """When no rows match, server returns the client's checkpoint unchanged."""
        conn, cur = mock_db
        cur.fetchall.return_value = []

        checkpoint = quote(json.dumps({
            'updated_at': '2026-05-02T11:00:00+00:00',
            'code': 'kosher',
        }))
        resp = client.get(
            f'/api/v1/diet-catalog/pull?checkpoint={checkpoint}',
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['documents'] == []
        assert data['checkpoint'] == {
            'updated_at': '2026-05-02T11:00:00+00:00',
            'code': 'kosher',
        }


# =============================================================================
# Phase 2: derivation_tier on responses
# =============================================================================


class TestDerivationTierField:
    """The Phase 2 derivation_tier column gates matcher behaviour
    ('clean' / 'approximate' / 'deferred'). The catalog API exposes
    this so mobile can disable badges on deferred-tier diets and surface
    the caveat from notes for approximate-tier diets."""

    def test_list_response_includes_derivation_tier(
        self, client, auth_headers, mock_db,
    ):
        conn, cur = mock_db
        cur.fetchall.return_value = [
            {
                'code': 'plant_based', 'display_name': 'Plant Based Diet',
                'category': 'exclusion',
                'description': 'Excludes all animal products',
                'excludes': {'fdc_food_categories': ['Beef Products']},
                'nutrient_targets': None,
                'parent_diet_code': None,
                'evidence_level': 'clinical',
                'is_clinical': False,
                'derivation_tier': 'clean',
                'notes': None,
            },
            {
                'code': 'low_fodmap', 'display_name': 'Low-FODMAP Diet',
                'category': 'exclusion',
                'description': 'Excludes FODMAPs',
                'excludes': {'ingredients_substr': ['onion']},
                'nutrient_targets': None,
                'parent_diet_code': None,
                'evidence_level': 'clinical',
                'is_clinical': False,
                'derivation_tier': 'deferred',
                'notes': 'Tier C: matcher returns unknown until Phase 4',
            },
        ]
        resp = client.get('/api/v1/diet-catalog', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        plant_based = next(r for r in data if r['code'] == 'plant_based')
        assert plant_based['derivation_tier'] == 'clean'
        deferred = next(r for r in data if r['code'] == 'low_fodmap')
        assert deferred['derivation_tier'] == 'deferred'

    def test_select_includes_derivation_tier_column(
        self, client, auth_headers, mock_db,
    ):
        """SQL-shape regression guard — derivation_tier must be in the
        SELECT or the response key would silently disappear."""
        conn, cur = mock_db
        cur.fetchall.return_value = []
        client.get('/api/v1/diet-catalog', headers=auth_headers)
        sqls = [c.args[0] for c in cur.execute.call_args_list if c.args]
        assert any('derivation_tier' in s for s in sqls), (
            'list_diet_catalog SELECT must include derivation_tier'
        )

    def test_single_get_includes_derivation_tier(
        self, client, auth_headers, mock_db,
    ):
        conn, cur = mock_db
        cur.fetchone.return_value = {
            'code': 'mediterranean', 'display_name': 'Mediterranean',
            'category': 'nutrient_pattern',
            'description': 'High vegetables, olive oil, fish',
            'excludes': {'fdc_food_categories_limit': ['Beef Products']},
            'nutrient_targets': {'fiber_g_min_per_day': 25},
            'parent_diet_code': None,
            'evidence_level': 'clinical',
            'is_clinical': False,
            'derivation_tier': 'approximate',
            'notes': 'Pattern, not a rulebook',
            'created_at': datetime(2026, 5, 6, tzinfo=timezone.utc),
        }
        resp = client.get(
            '/api/v1/diet-catalog/mediterranean', headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.get_json()['derivation_tier'] == 'approximate'

    def test_pull_includes_derivation_tier(
        self, client, auth_headers, mock_db,
    ):
        conn, cur = mock_db
        cur.fetchall.return_value = [{
            'code': 'low_carb', 'display_name': 'Low Carb',
            'category': 'nutrient_pattern',
            'description': 'Carbs below 100g/day',
            'excludes': None,
            'nutrient_targets': {'carbs_g_max_per_day': 100},
            'parent_diet_code': None,
            'evidence_level': 'clinical',
            'is_clinical': False,
            'derivation_tier': 'clean',
            'notes': None,
            'updated_at': datetime(2026, 5, 6, tzinfo=timezone.utc),
        }]
        resp = client.get('/api/v1/diet-catalog/pull', headers=auth_headers)
        assert resp.status_code == 200
        documents = resp.get_json()['documents']
        assert documents[0]['derivation_tier'] == 'clean'

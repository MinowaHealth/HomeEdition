"""
Unit tests for dietary settings routes.

Routes in routes/dietary_settings.py:
  - GET    /api/v1/dietary-settings          (active or ?history=true)
  - POST   /api/v1/dietary-settings          (create initial)
  - PUT    /api/v1/dietary-settings          (update = new row + deactivate old)
  - DELETE /api/v1/dietary-settings/<id>     (remove entry)
"""
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest


class TestGetDietarySettings:
    """GET /api/v1/dietary-settings"""

    def test_returns_active_setting(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        cur.fetchone.return_value = {
            'id': uuid.uuid4(),
            'diet_type': 'keto',
            'dietary_restrictions': ['gluten_free'],
            'calorie_target': 2000,
            'protein_target_g': Decimal('150.0'),
            'carb_target_g': Decimal('30.0'),
            'fat_target_g': Decimal('120.0'),
            'meal_count_per_day': 3,
            'notes': None,
            'is_active': True,
            'effective_date': datetime(2026, 3, 1).date(),
            'end_date': None,
            'created_at': datetime(2026, 3, 1, tzinfo=timezone.utc),
            'updated_at': datetime(2026, 3, 1, tzinfo=timezone.utc),
        }

        resp = client.get('/api/v1/dietary-settings', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['diet_type'] == 'keto'
        assert data['calorie_target'] == 2000
        assert data['protein_target_g'] == 150.0

    def test_returns_null_when_no_settings(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        cur.fetchone.return_value = None

        resp = client.get('/api/v1/dietary-settings', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json() is None

    def test_history_returns_all_settings(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        cur.fetchall.return_value = [
            {
                'id': uuid.uuid4(), 'diet_type': 'mediterranean',
                'dietary_restrictions': None, 'calorie_target': 2200,
                'protein_target_g': None, 'carb_target_g': None,
                'fat_target_g': None, 'meal_count_per_day': 3,
                'notes': None, 'is_active': True,
                'effective_date': datetime(2026, 6, 1).date(),
                'end_date': None,
                'created_at': datetime(2026, 6, 1, tzinfo=timezone.utc),
                'updated_at': datetime(2026, 6, 1, tzinfo=timezone.utc),
            },
            {
                'id': uuid.uuid4(), 'diet_type': 'keto',
                'dietary_restrictions': ['gluten_free'], 'calorie_target': 2000,
                'protein_target_g': Decimal('150.0'), 'carb_target_g': Decimal('30.0'),
                'fat_target_g': Decimal('120.0'), 'meal_count_per_day': 3,
                'notes': None, 'is_active': False,
                'effective_date': datetime(2026, 3, 1).date(),
                'end_date': datetime(2026, 6, 1).date(),
                'created_at': datetime(2026, 3, 1, tzinfo=timezone.utc),
                'updated_at': datetime(2026, 6, 1, tzinfo=timezone.utc),
            },
        ]

        resp = client.get(
            '/api/v1/dietary-settings?history=true', headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]['diet_type'] == 'mediterranean'
        assert data[0]['is_active'] is True
        assert data[1]['diet_type'] == 'keto'
        assert data[1]['is_active'] is False


class TestCreateDietarySettings:
    """POST /api/v1/dietary-settings"""

    def test_creates_settings(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        # POST without diet_codes triggers vegan-by-default — validator now
        # queries diet_catalog for 'plant_based'. Mock that query as known.
        cur.fetchall.return_value = [{'code': 'plant_based'}]
        cur.fetchone.side_effect = [None, {'id': uuid.uuid4()}]

        resp = client.post(
            '/api/v1/dietary-settings',
            data=json.dumps({
                'diet_type': 'keto',
                'calorie_target': 2000,
                'protein_target_g': 150,
                'dietary_restrictions': ['gluten_free', 'dairy_free'],
            }),
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert 'id' in data

    def test_rejects_duplicate_active(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        # Validator runs first (against vegan-default 'plant_based'); then
        # the active-setting check sees an existing row and rejects 409.
        cur.fetchall.return_value = [{'code': 'plant_based'}]
        cur.fetchone.return_value = {'id': uuid.uuid4()}

        resp = client.post(
            '/api/v1/dietary-settings',
            data=json.dumps({'diet_type': 'vegan'}),
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert 'already exist' in resp.get_json()['error']

    def test_requires_json_body(self, client, auth_headers):
        resp = client.post(
            '/api/v1/dietary-settings',
            data=json.dumps(None),
            content_type='application/json',
            headers={'Authorization': auth_headers['Authorization']},
        )
        assert resp.status_code == 400


class TestUpdateDietarySettings:
    """PUT /api/v1/dietary-settings"""

    def test_creates_new_row_and_deactivates_old(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        # PUT without diet_codes also re-defaults to plant_based (the user
        # is "clearing" their codes, which under the vegan-by-default
        # contract means falling back to vegan rather than NULL).
        cur.fetchall.return_value = [{'code': 'plant_based'}]
        cur.fetchone.return_value = {'id': uuid.uuid4()}

        resp = client.put(
            '/api/v1/dietary-settings',
            data=json.dumps({
                'diet_type': 'mediterranean',
                'calorie_target': 2200,
                'effective_date': '2026-06-01',
            }),
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'id' in data
        assert data['message'] == 'Dietary settings updated'

        # Should have executed: UPDATE (deactivate) + INSERT (new row)
        assert cur.execute.call_count >= 2

    def test_requires_json_body(self, client, auth_headers):
        resp = client.put(
            '/api/v1/dietary-settings',
            data=json.dumps(None),
            content_type='application/json',
            headers={'Authorization': auth_headers['Authorization']},
        )
        assert resp.status_code == 400


class TestDietCodes:
    """diet_codes round-trip + validation against diet_catalog (Phase 1)."""

    def test_get_surfaces_diet_codes(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        cur.fetchone.return_value = {
            'id': uuid.uuid4(),
            'diet_type': 'mediterranean',
            'diet_codes': ['mediterranean', 'halal'],
            'dietary_restrictions': None,
            'calorie_target': 2200,
            'protein_target_g': None,
            'carb_target_g': None,
            'fat_target_g': None,
            'meal_count_per_day': 3,
            'notes': None,
            'is_active': True,
            'effective_date': datetime(2026, 6, 1).date(),
            'end_date': None,
            'created_at': datetime(2026, 6, 1, tzinfo=timezone.utc),
            'updated_at': datetime(2026, 6, 1, tzinfo=timezone.utc),
        }

        resp = client.get('/api/v1/dietary-settings', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['diet_codes'] == ['mediterranean', 'halal']

    def test_post_accepts_valid_diet_codes(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        # 1st: validation SELECT against diet_catalog → all codes known
        # 2nd: existing-active check → no row
        # 3rd: INSERT RETURNING id
        cur.fetchall.return_value = [
            {'code': 'mediterranean'}, {'code': 'halal'}
        ]
        cur.fetchone.side_effect = [None, {'id': uuid.uuid4()}]

        resp = client.post(
            '/api/v1/dietary-settings',
            data=json.dumps({
                'diet_codes': ['mediterranean', 'halal'],
                'calorie_target': 2200,
            }),
            headers=auth_headers,
        )
        assert resp.status_code == 201

    def test_post_rejects_unknown_diet_codes(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        # Validation SELECT returns only the known code; 'fake_diet' is missing.
        cur.fetchall.return_value = [{'code': 'mediterranean'}]

        resp = client.post(
            '/api/v1/dietary-settings',
            data=json.dumps({
                'diet_codes': ['mediterranean', 'fake_diet'],
            }),
            headers=auth_headers,
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['error'] == 'Unknown diet_codes'
        assert 'fake_diet' in body['unknown']
        assert 'mediterranean' not in body['unknown']

    def test_post_without_diet_codes_defaults_to_plant_based(
        self, client, auth_headers, mock_db
    ):
        """Vegan-by-default contract (2026-05-09): a POST that omits
        diet_codes (or sends null/empty) is filled in with ['plant_based']
        before validation. The catalog query IS issued — for plant_based —
        and the inserted row carries diet_codes=['plant_based']."""
        conn, cur = mock_db
        cur.fetchall.return_value = [{'code': 'plant_based'}]
        cur.fetchone.side_effect = [None, {'id': uuid.uuid4()}]

        resp = client.post(
            '/api/v1/dietary-settings',
            data=json.dumps({'diet_type': 'keto', 'calorie_target': 2000}),
            headers=auth_headers,
        )
        assert resp.status_code == 201

        # Catalog validation now happens for the defaulted code.
        executed_sqls = [
            call.args[0] for call in cur.execute.call_args_list if call.args
        ]
        assert any('FROM diet_catalog' in s for s in executed_sqls), (
            "Vegan default should trigger a catalog validation query"
        )

        # The INSERT must persist diet_codes=['plant_based'].
        insert_calls = [
            call for call in cur.execute.call_args_list
            if call.args and 'INSERT INTO dietary_settings' in call.args[0]
        ]
        assert len(insert_calls) == 1, "exactly one INSERT expected"
        params = insert_calls[0].args[1]
        # diet_codes is the 5th positional in the INSERT (tenant_id, id,
        # user_id, diet_type, diet_codes, ...).
        assert params[4] == ['plant_based'], (
            f"INSERT should persist vegan default; got {params[4]!r}"
        )

    def test_put_accepts_valid_diet_codes(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        cur.fetchall.return_value = [{'code': 'dash'}]
        cur.fetchone.return_value = {'id': uuid.uuid4()}

        resp = client.put(
            '/api/v1/dietary-settings',
            data=json.dumps({
                'diet_codes': ['dash'],
                'calorie_target': 2000,
                'effective_date': '2026-07-01',
            }),
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_put_rejects_unknown_diet_codes(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        cur.fetchall.return_value = []  # nothing matches

        resp = client.put(
            '/api/v1/dietary-settings',
            data=json.dumps({'diet_codes': ['nonexistent']}),
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert resp.get_json()['unknown'] == ['nonexistent']


class TestDeleteDietarySettings:
    """DELETE /api/v1/dietary-settings/<id> (soft-delete)."""

    def test_deletes_setting(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        cur.rowcount = 1

        setting_id = str(uuid.uuid4())
        resp = client.delete(
            f'/api/v1/dietary-settings/{setting_id}', headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.get_json()['message'] == 'Dietary setting deleted'

    def test_returns_404_if_not_found(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        cur.rowcount = 0

        setting_id = str(uuid.uuid4())
        resp = client.delete(
            f'/api/v1/dietary-settings/{setting_id}', headers=auth_headers
        )
        assert resp.status_code == 404

    def test_uses_soft_delete_not_hard_delete(self, client, auth_headers, mock_db):
        """Soft-delete must issue UPDATE with deleted_at, not DELETE.

        Hard-delete would leave RxDB clients unable to learn about the
        deletion (no tombstone). Guard against future regressions that
        flip back to a literal DELETE.
        """
        conn, cur = mock_db
        cur.rowcount = 1

        setting_id = str(uuid.uuid4())
        resp = client.delete(
            f'/api/v1/dietary-settings/{setting_id}', headers=auth_headers
        )
        assert resp.status_code == 200

        executed_sqls = [
            call.args[0] for call in cur.execute.call_args_list if call.args
        ]
        # The route must UPDATE deleted_at, not DELETE.
        assert any(
            'UPDATE dietary_settings' in s and 'deleted_at' in s
            for s in executed_sqls
        )
        assert not any('DELETE FROM dietary_settings' in s for s in executed_sqls)


class TestDietarySettingsPull:
    """GET /api/v1/dietary-settings/pull (RxDB pull)."""

    def _server_row(self, **overrides):
        """Build a server-shape dietary_settings row dict."""
        base = {
            'id': uuid.uuid4(),
            'user_id': uuid.uuid4(),
            'diet_type': 'mediterranean',
            'diet_codes': ['mediterranean'],
            'dietary_restrictions': None,
            'calorie_target': 2000,
            'protein_target_g': None,
            'carb_target_g': None,
            'fat_target_g': None,
            'meal_count_per_day': 3,
            'notes': None,
            'is_active': True,
            'effective_date': datetime(2026, 5, 1).date(),
            'end_date': None,
            'created_at': datetime(2026, 5, 1, tzinfo=timezone.utc),
            'updated_at': datetime(2026, 5, 2, 12, tzinfo=timezone.utc),
            '_deleted': False,
        }
        base.update(overrides)
        return base

    def test_returns_documents_and_checkpoint(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        row = self._server_row()
        cur.fetchall.return_value = [row]

        resp = client.get('/api/v1/dietary-settings/pull', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data['documents'], list)
        assert len(data['documents']) == 1
        assert data['documents'][0]['_deleted'] is False
        assert data['checkpoint']['updated_at'] == row['updated_at'].isoformat()
        assert data['checkpoint']['id'] == str(row['id'])

    def test_includes_tombstones(self, client, auth_headers, mock_db):
        """Soft-deleted rows are surfaced with _deleted=True."""
        conn, cur = mock_db
        cur.fetchall.return_value = [self._server_row(_deleted=True)]

        resp = client.get('/api/v1/dietary-settings/pull', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['documents'][0]['_deleted'] is True

    def test_uses_checkpoint_in_query(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        cur.fetchall.return_value = []

        cp = json.dumps({
            'updated_at': '2026-05-02T00:00:00+00:00',
            'id': str(uuid.uuid4()),
        })
        resp = client.get(
            f'/api/v1/dietary-settings/pull?checkpoint={cp}',
            headers=auth_headers,
        )
        assert resp.status_code == 200

        executed_sqls = [
            call.args[0] for call in cur.execute.call_args_list if call.args
        ]
        assert any('(updated_at, id) >' in s for s in executed_sqls)

    def test_no_checkpoint_does_full_scan(self, client, auth_headers, mock_db):
        """Empty checkpoint must NOT include a (updated_at, id) > clause."""
        conn, cur = mock_db
        cur.fetchall.return_value = []

        resp = client.get('/api/v1/dietary-settings/pull', headers=auth_headers)
        assert resp.status_code == 200

        executed_sqls = [
            call.args[0] for call in cur.execute.call_args_list if call.args
        ]
        assert not any('(updated_at, id) >' in s for s in executed_sqls)

    def test_invalid_checkpoint_returns_400(self, client, auth_headers, mock_db):
        resp = client.get(
            '/api/v1/dietary-settings/pull?checkpoint=not-json',
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_invalid_batch_size_returns_400(self, client, auth_headers, mock_db):
        resp = client.get(
            '/api/v1/dietary-settings/pull?batchSize=abc',
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_zero_batch_size_returns_400(self, client, auth_headers, mock_db):
        resp = client.get(
            '/api/v1/dietary-settings/pull?batchSize=0',
            headers=auth_headers,
        )
        assert resp.status_code == 400


class TestDietarySettingsPush:
    """POST /api/v1/dietary-settings/push (RxDB push)."""

    def _new_doc(self, **overrides):
        base = {
            'id': str(uuid.uuid4()),
            'diet_type': 'mediterranean',
            'diet_codes': ['mediterranean'],
            'dietary_restrictions': None,
            'calorie_target': 2000,
            'protein_target_g': None,
            'carb_target_g': None,
            'fat_target_g': None,
            'meal_count_per_day': 3,
            'notes': None,
            'is_active': True,
            'effective_date': '2026-05-02',
            'end_date': None,
            '_deleted': False,
        }
        base.update(overrides)
        return base

    def test_inserts_new_row_with_no_assumed_state(
        self, client, auth_headers, mock_db
    ):
        conn, cur = mock_db
        # Pre-flight validation matches diet_codes against catalog
        cur.fetchall.return_value = [{'code': 'mediterranean'}]
        # Per-row server lookup → no existing row
        cur.fetchone.return_value = None

        resp = client.post(
            '/api/v1/dietary-settings/push',
            data=json.dumps({
                'changeRows': [{
                    'newDocumentState': self._new_doc(),
                    'assumedMasterState': None,
                }]
            }),
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.get_json()['errors'] == []

        # Verify INSERT was issued
        executed_sqls = [
            call.args[0] for call in cur.execute.call_args_list if call.args
        ]
        assert any('INSERT INTO dietary_settings' in s for s in executed_sqls)

    def test_conflict_when_no_assumed_but_server_has_row(
        self, client, auth_headers, mock_db
    ):
        conn, cur = mock_db
        cur.fetchall.return_value = [{'code': 'mediterranean'}]
        # Server already has a row for this id
        cur.fetchone.return_value = {
            'id': uuid.uuid4(),
            'user_id': uuid.uuid4(),
            'diet_type': 'keto',
            'diet_codes': ['keto'],
            'dietary_restrictions': None,
            'calorie_target': 1800,
            'protein_target_g': None,
            'carb_target_g': None,
            'fat_target_g': None,
            'meal_count_per_day': 3,
            'notes': None,
            'is_active': True,
            'effective_date': datetime(2026, 4, 1).date(),
            'end_date': None,
            'created_at': datetime(2026, 4, 1, tzinfo=timezone.utc),
            'updated_at': datetime(2026, 5, 1, 12, tzinfo=timezone.utc),
            '_deleted': False,
        }

        resp = client.post(
            '/api/v1/dietary-settings/push',
            data=json.dumps({
                'changeRows': [{
                    'newDocumentState': self._new_doc(),
                    'assumedMasterState': None,  # client thinks server has nothing
                }]
            }),
            headers=auth_headers,
        )
        assert resp.status_code == 200
        errors = resp.get_json()['errors']
        assert len(errors) == 1
        assert errors[0]['documentInDb']['diet_type'] == 'keto'
        assert errors[0]['documentInDb']['_deleted'] is False

    def test_tombstone_conflict_when_assumed_but_no_server_row(
        self, client, auth_headers, mock_db
    ):
        """Client assumed a row exists; server has nothing → return synthetic _deleted."""
        conn, cur = mock_db
        cur.fetchall.return_value = [{'code': 'mediterranean'}]
        cur.fetchone.return_value = None  # no server row

        row_id = str(uuid.uuid4())
        resp = client.post(
            '/api/v1/dietary-settings/push',
            data=json.dumps({
                'changeRows': [{
                    'newDocumentState': self._new_doc(id=row_id),
                    'assumedMasterState': {
                        'id': row_id,
                        'updated_at': '2026-04-01T00:00:00+00:00',
                    },
                }]
            }),
            headers=auth_headers,
        )
        assert resp.status_code == 200
        errors = resp.get_json()['errors']
        assert len(errors) == 1
        assert errors[0]['documentInDb']['_deleted'] is True
        assert errors[0]['documentInDb']['id'] == row_id

    def test_conflict_when_updated_at_mismatch(
        self, client, auth_headers, mock_db
    ):
        conn, cur = mock_db
        cur.fetchall.return_value = [{'code': 'mediterranean'}]
        # Server has a row updated at a different time than client thought
        cur.fetchone.return_value = {
            'id': uuid.uuid4(),
            'user_id': uuid.uuid4(),
            'diet_type': 'keto',
            'diet_codes': ['keto'],
            'dietary_restrictions': None,
            'calorie_target': 1800,
            'protein_target_g': None,
            'carb_target_g': None,
            'fat_target_g': None,
            'meal_count_per_day': 3,
            'notes': None,
            'is_active': True,
            'effective_date': datetime(2026, 4, 1).date(),
            'end_date': None,
            'created_at': datetime(2026, 4, 1, tzinfo=timezone.utc),
            'updated_at': datetime(2026, 5, 2, 14, tzinfo=timezone.utc),
            '_deleted': False,
        }

        resp = client.post(
            '/api/v1/dietary-settings/push',
            data=json.dumps({
                'changeRows': [{
                    'newDocumentState': self._new_doc(),
                    'assumedMasterState': {
                        'updated_at': '2026-05-01T00:00:00+00:00',  # stale
                    },
                }]
            }),
            headers=auth_headers,
        )
        assert resp.status_code == 200
        errors = resp.get_json()['errors']
        assert len(errors) == 1

    def test_soft_deletes_via_deleted_flag(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        cur.fetchall.return_value = []  # no diet_codes to validate
        # Server row matches the assumed state → no conflict
        server_updated = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
        cur.fetchone.return_value = {
            'id': uuid.uuid4(),
            'user_id': uuid.uuid4(),
            'diet_type': 'keto',
            'diet_codes': None,
            'dietary_restrictions': None,
            'calorie_target': 1800,
            'protein_target_g': None,
            'carb_target_g': None,
            'fat_target_g': None,
            'meal_count_per_day': 3,
            'notes': None,
            'is_active': True,
            'effective_date': datetime(2026, 4, 1).date(),
            'end_date': None,
            'created_at': datetime(2026, 4, 1, tzinfo=timezone.utc),
            'updated_at': server_updated,
            '_deleted': False,
        }

        resp = client.post(
            '/api/v1/dietary-settings/push',
            data=json.dumps({
                'changeRows': [{
                    'newDocumentState': self._new_doc(
                        diet_codes=[],
                        _deleted=True,
                    ),
                    'assumedMasterState': {
                        'updated_at': server_updated.isoformat(),
                    },
                }]
            }),
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.get_json()['errors'] == []

        executed_sqls = [
            call.args[0] for call in cur.execute.call_args_list if call.args
        ]
        # Soft-delete must SET deleted_at; INSERT/UPDATE main row branch must
        # NOT have run (the if is_deleted: continue path skips it).
        assert any(
            'SET deleted_at = %s' in s for s in executed_sqls
        )

    def test_auto_deactivates_when_pushing_active_row(
        self, client, auth_headers, mock_db
    ):
        """Pushing is_active=true issues the auto-deactivate UPDATE on other active rows."""
        conn, cur = mock_db
        cur.fetchall.return_value = [{'code': 'mediterranean'}]
        cur.fetchone.return_value = None  # no existing row at this id

        resp = client.post(
            '/api/v1/dietary-settings/push',
            data=json.dumps({
                'changeRows': [{
                    'newDocumentState': self._new_doc(is_active=True),
                    'assumedMasterState': None,
                }]
            }),
            headers=auth_headers,
        )
        assert resp.status_code == 200

        executed_sqls = [
            call.args[0] for call in cur.execute.call_args_list if call.args
        ]
        # Find the auto-deactivate UPDATE — it scopes to id <> %s
        # (other active rows, not the one being pushed).
        assert any(
            'is_active = false' in s and 'id <> %s' in s for s in executed_sqls
        )

    def test_rejects_unknown_diet_codes_in_batch(
        self, client, auth_headers, mock_db
    ):
        conn, cur = mock_db
        # Validation finds only one of the two pushed codes
        cur.fetchall.return_value = [{'code': 'mediterranean'}]

        resp = client.post(
            '/api/v1/dietary-settings/push',
            data=json.dumps({
                'changeRows': [
                    {
                        'newDocumentState': self._new_doc(
                            diet_codes=['mediterranean', 'fake_one']
                        ),
                        'assumedMasterState': None,
                    },
                ]
            }),
            headers=auth_headers,
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['error'] == 'Unknown diet_codes'
        assert 'fake_one' in body['unknown']

    def test_requires_change_rows_array(self, client, auth_headers, mock_db):
        resp = client.post(
            '/api/v1/dietary-settings/push',
            data=json.dumps({}),
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_empty_change_rows_returns_empty_errors(
        self, client, auth_headers, mock_db
    ):
        resp = client.post(
            '/api/v1/dietary-settings/push',
            data=json.dumps({'changeRows': []}),
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.get_json()['errors'] == []


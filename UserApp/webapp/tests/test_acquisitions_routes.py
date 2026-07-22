"""
Unit tests for the acquisitions routes (health_inputs blueprint) and the
count-remaining inventory wiring in logging_routes.
"""
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest


def _acq_row(**over):
    row = {
        'tenant_id': 1,
        'id': uuid.uuid4(),
        'sqlite_id': None,
        'user_id': uuid.uuid4(),
        'health_input_id': None,
        'item_name': 'Magnesium',
        'acquired_date': date(2026, 7, 16),
        'quantity': Decimal('90.00'),
        'unit': 'tablets',
        'cost': Decimal('14.99'),
        'brand': 'NOW',
        'vendor': 'Amazon',
        'expiration_date': None,
        'notes': None,
        'created_at': datetime(2026, 7, 16, 12, 0),
        'updated_at': datetime(2026, 7, 16, 12, 0),
        'synced_at': None,
    }
    row.update(over)
    return row


class TestCreateAcquisition:
    def test_requires_acquired_date(self, client, auth_headers, mock_db):
        resp = client.post('/api/v1/acquisitions', headers=auth_headers,
                           data=json.dumps({'item_name': 'Magnesium'}))
        assert resp.status_code == 400
        assert 'acquired_date' in resp.get_json()['error']

    def test_requires_item_name_or_input_id(self, client, auth_headers, mock_db):
        resp = client.post('/api/v1/acquisitions', headers=auth_headers,
                           data=json.dumps({'acquired_date': '2026-07-16'}))
        assert resp.status_code == 400

    def test_bad_date_400(self, client, auth_headers, mock_db):
        resp = client.post('/api/v1/acquisitions', headers=auth_headers,
                           data=json.dumps({'item_name': 'X',
                                            'acquired_date': 'July 16'}))
        assert resp.status_code == 400

    def test_negative_cost_400(self, client, auth_headers, mock_db):
        resp = client.post('/api/v1/acquisitions', headers=auth_headers,
                           data=json.dumps({'item_name': 'X',
                                            'acquired_date': '2026-07-16',
                                            'cost': -5}))
        assert resp.status_code == 400

    def test_freeform_create_201(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        cur.fetchone.return_value = _acq_row()
        resp = client.post('/api/v1/acquisitions', headers=auth_headers,
                           data=json.dumps({'item_name': 'Magnesium',
                                            'acquired_date': '2026-07-16',
                                            'quantity': 90, 'unit': 'tablets',
                                            'cost': 14.99, 'brand': 'NOW',
                                            'vendor': 'Amazon'}))
        assert resp.status_code == 201
        body = resp.get_json()
        assert body['acquisition']['item_name'] == 'Magnesium'
        assert body['acquisition']['cost'] == 14.99
        # No catalog link, no inventory bump
        assert body['remaining'] is None
        assert conn.commit.called

    def test_catalog_create_bumps_inventory_and_returns_remaining(
            self, client, auth_headers, mock_db):
        conn, cur = mock_db
        input_id = uuid.uuid4()
        cur.fetchone.side_effect = [
            {'name': 'Metformin'},                       # catalog lookup
            _acq_row(health_input_id=input_id,
                     item_name='Metformin'),             # INSERT RETURNING
            {'current_quantity': Decimal('120.00')},     # inventory UPDATE
        ]
        resp = client.post('/api/v1/acquisitions', headers=auth_headers,
                           data=json.dumps({'health_input_id': str(input_id),
                                            'acquired_date': '2026-07-16',
                                            'quantity': 30}))
        assert resp.status_code == 201
        body = resp.get_json()
        assert body['acquisition']['item_name'] == 'Metformin'
        assert body['remaining'] == 120.0
        update_sqls = [c.args[0] for c in cur.execute.call_args_list
                       if 'UPDATE health_inputs' in c.args[0]]
        assert update_sqls and 'COALESCE(current_quantity, 0) + %s' in update_sqls[0]

    def test_unknown_catalog_item_404(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        cur.fetchone.return_value = None
        resp = client.post('/api/v1/acquisitions', headers=auth_headers,
                           data=json.dumps({'health_input_id': str(uuid.uuid4()),
                                            'acquired_date': '2026-07-16'}))
        assert resp.status_code == 404


class TestGetAcquisitions:
    def test_list_envelope(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        row = _acq_row()
        row['_total'] = 1
        cur.fetchall.return_value = [row]
        resp = client.get('/api/v1/acquisitions', headers=auth_headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['pagination']['total'] == 1
        assert body['entries'][0]['quantity'] == 90.0
        assert 'tenant_id' not in body['entries'][0]
        assert 'user_id' not in body['entries'][0]

    def test_input_filter_applied(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        cur.fetchall.return_value = []
        input_id = uuid.uuid4()
        resp = client.get(f'/api/v1/acquisitions?health_input_id={input_id}',
                          headers=auth_headers)
        assert resp.status_code == 200
        sql_text = str(cur.execute.call_args.args[0])
        assert 'health_input_id = %s' in sql_text

    def test_bad_input_id_400(self, client, auth_headers, mock_db):
        resp = client.get('/api/v1/acquisitions?health_input_id=nope',
                          headers=auth_headers)
        assert resp.status_code == 400


class TestUpdateDeleteAcquisition:
    def test_update_returns_row(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        cur.fetchone.return_value = _acq_row(brand='Jarrow')
        resp = client.put(f'/api/v1/acquisitions/{uuid.uuid4()}',
                          headers=auth_headers,
                          data=json.dumps({'brand': 'Jarrow'}))
        assert resp.status_code == 200
        assert resp.get_json()['acquisition']['brand'] == 'Jarrow'

    def test_update_unknown_404(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        cur.fetchone.return_value = None
        resp = client.put(f'/api/v1/acquisitions/{uuid.uuid4()}',
                          headers=auth_headers,
                          data=json.dumps({'brand': 'X'}))
        assert resp.status_code == 404

    def test_delete(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        cur.fetchone.return_value = {'id': uuid.uuid4()}
        resp = client.delete(f'/api/v1/acquisitions/{uuid.uuid4()}',
                             headers=auth_headers)
        assert resp.status_code == 200


class TestCountRemainingOnLogging:
    def test_log_health_input_returns_remaining(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        input_id = uuid.uuid4()
        # table_has_column consumes fetchall; fetchone feeds the decrement
        cur.fetchone.side_effect = [
            {'current_quantity': Decimal('29.00')},
        ]
        resp = client.post('/api/v1/log-health-input', headers=auth_headers,
                           data=json.dumps({'input_id': str(input_id),
                                            'timestamp': '2026-07-16T08:00:00',
                                            'dosage': '1 tablet'}))
        assert resp.status_code == 201
        assert resp.get_json()['remaining'] == 29.0
        dec_sqls = [c.args[0] for c in cur.execute.call_args_list
                    if 'GREATEST(current_quantity - 1, 0)' in c.args[0]]
        assert dec_sqls and 'current_quantity IS NOT NULL' in dec_sqls[0]

    def test_log_health_input_null_inventory_stays_null(self, client, auth_headers, mock_db):
        conn, cur = mock_db
        cur.fetchone.side_effect = [
            None,  # decrement matched no row (current_quantity IS NULL)
        ]
        resp = client.post('/api/v1/log-health-input', headers=auth_headers,
                           data=json.dumps({'input_id': str(uuid.uuid4()),
                                            'timestamp': '2026-07-16T08:00:00'}))
        assert resp.status_code == 201
        assert resp.get_json()['remaining'] is None

"""
Unit tests for food blueprint routes.

Tests food items and meals CRUD with mocked DB.
"""
import json
import uuid
from datetime import datetime

import pytest


class TestGetFoodItems:
    def test_returns_items(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        # Simulates count(*) OVER() — every row carries the same _total
        cur.fetchall.return_value = [
            {
                '_total': 1,
                'id': uuid.uuid4(),
                'name': 'Banana',
                'brand': None,
                'barcode': None,
                'calories': 105,
                'protein_g': 1.3,
                'carbs_g': 27.0,
                'fat_g': 0.4,
                'is_favorite': True,
                'created_at': datetime(2026, 1, 1),
                'updated_at': datetime(2026, 1, 1),
            }
        ]

        resp = client.get('/api/v1/food-items', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'entries' in data
        assert 'pagination' in data
        assert len(data['entries']) == 1
        assert data['entries'][0]['name'] == 'Banana'
        assert data['entries'][0]['calories'] == 105
        assert '_total' not in data['entries'][0]
        assert data['pagination']['total'] == 1
        assert data['pagination']['has_more'] is False

    def test_empty_list(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = []

        resp = client.get('/api/v1/food-items', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['entries'] == []
        assert data['pagination']['total'] == 0
        assert data['pagination']['has_more'] is False


class TestCreateFoodItem:
    def test_creates_item(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchone.return_value = {'id': str(uuid.uuid4())}
        payload = {
            'name': 'Apple',
            'calories': 95,
            'protein_g': 0.5,
            'carbs_g': 25.0,
            'fat_g': 0.3,
        }

        resp = client.post(
            '/api/v1/food-items',
            headers=auth_headers,
            data=json.dumps(payload),
        )
        assert resp.status_code == 201
        conn.commit.assert_called()

    def test_requires_name(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        payload = {'calories': 100}

        resp = client.post(
            '/api/v1/food-items',
            headers=auth_headers,
            data=json.dumps(payload),
        )
        assert resp.status_code == 400


class TestUpdateFoodItem:
    def test_updates_item(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.rowcount = 1
        item_id = str(uuid.uuid4())
        payload = {'name': 'Green Apple', 'calories': 80}

        resp = client.put(
            f'/api/v1/food-items/{item_id}',
            headers=auth_headers,
            data=json.dumps(payload),
        )
        assert resp.status_code == 200
        conn.commit.assert_called()


class TestDeleteFoodItem:
    def test_deletes_item(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.rowcount = 1
        item_id = str(uuid.uuid4())

        resp = client.delete(
            f'/api/v1/food-items/{item_id}',
            headers=auth_headers,
        )
        assert resp.status_code == 200
        conn.commit.assert_called()


class TestGetMeals:
    def test_returns_meals(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        # GET /meals uses json_agg — each row has an 'items' field from the JOIN.
        # _total simulates count(*) OVER() — every row carries the same total.
        cur.fetchall.return_value = [
            {
                '_total': 1,
                'id': uuid.uuid4(),
                'name': 'Breakfast Bowl',
                'description': 'Oats + banana + peanut butter',
                'is_favorite': True,
                'items': [
                    {'food_item_id': str(uuid.uuid4()), 'food_name': 'Oats', 'servings': 1},
                ],
            }
        ]

        resp = client.get('/api/v1/meals', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'entries' in data
        assert 'pagination' in data
        assert len(data['entries']) == 1
        assert '_total' not in data['entries'][0]
        assert data['pagination']['total'] == 1
        assert data['pagination']['has_more'] is False


class TestCreateMeal:
    def test_creates_meal(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchone.return_value = {'id': str(uuid.uuid4())}
        payload = {
            'name': 'Lunch Salad',
            'description': 'Mixed greens with chicken',
            'items': [
                {'food_item_id': str(uuid.uuid4()), 'servings': 1},
            ],
        }

        resp = client.post(
            '/api/v1/meals',
            headers=auth_headers,
            data=json.dumps(payload),
        )
        assert resp.status_code == 201

    def test_missing_name_raises_key_error(self, client, mock_db, auth_headers):
        """Route doesn't validate 'name' — bare data['name'] raises KeyError.

        Flask TESTING mode propagates exceptions instead of returning 500.
        """
        conn, cur = mock_db
        payload = {'description': 'Missing name'}

        with pytest.raises(KeyError, match='name'):
            client.post(
                '/api/v1/meals',
                headers=auth_headers,
                data=json.dumps(payload),
            )

"""Tests for document annotation CRUD endpoints."""

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from conftest import TEST_USER_ID, TEST_TENANT_ID


class TestCreateAnnotation:
    """Test POST /api/v1/documents/{doc_id}/annotations."""

    def test_create_annotation(self, client, mock_db, auth_headers):
        """Creates an annotation and returns it."""
        conn, cur = mock_db
        doc_id = str(uuid.uuid4())
        ann_id = uuid.uuid4()
        now = datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc)

        # fetchone for document ownership check
        cur.fetchone.side_effect = [
            {'id': doc_id, 'user_id': TEST_USER_ID},  # doc exists
            {  # INSERT RETURNING
                'id': ann_id,
                'document_id': uuid.UUID(doc_id),
                'author_type': 'user',
                'author_id': uuid.UUID(TEST_USER_ID),
                'page_number': None,
                'body': 'This looks good',
                'created_at': now,
                'updated_at': now,
            },
        ]

        resp = client.post(
            f'/api/v1/documents/{doc_id}/annotations',
            headers=auth_headers,
            json={'body': 'This looks good'},
        )

        assert resp.status_code == 201
        data = resp.get_json()
        assert data['body'] == 'This looks good'
        assert data['author_type'] == 'user'

    def test_create_annotation_missing_body(self, client, mock_db, auth_headers):
        """Returns 400 when body is missing."""
        doc_id = str(uuid.uuid4())

        resp = client.post(
            f'/api/v1/documents/{doc_id}/annotations',
            headers=auth_headers,
            json={},
        )

        assert resp.status_code == 400

    def test_create_annotation_with_page_number(self, client, mock_db, auth_headers):
        """Creates a page-level annotation."""
        conn, cur = mock_db
        doc_id = str(uuid.uuid4())
        ann_id = uuid.uuid4()
        now = datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc)

        cur.fetchone.side_effect = [
            {'id': doc_id, 'user_id': TEST_USER_ID},
            {
                'id': ann_id,
                'document_id': uuid.UUID(doc_id),
                'author_type': 'user',
                'author_id': uuid.UUID(TEST_USER_ID),
                'page_number': 3,
                'body': 'Page 3 note',
                'created_at': now,
                'updated_at': now,
            },
        ]

        resp = client.post(
            f'/api/v1/documents/{doc_id}/annotations',
            headers=auth_headers,
            json={'body': 'Page 3 note', 'page_number': 3},
        )

        assert resp.status_code == 201
        assert resp.get_json()['page_number'] == 3


class TestListAnnotations:
    """Test GET /api/v1/documents/{doc_id}/annotations."""

    def test_list_annotations(self, client, mock_db, auth_headers):
        """Returns paginated annotation list."""
        conn, cur = mock_db
        doc_id = str(uuid.uuid4())
        ann_id = uuid.uuid4()
        now = datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc)

        cur.fetchall.return_value = [
            {
                'id': ann_id,
                'document_id': uuid.UUID(doc_id),
                'author_type': 'user',
                'author_id': uuid.UUID(TEST_USER_ID),
                'author_name': 'Test User',
                'page_number': None,
                'body': 'A note',
                'created_at': now,
                'updated_at': now,
            }
        ]
        cur.fetchone.return_value = {'total': 1}

        resp = client.get(
            f'/api/v1/documents/{doc_id}/annotations',
            headers=auth_headers,
        )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total'] == 1
        assert len(data['annotations']) == 1

    def test_list_annotations_empty(self, client, mock_db, auth_headers):
        """Returns empty list when no annotations exist."""
        conn, cur = mock_db
        doc_id = str(uuid.uuid4())

        cur.fetchall.return_value = []
        cur.fetchone.return_value = {'total': 0}

        resp = client.get(
            f'/api/v1/documents/{doc_id}/annotations',
            headers=auth_headers,
        )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total'] == 0
        assert data['annotations'] == []


class TestUpdateAnnotation:
    """Test PATCH /api/v1/documents/{doc_id}/annotations/{ann_id}."""

    def test_update_own_annotation(self, client, mock_db, auth_headers):
        """Owner can update their own annotation body."""
        conn, cur = mock_db
        doc_id = str(uuid.uuid4())
        ann_id = str(uuid.uuid4())
        now = datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc)

        cur.fetchone.return_value = {
            'id': uuid.UUID(ann_id),
            'body': 'Updated note',
            'updated_at': now,
        }
        cur.rowcount = 1

        resp = client.patch(
            f'/api/v1/documents/{doc_id}/annotations/{ann_id}',
            headers=auth_headers,
            json={'body': 'Updated note'},
        )

        assert resp.status_code == 200
        assert resp.get_json()['body'] == 'Updated note'

    def test_update_annotation_not_found(self, client, mock_db, auth_headers):
        """Returns 404 when annotation doesn't exist or isn't owned by user."""
        conn, cur = mock_db
        doc_id = str(uuid.uuid4())
        ann_id = str(uuid.uuid4())

        cur.fetchone.return_value = None

        resp = client.patch(
            f'/api/v1/documents/{doc_id}/annotations/{ann_id}',
            headers=auth_headers,
            json={'body': 'Updated note'},
        )

        assert resp.status_code == 404


class TestDeleteAnnotation:
    """Test DELETE /api/v1/documents/{doc_id}/annotations/{ann_id}."""

    def test_delete_own_annotation(self, client, mock_db, auth_headers):
        """Author can delete their own annotation."""
        conn, cur = mock_db
        doc_id = str(uuid.uuid4())
        ann_id = str(uuid.uuid4())

        # First fetchone: check annotation exists and user is author
        cur.fetchone.side_effect = [
            {'id': uuid.UUID(ann_id), 'author_id': uuid.UUID(TEST_USER_ID),
             'user_id': uuid.UUID(TEST_USER_ID)},
        ]

        resp = client.delete(
            f'/api/v1/documents/{doc_id}/annotations/{ann_id}',
            headers=auth_headers,
        )

        assert resp.status_code == 200

    def test_delete_annotation_not_found(self, client, mock_db, auth_headers):
        """Returns 404 when annotation doesn't exist."""
        conn, cur = mock_db
        doc_id = str(uuid.uuid4())
        ann_id = str(uuid.uuid4())

        cur.fetchone.return_value = None

        resp = client.delete(
            f'/api/v1/documents/{doc_id}/annotations/{ann_id}',
            headers=auth_headers,
        )

        assert resp.status_code == 404

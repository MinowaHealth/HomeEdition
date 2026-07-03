"""
UserDocs — Document folder routes.

File-system metaphor over document_folders: tree via parent_id, two
auto-seeded system folders per user (Documents, Fax), soft-delete with
transactional cascade to descendants and their documents.
"""
from flask import Blueprint, request, jsonify, g, current_app
from db_driver import sql
from datetime import datetime
import pytz

from utils import require_auth, get_db_connection, get_user_id
import db_manager
import analytics

bp = Blueprint('folders', __name__, url_prefix='/api/v1')

MAX_NAME_LEN = 120


def _clean_name(raw):
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None
    name = raw.strip()
    if not name or len(name) > MAX_NAME_LEN:
        return None
    # Disallow path separators and control chars — these are folder names, not paths
    if any(c in name for c in ('/', '\\', '\x00')):
        return None
    return name


def _serialize_folder(row):
    row['id'] = str(row['id'])
    if row.get('parent_id'):
        row['parent_id'] = str(row['parent_id'])
    for field in ('created_at', 'updated_at', 'deleted_at'):
        if row.get(field):
            row[field] = row[field].isoformat()
    return row


# ==================== LIST (tree) ====================

@bp.route('/folders', methods=['GET'])
@require_auth
def list_folders():
    """Return the caller's folder tree. Excludes trashed unless ?include_trash=1."""
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    include_trash = request.args.get('include_trash') in ('1', 'true', 'yes')

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        if include_trash:
            cur.execute("""
                SELECT id, parent_id, name, is_system, deleted_at,
                       created_at, updated_at
                FROM document_folders
                WHERE tenant_id = %s AND user_id = %s
                ORDER BY is_system DESC, lower(name)
            """, (tenant_id, str(user_id)))
        else:
            cur.execute("""
                SELECT id, parent_id, name, is_system, deleted_at,
                       created_at, updated_at
                FROM document_folders
                WHERE tenant_id = %s AND user_id = %s AND deleted_at IS NULL
                ORDER BY is_system DESC, lower(name)
            """, (tenant_id, str(user_id)))

        rows = cur.fetchall()
        cur.close()
        conn.close()

        folders = [_serialize_folder(dict(r)) for r in rows]
        return jsonify({'folders': folders})

    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/folders', str(user_id))
            return jsonify({'error': 'Query timeout', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("GET /folders failed: %s", e, exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== CREATE ====================

@bp.route('/folders', methods=['POST'])
@require_auth
def create_folder():
    """Create a non-system folder. Body: {name, parent_id?}."""
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    data = request.json or {}

    name = _clean_name(data.get('name'))
    if not name:
        return jsonify({'error': 'Invalid or missing name'}), 400

    parent_id = data.get('parent_id')
    if parent_id == '':
        parent_id = None

    now = datetime.now(pytz.utc)

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Validate parent (if provided) belongs to user and is live
        if parent_id is not None:
            cur.execute("""
                SELECT id FROM document_folders
                WHERE tenant_id = %s AND user_id = %s AND id = %s AND deleted_at IS NULL
            """, (tenant_id, str(user_id), parent_id))
            if not cur.fetchone():
                cur.close()
                conn.close()
                return jsonify({'error': 'Parent folder not found'}), 404

        # Insert — unique index enforces (user, parent, lower(name)) collision
        try:
            cur.execute("""
                INSERT INTO document_folders
                    (tenant_id, user_id, parent_id, name, is_system, created_at, updated_at)
                VALUES (%s, %s, %s, %s, FALSE, %s, %s)
                RETURNING id, parent_id, name, is_system, deleted_at, created_at, updated_at
            """, (tenant_id, str(user_id), parent_id, name, now, now))
            row = cur.fetchone()
        except Exception as e:
            conn.rollback()
            msg = str(e).lower()
            if 'idx_document_folders_unique_name_live' in msg or 'duplicate key' in msg:
                cur.close()
                conn.close()
                return jsonify({'error': 'A folder with that name already exists here'}), 409
            raise

        conn.commit()
        cur.close()
        conn.close()

        analytics.capture('folder_created', {'has_parent': parent_id is not None})
        return jsonify(_serialize_folder(dict(row))), 201

    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/folders', str(user_id))
            return jsonify({'error': 'Query timeout', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("POST /folders failed: %s", e, exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== RENAME / MOVE ====================

@bp.route('/folders/<folder_id>', methods=['PATCH'])
@require_auth
def update_folder(folder_id):
    """Rename or move a folder. Body: {name?, parent_id?}.

    System folders may not be renamed or moved. Moving a folder inside
    its own descendant is rejected (cycle prevention).
    """
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    data = request.json or {}

    if 'name' not in data and 'parent_id' not in data:
        return jsonify({'error': 'No fields to update'}), 400

    new_name = None
    if 'name' in data:
        new_name = _clean_name(data.get('name'))
        if not new_name:
            return jsonify({'error': 'Invalid name'}), 400

    new_parent = data.get('parent_id') if 'parent_id' in data else '__unchanged__'
    if new_parent == '':
        new_parent = None

    now = datetime.now(pytz.utc)

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Load target folder
        cur.execute("""
            SELECT id, parent_id, name, is_system
            FROM document_folders
            WHERE tenant_id = %s AND user_id = %s AND id = %s AND deleted_at IS NULL
        """, (tenant_id, str(user_id), folder_id))
        target = cur.fetchone()
        if not target:
            cur.close()
            conn.close()
            return jsonify({'error': 'Folder not found'}), 404

        if target['is_system']:
            cur.close()
            conn.close()
            return jsonify({'error': 'System folders cannot be renamed or moved'}), 403

        # Validate new parent if moving
        if new_parent != '__unchanged__':
            if new_parent is not None:
                if str(new_parent) == str(folder_id):
                    cur.close()
                    conn.close()
                    return jsonify({'error': 'Folder cannot be its own parent'}), 400

                cur.execute("""
                    SELECT id FROM document_folders
                    WHERE tenant_id = %s AND user_id = %s AND id = %s AND deleted_at IS NULL
                """, (tenant_id, str(user_id), new_parent))
                if not cur.fetchone():
                    cur.close()
                    conn.close()
                    return jsonify({'error': 'Parent folder not found'}), 404

                # Cycle check: new_parent must not be a descendant of folder_id
                cur.execute("""
                    WITH RECURSIVE descendants AS (
                        SELECT id FROM document_folders
                        WHERE tenant_id = %s AND user_id = %s AND id = %s AND deleted_at IS NULL
                        UNION ALL
                        SELECT f.id FROM document_folders f
                        JOIN descendants d ON f.parent_id = d.id
                        WHERE f.tenant_id = %s AND f.deleted_at IS NULL
                    )
                    SELECT 1 FROM descendants WHERE id = %s
                """, (tenant_id, str(user_id), folder_id, tenant_id, new_parent))
                if cur.fetchone():
                    cur.close()
                    conn.close()
                    return jsonify({'error': 'Cannot move a folder inside its own descendant'}), 400

        # Build UPDATE
        set_parts = [sql.SQL("updated_at = %s")]
        params = [now]
        if new_name is not None:
            set_parts.append(sql.SQL("{} = %s").format(sql.Identifier('name')))
            params.append(new_name)
        if new_parent != '__unchanged__':
            set_parts.append(sql.SQL("{} = %s").format(sql.Identifier('parent_id')))
            params.append(new_parent)
        params.extend([tenant_id, str(user_id), folder_id])

        try:
            update_query = sql.SQL("""
                UPDATE document_folders
                SET {set_clause}
                WHERE tenant_id = %s AND user_id = %s AND id = %s AND deleted_at IS NULL
                RETURNING id, parent_id, name, is_system, deleted_at, created_at, updated_at
            """).format(set_clause=sql.SQL(", ").join(set_parts))
            cur.execute(update_query, params)
            row = cur.fetchone()
        except Exception as e:
            conn.rollback()
            msg = str(e).lower()
            if 'idx_document_folders_unique_name_live' in msg or 'duplicate key' in msg:
                cur.close()
                conn.close()
                return jsonify({'error': 'A folder with that name already exists here'}), 409
            raise

        if not row:
            cur.close()
            conn.close()
            return jsonify({'error': 'Folder not found'}), 404

        conn.commit()
        cur.close()
        conn.close()

        return jsonify(_serialize_folder(dict(row)))

    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill(f'/folders/{folder_id}', str(user_id))
            return jsonify({'error': 'Query timeout', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("PATCH /folders/%s failed: %s", folder_id, e, exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== TRASH (soft delete with cascade) ====================

@bp.route('/folders/<folder_id>', methods=['DELETE'])
@require_auth
def trash_folder(folder_id):
    """Soft-delete a folder and all its descendants + documents in one transaction.

    System folders are refused. The timestamp is identical across all rows in
    the cascade so restore can find the subtree by (deleted_at = X).
    """
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    now = datetime.now(pytz.utc)

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, is_system FROM document_folders
            WHERE tenant_id = %s AND user_id = %s AND id = %s AND deleted_at IS NULL
        """, (tenant_id, str(user_id), folder_id))
        target = cur.fetchone()
        if not target:
            cur.close()
            conn.close()
            return jsonify({'error': 'Folder not found'}), 404

        if target['is_system']:
            cur.close()
            conn.close()
            return jsonify({'error': 'System folders cannot be trashed'}), 403

        # Collect the whole live subtree (including the target).
        cur.execute("""
            WITH RECURSIVE subtree AS (
                SELECT id FROM document_folders
                WHERE tenant_id = %s AND id = %s AND deleted_at IS NULL
                UNION ALL
                SELECT f.id FROM document_folders f
                JOIN subtree s ON f.parent_id = s.id
                WHERE f.tenant_id = %s AND f.deleted_at IS NULL
            )
            UPDATE document_folders
            SET deleted_at = %s, updated_at = %s
            WHERE tenant_id = %s AND user_id = %s AND id IN (SELECT id FROM subtree)
            RETURNING id
        """, (tenant_id, folder_id, tenant_id, now, now, tenant_id, str(user_id)))
        folder_ids = [r['id'] for r in cur.fetchall()]

        # Cascade soft-delete to documents that live in any of those folders.
        if folder_ids:
            cur.execute("""
                UPDATE documents
                SET deleted_at = %s, updated_at = %s
                WHERE tenant_id = %s AND user_id = %s AND folder_id = ANY(%s::uuid[]) AND deleted_at IS NULL
            """, (now, now, tenant_id, str(user_id), [str(fid) for fid in folder_ids]))
            doc_count = cur.rowcount
        else:
            doc_count = 0

        conn.commit()
        cur.close()
        conn.close()

        analytics.capture('folder_trashed', {'folders': len(folder_ids), 'documents': doc_count})

        return jsonify({
            'id': str(folder_id),
            'deleted_at': now.isoformat(),
            'folders_trashed': len(folder_ids),
            'documents_trashed': doc_count,
        })

    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill(f'/folders/{folder_id}', str(user_id))
            return jsonify({'error': 'Query timeout', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("DELETE /folders/%s failed: %s", folder_id, e, exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== RESTORE ====================

@bp.route('/folders/<folder_id>/restore', methods=['POST'])
@require_auth
def restore_folder(folder_id):
    """Restore a soft-deleted folder. Restores exactly the subtree that was
    trashed together (same deleted_at timestamp) plus any documents trashed
    in the same cascade."""
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    now = datetime.now(pytz.utc)

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, parent_id, deleted_at FROM document_folders
            WHERE tenant_id = %s AND user_id = %s AND id = %s AND deleted_at IS NOT NULL
        """, (tenant_id, str(user_id), folder_id))
        target = cur.fetchone()
        if not target:
            cur.close()
            conn.close()
            return jsonify({'error': 'Trashed folder not found'}), 404

        trashed_at = target['deleted_at']

        # Parent must be live (or NULL). If parent was trashed in a different
        # operation, the caller must restore it first.
        if target['parent_id'] is not None:
            cur.execute("""
                SELECT 1 FROM document_folders
                WHERE tenant_id = %s AND user_id = %s AND id = %s AND deleted_at IS NULL
            """, (tenant_id, str(user_id), target['parent_id']))
            if not cur.fetchone():
                cur.close()
                conn.close()
                return jsonify({'error': 'Parent folder is not available; restore it first'}), 409

        # Restore the subtree trashed together with this folder.
        cur.execute("""
            WITH RECURSIVE subtree AS (
                SELECT id FROM document_folders
                WHERE tenant_id = %s AND id = %s AND deleted_at = %s
                UNION ALL
                SELECT f.id FROM document_folders f
                JOIN subtree s ON f.parent_id = s.id
                WHERE f.tenant_id = %s AND f.deleted_at = %s
            )
            UPDATE document_folders
            SET deleted_at = NULL, updated_at = %s
            WHERE tenant_id = %s AND user_id = %s AND id IN (SELECT id FROM subtree)
            RETURNING id
        """, (tenant_id, folder_id, trashed_at, tenant_id, trashed_at, now, tenant_id, str(user_id)))
        folder_ids = [r['id'] for r in cur.fetchall()]

        if folder_ids:
            cur.execute("""
                UPDATE documents
                SET deleted_at = NULL, updated_at = %s
                WHERE tenant_id = %s AND user_id = %s AND folder_id = ANY(%s::uuid[]) AND deleted_at = %s
            """, (now, tenant_id, str(user_id), [str(fid) for fid in folder_ids], trashed_at))
            doc_count = cur.rowcount
        else:
            doc_count = 0

        conn.commit()
        cur.close()
        conn.close()

        analytics.capture('folder_restored', {'folders': len(folder_ids), 'documents': doc_count})

        return jsonify({
            'id': str(folder_id),
            'folders_restored': len(folder_ids),
            'documents_restored': doc_count,
        })

    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill(f'/folders/{folder_id}/restore', str(user_id))
            return jsonify({'error': 'Query timeout', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("POST /folders/%s/restore failed: %s", folder_id, e, exc_info=True)
        return jsonify({'error': str(e)}), 500

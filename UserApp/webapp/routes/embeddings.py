"""
Embedding sync and semantic search routes.

Blueprint for:
- POST /api/v1/sync-embeddings  — mobile upload of pre-computed vectors
- POST /api/v1/semantic-search  — similarity search across embedded tables
"""
from flask import Blueprint, request, jsonify, g, current_app
from datetime import datetime

from db_driver import sql
import pytz
import uuid

from utils import require_auth, get_db_connection, get_user_id, local_to_utc, table_has_column
import analytics

bp = Blueprint('embeddings', __name__, url_prefix='/api/v1')

MAX_EMBEDDINGS_PER_REQUEST = 100


# ==================== SYNC EMBEDDINGS ====================

@bp.route('/sync-embeddings', methods=['POST'])
@require_auth
def sync_embeddings():
    """Accept pre-computed embeddings from mobile and/or generate server-side.

    Request: {
        device_capabilities: {device_id, can_embed, embed_model, ...},
        embeddings: [{table, record_id, content, embedding, text_hash}, ...]
    }
    """
    from embedding_utils import (
        EMBEDDING_TABLES, get_embedding, validate_embedding_vector,
        register_pgvector, set_ivfflat_probes, EMBEDDING_DIMENSIONS,
    )

    payload = request.get_json(silent=True) or {}
    device_caps = payload.get('device_capabilities') or {}
    embeddings_list = payload.get('embeddings') or []

    if not isinstance(embeddings_list, list):
        return jsonify({'error': 'embeddings must be an array'}), 400

    if len(embeddings_list) > MAX_EMBEDDINGS_PER_REQUEST:
        return jsonify({
            'error': f'Max {MAX_EMBEDDINGS_PER_REQUEST} embeddings per request'
        }), 400

    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    conn = get_db_connection()
    register_pgvector(conn)
    set_ivfflat_probes(conn)
    # Shim sets dict-row factory at connection level — no per-cursor override.
    cur = conn.cursor()

    # --- 1. Upsert user_devices ---
    _upsert_device(cur, conn, tenant_id, user_id, device_caps)

    # --- 2. Process embeddings ---
    processed = 0
    embedded_server = 0
    embedded_client = 0
    errors = []

    for idx, item in enumerate(embeddings_list):
        if not isinstance(item, dict):
            errors.append({'index': idx, 'error': 'Item must be an object'})
            continue

        table = item.get('table')
        record_id = item.get('record_id')
        content = item.get('content')
        client_embedding = item.get('embedding')

        # Validate table is in whitelist
        if table not in EMBEDDING_TABLES:
            errors.append({'index': idx, 'error': f'Unknown table: {table}'})
            continue

        if not record_id:
            errors.append({'index': idx, 'error': 'Missing record_id'})
            continue

        table_config = EMBEDDING_TABLES[table]
        embed_column = table_config['embed_column']

        # Check column exists (safe before migration)
        if not table_has_column(conn, table, embed_column):
            errors.append({
                'index': idx,
                'error': f'Column {embed_column} not yet available on {table}',
            })
            continue

        # Determine embedding source
        embedding_to_store = None
        source = None

        if client_embedding and validate_embedding_vector(
            client_embedding, EMBEDDING_DIMENSIONS
        ):
            embedding_to_store = client_embedding
            source = 'client'
        elif content:
            embedding_to_store = get_embedding(content)
            source = 'server' if embedding_to_store else None
        else:
            errors.append({
                'index': idx,
                'error': 'No embedding and no content provided',
            })
            continue

        if embedding_to_store is None:
            errors.append({
                'index': idx,
                'error': 'Server-side embedding unavailable',
            })
            continue

        # UPDATE the embedding column on the existing row. record_id is
        # client-supplied, so the WHERE must scope to the authenticated user —
        # with no RLS this predicate is the only thing preventing one household
        # member from overwriting another's embedding.
        try:
            cur.execute(
                sql.SQL(
                    "UPDATE {table} SET {embed_column} = %s::vector "
                    "WHERE tenant_id = %s AND user_id = %s AND id = %s"
                ).format(
                    table=sql.Identifier(table),
                    embed_column=sql.Identifier(embed_column),
                ),
                (str(embedding_to_store), tenant_id, user_id,
                 uuid.UUID(str(record_id))),
            )

            if cur.rowcount == 0:
                errors.append({
                    'index': idx,
                    'error': f'Record not found in {table}',
                })
                continue

            # Commit per item so a later failure doesn't rollback prior successes
            conn.commit()
            processed += 1
            if source == 'server':
                embedded_server += 1
            else:
                embedded_client += 1

        except Exception as exc:
            conn.rollback()
            current_app.logger.warning(
                "sync_embedding_update_failed table=%s record=%s error=%s",
                table, record_id, exc,
            )
            errors.append({'index': idx, 'error': 'Database update failed'})
            continue
    cur.close()
    conn.close()

    analytics.capture('embeddings_synced', {
        'record_count': processed,
        'embedded_server_side': embedded_server,
        'embedded_client_side': embedded_client,
        'error_count': len(errors),
    })

    return jsonify({
        'processed': processed,
        'embedded_server_side': embedded_server,
        'embedded_client_side': embedded_client,
        'errors': errors,
    }), 200


# ==================== SEMANTIC SEARCH ====================

@bp.route('/semantic-search', methods=['POST'])
@require_auth
def semantic_search():
    """Search across embedding-enabled tables by semantic similarity.

    Request: {
        query: "text" OR query_embedding: [floats],
        tables: ["health_observations", ...],  // optional
        limit: 5,           // max 20
        min_similarity: 0.7, // cosine similarity threshold
        date_after: "ISO"   // optional recency filter
    }
    """
    from embedding_utils import (
        EMBEDDING_TABLES, TIER1_TABLES, get_embedding,
        validate_embedding_vector, register_pgvector, set_ivfflat_probes,
        EMBEDDING_DIMENSIONS,
    )

    payload = request.get_json(silent=True) or {}
    query_text = payload.get('query')
    query_embedding = payload.get('query_embedding')
    tables = payload.get('tables') or TIER1_TABLES
    date_after = payload.get('date_after')

    try:
        limit = min(int(payload.get('limit', 5)), 20)
        min_similarity = float(payload.get('min_similarity', 0.7))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid limit or min_similarity'}), 400

    # Must have either query text or pre-computed embedding
    if not query_text and not query_embedding:
        return jsonify({'error': 'Provide query or query_embedding'}), 400

    # Validate tables
    for t in tables:
        if t not in EMBEDDING_TABLES:
            return jsonify({'error': f'Unknown table: {t}'}), 400

    # Get or validate query vector
    query_embedded_by = None
    if query_embedding:
        if not validate_embedding_vector(query_embedding, EMBEDDING_DIMENSIONS):
            return jsonify({'error': 'Invalid query_embedding dimensions'}), 400
        query_vec = query_embedding
        query_embedded_by = 'client'
    else:
        query_vec = get_embedding(query_text)
        if query_vec is None:
            return jsonify({'error': 'Embedding service unavailable'}), 503
        query_embedded_by = 'server'

    # Parse date_after
    date_filter = None
    if date_after:
        try:
            date_filter = local_to_utc(date_after)
        except Exception:
            return jsonify({'error': 'Invalid date_after format'}), 400

    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    conn = get_db_connection()
    register_pgvector(conn)
    set_ivfflat_probes(conn)
    # Shim sets dict-row factory at connection level — no per-cursor override.
    cur = conn.cursor()

    all_results = []
    query_vec_str = str(query_vec)
    # Cosine distance threshold: 1 - similarity (e.g., 0.7 similarity -> 0.3 distance)
    max_distance = 1.0 - min_similarity

    for table in tables:
        config = EMBEDDING_TABLES[table]
        embed_col = config['embed_column']
        ts_col = config['timestamp_column']
        display_col = config.get('display_column') or config.get('text_column') or 'content'

        if not table_has_column(conn, table, embed_col):
            continue

        # Build WHERE conditions — per-user scoping first: search must never
        # return another household member's rows (no RLS backstop).
        conditions = [
            sql.SQL("tenant_id = %s"),
            sql.SQL("user_id = %s"),
            sql.SQL("{} IS NOT NULL").format(sql.Identifier(embed_col)),
        ]
        params = [tenant_id, user_id]

        if date_filter:
            conditions.append(sql.SQL("{} >= %s").format(sql.Identifier(ts_col)))
            params.append(date_filter)

        # CTE computes distance once; cosine distance <=> : 0 = identical, 2 = opposite
        search_query = sql.SQL(
            "WITH scored AS ("
            "  SELECT id, {display_col} AS content, {ts_col} AS timestamp, "
            "    {embed_col} <=> %s::vector AS distance "
            "  FROM {table} "
            "  WHERE {where_clause}"
            ") "
            "SELECT id, content, timestamp, 1 - distance AS similarity "
            "FROM scored "
            "WHERE distance <= %s "
            "ORDER BY distance "
            "LIMIT %s"
        ).format(
            display_col=sql.Identifier(display_col),
            ts_col=sql.Identifier(ts_col),
            embed_col=sql.Identifier(embed_col),
            table=sql.Identifier(table),
            where_clause=sql.SQL(" AND ").join(conditions),
        )
        query_params = [query_vec_str] + params + [max_distance, limit]

        try:
            cur.execute(search_query, query_params)
            rows = cur.fetchall()
            for row in rows:
                all_results.append({
                    'table': table,
                    'id': str(row['id']),
                    'content': row['content'],
                    'similarity': round(float(row['similarity']), 4),
                    'timestamp': (
                        row['timestamp'].isoformat()
                        if hasattr(row.get('timestamp'), 'isoformat')
                        else str(row['timestamp']) if row.get('timestamp') else None
                    ),
                })
        except Exception as exc:
            current_app.logger.warning(
                "semantic_search_table_failed table=%s error=%s", table, exc,
            )
            continue

    cur.close()
    conn.close()

    # Sort all results by similarity descending
    all_results.sort(key=lambda r: r['similarity'], reverse=True)

    analytics.capture('semantic_search_performed', {
        'result_count': len(all_results[:limit]),
        'table_count': len(tables),
        'query_embedded_by': query_embedded_by,
    })

    return jsonify({
        'results': all_results[:limit],
        'query_embedded_by': query_embedded_by,
    }), 200


# ==================== HELPERS ====================

def _upsert_device(cur, conn, tenant_id: int, user_id, device_caps: dict):
    """Upsert user_devices from device_capabilities payload.

    Uses INSERT ... ON CONFLICT to create-or-update the device record.
    Silently skips if device_capabilities is empty (webapp clients).
    """
    device_id = device_caps.get('device_id')
    if not device_id:
        return

    if not table_has_column(conn, 'user_devices', 'device_id'):
        return  # Table not migrated yet

    try:
        now = datetime.now(pytz.utc)
        cur.execute("""
            INSERT INTO user_devices
                (tenant_id, user_id, device_id, device_name, platform, os_version,
                 app_version, device_model, ram_mb,
                 can_embed, embed_model, embed_model_version, embed_dimensions,
                 first_seen_at, last_seen_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, user_id, device_id) DO UPDATE SET
                device_name = COALESCE(EXCLUDED.device_name, user_devices.device_name),
                platform = COALESCE(EXCLUDED.platform, user_devices.platform),
                os_version = COALESCE(EXCLUDED.os_version, user_devices.os_version),
                app_version = COALESCE(EXCLUDED.app_version, user_devices.app_version),
                device_model = COALESCE(EXCLUDED.device_model, user_devices.device_model),
                ram_mb = COALESCE(EXCLUDED.ram_mb, user_devices.ram_mb),
                can_embed = EXCLUDED.can_embed,
                embed_model = EXCLUDED.embed_model,
                embed_model_version = EXCLUDED.embed_model_version,
                embed_dimensions = EXCLUDED.embed_dimensions,
                last_seen_at = EXCLUDED.last_seen_at
        """, (
            tenant_id, user_id, device_id,
            device_caps.get('device_name'),
            device_caps.get('platform'),
            device_caps.get('os_version'),
            device_caps.get('app_version'),
            device_caps.get('device_model'),
            device_caps.get('ram_mb'),
            device_caps.get('can_embed', False),
            device_caps.get('embed_model'),
            device_caps.get('embed_model_version'),
            device_caps.get('embed_dimensions'),
            now, now,
        ))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        current_app.logger.warning(
            "device_upsert_failed device_id=%s error=%s", device_id, exc,
        )

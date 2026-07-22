"""
Search routes — semantic + keyword search across user data.

GET /api/v1/search?q=...&scope=...&k=5[&from=YYYY-MM-DD&to=YYYY-MM-DD]

Unlike /semantic-search (POST, low-level per-table tuning), this is a
convenience endpoint sized for the MCP search_my_data tool: one query,
one scope keyword, bounded-k results across a small curated set of
embedding-enabled tables. Each per-table read is scoped to the
authenticated user (tenant_id + user_id) at the application layer.

If Ollama is unreachable, we fall back to ILIKE keyword matching on the
display columns so the endpoint never 5xxs on an infra hiccup. The
`mode` field on each result (`semantic` | `keyword`) tells the caller
which path produced the hit.
"""
from datetime import date, timedelta

from flask import Blueprint, request, jsonify, g, current_app
from db_driver import sql

from utils import (
    require_auth,
    get_user_db_connection,
    get_user_id,
    table_has_column,
    local_to_utc,
)
import db_manager

bp = Blueprint('search', __name__, url_prefix='/api/v1')


# Scope -> list of EMBEDDING_TABLES keys. Keep this curated — the MCP tool
# uses scope for intent ("search my notes" vs "search documents") and we
# want predictable result sets. "all" = the union.
_SCOPES = {
    'all': [
        'health_observations',
        'health_inputs',
        'health_conditions',
        'health_allergies',
        'health_food_itemsv2',
        'document_annotations',
    ],
    'observations': ['health_observations'],
    'inputs': ['health_inputs'],
    'conditions': ['health_conditions'],
    'allergies': ['health_allergies'],
    'food': ['health_food_itemsv2'],
    'documents': ['document_annotations'],
    'notes': ['health_observations'],
}


@bp.route('/search', methods=['GET'])
@require_auth
def search_user_data():
    """Semantic-first search across a scoped set of user-owned tables.

    Query params:
        q       (required) search text
        scope   (optional) one of: all, observations, inputs,
                           conditions, allergies, food, documents, notes.
                           Defaults to "all".
        k       (optional) top-K overall, 1..25, default 5
        from    (optional) YYYY-MM-DD — only return rows with timestamp >= from
        to      (optional) YYYY-MM-DD — only return rows with timestamp <= to

    Response:
        {
          "query": "...",
          "scope": "all",
          "mode": "semantic" | "keyword",
          "results": [
            {"table": "...", "id": "...", "content": "...",
             "timestamp": "ISO8601" | null, "similarity": 0.0..1.0 | null,
             "mode": "semantic" | "keyword"}
          ]
        }

    Each per-table read is scoped to the authenticated user
    (tenant_id + user_id) at the application layer — no cross-user leakage.
    """
    from embedding_utils import (
        EMBEDDING_TABLES, EMBEDDING_DIMENSIONS,
        get_embedding, validate_embedding_vector,
        register_pgvector, set_ivfflat_probes,
    )

    q = (request.args.get('q') or '').strip()
    if not q:
        return jsonify({'error': 'q is required'}), 400
    if len(q) > 500:
        return jsonify({'error': 'q must be 500 characters or fewer'}), 400

    scope = (request.args.get('scope') or 'all').lower()
    if scope not in _SCOPES:
        return jsonify({
            'error': f'unknown scope: {scope}',
            'supported': sorted(_SCOPES.keys()),
        }), 400

    try:
        k = int(request.args.get('k', 5))
    except (TypeError, ValueError):
        return jsonify({'error': 'k must be an integer'}), 400
    if k < 1 or k > 25:
        return jsonify({'error': 'k must be between 1 and 25'}), 400

    from_str = request.args.get('from')
    to_str = request.args.get('to')
    from_filter = None
    to_filter = None
    try:
        if from_str:
            from_filter = local_to_utc(from_str)
        if to_str:
            # Exclusive next-day bound so the `to` day is fully included.
            # Localize the next calendar day rather than adding 24h to a
            # UTC instant — the latter drifts an hour across DST.
            to_filter = local_to_utc(
                (date.fromisoformat(to_str[:10]) + timedelta(days=1)).isoformat())
    except Exception:
        return jsonify({'error': 'from/to must be ISO YYYY-MM-DD'}), 400

    tables = _SCOPES[scope]
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    # Try semantic path first. If the embedding service is down, fall back
    # to keyword — we still want usable results.
    query_vec = get_embedding(q)
    mode = 'semantic' if query_vec else 'keyword'
    if query_vec and not validate_embedding_vector(query_vec, EMBEDDING_DIMENSIONS):
        query_vec = None
        mode = 'keyword'

    conn = get_user_db_connection()
    cur = conn.cursor()

    try:
        if query_vec:
            register_pgvector(conn)
            set_ivfflat_probes(conn)

        results: list = []
        for table in tables:
            config = EMBEDDING_TABLES.get(table)
            if not config:
                continue
            embed_col = config['embed_column']
            ts_col = config['timestamp_column']
            display_col = (
                config.get('display_column')
                or config.get('text_column')
                or 'content'
            )

            if not table_has_column(conn, table, display_col):
                continue

            conditions = [sql.SQL("tenant_id = %s AND user_id = %s")]
            params: list = [tenant_id, user_id]

            if from_filter:
                conditions.append(sql.SQL("{} >= %s").format(sql.Identifier(ts_col)))
                params.append(from_filter)
            if to_filter:
                conditions.append(sql.SQL("{} < %s").format(sql.Identifier(ts_col)))
                params.append(to_filter)

            if query_vec and table_has_column(conn, table, embed_col):
                # Semantic path
                conditions.append(
                    sql.SQL("{} IS NOT NULL").format(sql.Identifier(embed_col))
                )
                where_sql = sql.SQL(" AND ").join(conditions)
                query_sql = sql.SQL(
                    "SELECT id, {display} AS content, {ts} AS ts, "
                    "1 - ({embed} <=> %s::vector) AS similarity "
                    "FROM {table} "
                    "WHERE {where} "
                    "ORDER BY {embed} <=> %s::vector "
                    "LIMIT %s"
                ).format(
                    display=sql.Identifier(display_col),
                    ts=sql.Identifier(ts_col),
                    embed=sql.Identifier(embed_col),
                    table=sql.Identifier(table),
                    where=where_sql,
                )
                vec_str = str(query_vec)
                try:
                    cur.execute(query_sql, [vec_str] + params + [vec_str, k])
                    rows = cur.fetchall()
                except Exception as exc:
                    current_app.logger.warning(
                        "search_semantic_failed table=%s error=%s", table, exc,
                    )
                    rows = []

                for r in rows:
                    results.append({
                        'table': table,
                        'id': str(r['id']),
                        'content': r['content'],
                        'timestamp': r['ts'].isoformat() if hasattr(r.get('ts'), 'isoformat') else None,
                        'similarity': round(float(r['similarity']), 4) if r.get('similarity') is not None else None,
                        'mode': 'semantic',
                    })
            else:
                # Keyword fallback — ILIKE on the display column
                like_cond = sql.SQL("{} ILIKE %s").format(sql.Identifier(display_col))
                conditions.append(like_cond)
                where_sql = sql.SQL(" AND ").join(conditions) if conditions else sql.SQL("true")
                query_sql = sql.SQL(
                    "SELECT id, {display} AS content, {ts} AS ts "
                    "FROM {table} "
                    "WHERE {where} "
                    "ORDER BY {ts} DESC "
                    "LIMIT %s"
                ).format(
                    display=sql.Identifier(display_col),
                    ts=sql.Identifier(ts_col),
                    table=sql.Identifier(table),
                    where=where_sql,
                )
                like_params = params + [f"%{q}%", k]
                try:
                    cur.execute(query_sql, like_params)
                    rows = cur.fetchall()
                except Exception as exc:
                    current_app.logger.warning(
                        "search_keyword_failed table=%s error=%s", table, exc,
                    )
                    rows = []

                for r in rows:
                    results.append({
                        'table': table,
                        'id': str(r['id']),
                        'content': r['content'],
                        'timestamp': r['ts'].isoformat() if hasattr(r.get('ts'), 'isoformat') else None,
                        'similarity': None,
                        'mode': 'keyword',
                    })

        # Rank: semantic hits by similarity desc first, keyword hits after
        # (by timestamp desc). Semantic always wins tie-breaks.
        semantic_hits = sorted(
            (r for r in results if r.get('similarity') is not None),
            key=lambda r: float(r['similarity']),
            reverse=True,
        )
        keyword_hits = sorted(
            (r for r in results if r.get('similarity') is None),
            key=lambda r: r.get('timestamp') or '',
            reverse=True,
        )
        results = semantic_hits + keyword_hits

        return jsonify({
            'query': q,
            'scope': scope,
            'mode': mode,
            'applied': {'from': from_str, 'to': to_str},
            'results': results[:k],
        })

    except Exception as e:
        current_app.logger.error("search GET FAILED: %s", e)
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/search', str(g.user.get('user_id', 'anon')))
            return jsonify({
                'error': 'Query took too long and was cancelled',
                'code': 'QUERY_TIMEOUT',
            }), 503
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()

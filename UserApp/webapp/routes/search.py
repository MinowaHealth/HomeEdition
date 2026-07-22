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
        'documents',
        'document_annotations',
    ],
    'observations': ['health_observations'],
    'inputs': ['health_inputs'],
    'conditions': ['health_conditions'],
    'allergies': ['health_allergies'],
    'food': ['health_food_itemsv2'],
    'documents': ['documents', 'document_annotations'],
    'notes': ['health_observations'],
}

_MODES = ('auto', 'semantic', 'keyword')


def _doc_links(doc_id: str) -> dict:
    """Session-gated view links for a document. Relative paths — MCP
    absolutizes with its APP_BASE_URL; the SPA is same-origin."""
    return {
        'web': f'/?activity=documents&doc={doc_id}',
        'download': f'/api/v1/documents/{doc_id}/download',
    }


def _enrich_hits(cur, results: list, q: str, keyword_fts: bool) -> None:
    """Attach document metadata + view links to document/annotation hits.

    One batched query per table. Mutates `results` in place. RLS scopes
    every read, so enrichment can never leak another user's metadata.
    """
    doc_ids = [r['id'] for r in results if r['table'] == 'documents']
    ann_ids = [r['id'] for r in results if r['table'] == 'document_annotations']

    if doc_ids:
        cur.execute("""
            SELECT id, title, filename, mime_type, source,
                   left(coalesce(ocr_text_full, ''), 300) AS lead_text
            FROM documents WHERE id = ANY(%s::uuid[])
        """, (doc_ids,))
        meta = {str(r['id']): r for r in cur.fetchall()}

        matched_pages: dict = {}
        if keyword_fts:
            # Which pages matched? On-the-fly tsvector over this hit set's
            # pages only — no page-level index needed.
            cur.execute("""
                SELECT document_id, page_number FROM document_pages
                WHERE document_id = ANY(%s::uuid[])
                  AND to_tsvector('english', coalesce(ocr_text, ''))
                      @@ websearch_to_tsquery('english', %s)
                ORDER BY document_id, page_number
            """, (doc_ids, q))
            for r in cur.fetchall():
                pages = matched_pages.setdefault(str(r['document_id']), [])
                if len(pages) < 5:
                    pages.append(r['page_number'])

        for hit in results:
            if hit['table'] != 'documents':
                continue
            m = meta.get(hit['id'])
            if m:
                hit['title'] = m['title']
                hit['filename'] = m['filename']
                hit['mime_type'] = m['mime_type']
                hit['source'] = m['source']
                if not hit.get('snippet'):
                    hit['snippet'] = m['lead_text']
            hit['matched_pages'] = matched_pages.get(hit['id'], [])
            hit['links'] = _doc_links(hit['id'])

    if ann_ids:
        cur.execute("""
            SELECT id, document_id FROM document_annotations
            WHERE id = ANY(%s::uuid[])
        """, (ann_ids,))
        ann_docs = {str(r['id']): str(r['document_id']) for r in cur.fetchall()}
        for hit in results:
            if hit['table'] != 'document_annotations':
                continue
            doc_id = ann_docs.get(hit['id'])
            if doc_id:
                hit['document_id'] = doc_id
                hit['links'] = _doc_links(doc_id)


@bp.route('/search', methods=['GET'])
@require_auth
def search_user_data():
    """Semantic-first search across a scoped set of user-owned tables.

    Query params:
        q       (required) search text
        scope   (optional) one of: all, observations, inputs, conditions,
                           allergies, food, documents, notes. Defaults to "all".
        mode    (optional) auto (default; semantic with keyword fallback),
                           semantic (503 if embeddings down), keyword
                           (FTS/ILIKE only, no embedding call)
        k       (optional) top-K overall, 1..25, default 5
        from    (optional) YYYY-MM-DD — only return rows with timestamp >= from
        to      (optional) YYYY-MM-DD — only return rows with timestamp <= to

    Document hits ('documents' / 'document_annotations' tables) are enriched
    with title/filename/mime_type/source, a snippet (ts_headline «» markers
    in keyword mode, leading text otherwise), matched_pages (keyword mode),
    and session-gated view links {web, download}.

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

    req_mode = (request.args.get('mode') or 'auto').lower()
    if req_mode not in _MODES:
        return jsonify({
            'error': f'unknown mode: {req_mode}',
            'supported': list(_MODES),
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

    # Mode selection. auto = semantic with keyword fallback (historic
    # behavior); semantic = hard requirement (503 if embeddings down);
    # keyword = skip the embedding call entirely.
    query_vec = None
    if req_mode in ('auto', 'semantic'):
        query_vec = get_embedding(q)
        if query_vec and not validate_embedding_vector(query_vec, EMBEDDING_DIMENSIONS):
            query_vec = None
    if req_mode == 'semantic' and not query_vec:
        return jsonify({
            'error': 'Embedding service unavailable — retry with mode=keyword or mode=auto',
            'code': 'EMBEDDING_UNAVAILABLE',
        }), 503
    mode = 'semantic' if query_vec else 'keyword'

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

            if table == 'documents':
                # Soft-deleted docs stay out of search (list route does the same).
                conditions.append(sql.SQL("deleted_at IS NULL"))
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
            elif table == 'documents' and table_has_column(conn, table, 'fts'):
                # Keyword mode with real FTS: websearch syntax, rank ordering,
                # ts_headline snippet with plain-text «» markers (no HTML —
                # clients render the markers as highlights themselves).
                where_sql = sql.SQL(" AND ").join(
                    [sql.SQL("fts @@ websearch_to_tsquery('english', %s)")] + conditions
                )
                query_sql = sql.SQL(
                    "SELECT id, {display} AS content, {ts} AS ts, "
                    "ts_rank_cd(fts, websearch_to_tsquery('english', %s)) AS rank, "
                    "ts_headline('english', left(coalesce(ocr_text_full, ''), 20000), "
                    "  websearch_to_tsquery('english', %s), "
                    "  'StartSel=«, StopSel=», MaxFragments=2, MaxWords=25') AS snippet "
                    "FROM {table} "
                    "WHERE {where} "
                    "ORDER BY rank DESC "
                    "LIMIT %s"
                ).format(
                    display=sql.Identifier(display_col),
                    ts=sql.Identifier(ts_col),
                    table=sql.Identifier(table),
                    where=where_sql,
                )
                try:
                    cur.execute(query_sql, [q, q, q] + params + [k])
                    rows = cur.fetchall()
                except Exception as exc:
                    current_app.logger.warning(
                        "search_fts_failed table=%s error=%s", table, exc,
                    )
                    rows = []

                for r in rows:
                    results.append({
                        'table': table,
                        'id': str(r['id']),
                        'content': r['content'],
                        'timestamp': r['ts'].isoformat() if hasattr(r.get('ts'), 'isoformat') else None,
                        'similarity': None,
                        'rank': round(float(r['rank']), 4) if r.get('rank') is not None else None,
                        'snippet': r.get('snippet'),
                        'mode': 'keyword',
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

        # Rank: semantic hits by similarity desc first; keyword hits after,
        # FTS-ranked hits by rank desc, then plain ILIKE hits by recency.
        semantic_hits = sorted(
            (r for r in results if r.get('similarity') is not None),
            key=lambda r: float(r['similarity']),
            reverse=True,
        )
        keyword_hits = sorted(
            (r for r in results if r.get('similarity') is None),
            key=lambda r: (r.get('rank') or 0.0, r.get('timestamp') or ''),
            reverse=True,
        )
        results = (semantic_hits + keyword_hits)[:k]

        # Attach document metadata + view links to document/annotation hits.
        _enrich_hits(cur, results, q, keyword_fts=(mode == 'keyword'))

        return jsonify({
            'query': q,
            'scope': scope,
            'mode': mode,
            'requested_mode': req_mode,
            'applied': {'from': from_str, 'to': to_str},
            'results': results,
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

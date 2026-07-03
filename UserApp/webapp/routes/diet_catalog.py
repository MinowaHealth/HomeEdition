"""
Diet Catalog routes (read-only).

Surfaces the 23-row diet_catalog reference table seeded by the schema
init script (Diets-Plan2.md Phase 1). Mobile clients cache the full list
for offline diet classification; web/UI uses the list to populate the
dietary-settings picker.

The table has no RLS (reference data); we still scope reads by tenant_id
for forward-compat with per-tenant catalog overrides — only tenant=1 has
rows today.

Endpoints:
  - GET /api/v1/diet-catalog            (list; optional ?category=... filter)
  - GET /api/v1/diet-catalog/<code>     (single diet detail)
  - GET /api/v1/diet-catalog/pull       (RxDB pull, checkpoint-ordered)

The full JSONB excludes/nutrient_targets are returned because the mobile
app needs them for offline diet-flag derivation per Diets-Plan2.md §5.

There is no /diet-catalog/push — the catalog is reference data, edited
only via init scripts and migrations. Mobile clients pull and cache;
they do not author rows.
"""
import json

from flask import Blueprint, request, jsonify, g

from utils import require_auth, get_db_connection

bp = Blueprint('diet_catalog', __name__, url_prefix='/api/v1')

VALID_CATEGORIES = {'exclusion', 'nutrient_pattern', 'medical', 'lifestyle'}

PULL_DEFAULT_BATCH = 100
PULL_MAX_BATCH = 1000

# Columns selected for both list and detail. `created_at` is omitted from
# list responses (it's metadata) but included in detail responses.
_LIST_COLUMNS = (
    "code, display_name, category, description, excludes, nutrient_targets, "
    "parent_diet_code, evidence_level, is_clinical, derivation_tier, notes"
)


def _serialize_row(row: dict) -> dict:
    """Normalize a diet_catalog row for JSON.

    JSONB columns come back as Python dicts already; nothing to coerce.
    Booleans, text, and text[] all serialize directly.
    """
    if row.get('created_at'):
        row['created_at'] = row['created_at'].isoformat()
    return row


@bp.route('/diet-catalog', methods=['GET'])
@require_auth
def list_diet_catalog():
    """List the diet catalog.

    Optional query params:
      category — one of exclusion / nutrient_pattern / medical / lifestyle
    """
    tenant_id = g.user.get('tenant_id', 1)
    category = request.args.get('category')

    if category and category not in VALID_CATEGORIES:
        return jsonify({
            'error': 'invalid category',
            'valid': sorted(VALID_CATEGORIES),
        }), 400

    conn = get_db_connection()
    cur = conn.cursor()

    if category:
        cur.execute(
            f"SELECT {_LIST_COLUMNS} FROM diet_catalog "
            "WHERE tenant_id = %s AND category = %s "
            "ORDER BY display_name",
            (tenant_id, category),
        )
    else:
        cur.execute(
            f"SELECT {_LIST_COLUMNS} FROM diet_catalog "
            "WHERE tenant_id = %s "
            "ORDER BY category, display_name",
            (tenant_id,),
        )

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return jsonify([_serialize_row(r) for r in rows])


@bp.route('/diet-catalog/<code>', methods=['GET'])
@require_auth
def get_diet_catalog_entry(code):
    """Return a single diet_catalog entry by code."""
    tenant_id = g.user.get('tenant_id', 1)

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute(
        f"SELECT {_LIST_COLUMNS}, created_at FROM diet_catalog "
        "WHERE tenant_id = %s AND code = %s",
        (tenant_id, code),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return jsonify({'error': 'diet code not found', 'code': code}), 404

    return jsonify(_serialize_row(row))


@bp.route('/diet-catalog/pull', methods=['GET'])
@require_auth
def pull_diet_catalog():
    """RxDB pull endpoint (read-only).

    Returns catalog rows whose updated_at is greater than the checkpoint,
    ordered by (updated_at, code) so the cursor is monotonic.

    Query params:
        checkpoint  — JSON {"updated_at": "...", "code": "..."} (optional)
        batchSize   — max rows per response (default 100, max 1000)

    Response:
        {
          "documents": [{...row..., "_deleted": false}, ...],
          "checkpoint": {"updated_at": "...", "code": "..."}
        }

    diet_catalog rows are never deleted (reference data is additive only),
    so _deleted is always false. The field is present so RxDB schema
    validation succeeds.
    """
    tenant_id = g.user.get('tenant_id', 1)

    checkpoint_raw = request.args.get('checkpoint')
    checkpoint = None
    if checkpoint_raw:
        try:
            checkpoint = json.loads(checkpoint_raw)
            if not isinstance(checkpoint, dict):
                checkpoint = None
        except (json.JSONDecodeError, TypeError):
            return jsonify({'error': 'invalid checkpoint JSON'}), 400

    try:
        batch_size = int(request.args.get('batchSize', PULL_DEFAULT_BATCH))
    except (TypeError, ValueError):
        return jsonify({'error': 'invalid batchSize'}), 400
    if batch_size < 1:
        return jsonify({'error': 'invalid batchSize'}), 400
    batch_size = min(batch_size, PULL_MAX_BATCH)

    conn = get_db_connection()
    cur = conn.cursor()

    base_select = (
        f"SELECT {_LIST_COLUMNS}, updated_at "
        "FROM diet_catalog WHERE tenant_id = %s"
    )

    if checkpoint and checkpoint.get('updated_at') and checkpoint.get('code'):
        cur.execute(
            base_select + " AND (updated_at, code) > (%s::timestamptz, %s) "
            "ORDER BY updated_at, code LIMIT %s",
            (tenant_id, checkpoint['updated_at'], checkpoint['code'], batch_size),
        )
    else:
        cur.execute(
            base_select + " ORDER BY updated_at, code LIMIT %s",
            (tenant_id, batch_size),
        )

    rows = cur.fetchall()
    cur.close()
    conn.close()

    documents = []
    for r in rows:
        out = dict(r)
        if out.get('updated_at'):
            out['updated_at'] = out['updated_at'].isoformat()
        out['_deleted'] = False
        documents.append(out)

    new_checkpoint = checkpoint
    if rows:
        last = rows[-1]
        new_checkpoint = {
            'updated_at': last['updated_at'].isoformat(),
            'code': last['code'],
        }

    return jsonify({'documents': documents, 'checkpoint': new_checkpoint})

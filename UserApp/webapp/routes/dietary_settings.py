"""
Dietary Settings routes.

Blueprint for user dietary preferences with history tracking.
Each PUT creates a new row and deactivates the previous one, preserving
a complete history of diet changes over time.

GET returns the active setting. GET ?history=true returns all settings.

diet_codes (added 2026-05-02 per Diets-Plan2.md Phase 1):
  TEXT[] of diet_catalog.code values; supports multi-diet (e.g.
  ['mediterranean','halal']). Validated against diet_catalog on POST/PUT;
  unknown codes return 400. The legacy diet_type column is still accepted
  and returned for backward compat; clients are expected to migrate to
  diet_codes over the next release.
"""
from flask import Blueprint, request, jsonify, g
from datetime import datetime
import json
import pytz
import uuid

from utils import require_auth, get_db_connection, get_user_id

bp = Blueprint('dietary_settings', __name__, url_prefix='/api/v1')

# Maximum batch size for RxDB pull. Higher values are clamped silently.
PULL_MAX_BATCH = 1000
PULL_DEFAULT_BATCH = 100

# Push payloads beyond this size are rejected outright. Keeps a
# misbehaving client from saturating the connection or the DB.
PUSH_MAX_CHANGE_ROWS = 500


def _validate_diet_codes(cur, codes):
    """Return (ok, unknown_codes). Empty/None codes pass.

    Uses a single round-trip against diet_catalog. RLS does not apply
    (diet_catalog has no RLS — reference data), but we still scope by
    tenant_id for forward-compat with per-tenant catalog overrides.
    """
    if not codes:
        return True, []
    cur.execute(
        "SELECT code FROM diet_catalog WHERE code = ANY(%s)",
        (list(codes),),
    )
    known = {r['code'] for r in cur.fetchall()}
    unknown = [c for c in codes if c not in known]
    return (not unknown), unknown


def _serialize_pull_row(row: dict) -> dict:
    """Shape a dietary_settings row for RxDB pull responses.

    Differs from _serialize_row by including user_id (the full document
    state RxDB needs to reason about ownership) and exposing _deleted
    derived from deleted_at.
    """
    out = dict(row)
    if out.get('id') is not None:
        out['id'] = str(out['id'])
    if out.get('user_id') is not None:
        out['user_id'] = str(out['user_id'])
    for field in ('effective_date', 'end_date', 'created_at', 'updated_at'):
        if out.get(field):
            out[field] = out[field].isoformat()
    for field in ('protein_target_g', 'carb_target_g', 'fat_target_g'):
        if out.get(field) is not None:
            out[field] = float(out[field])
    # _deleted comes from a SELECT alias; if it wasn't selected, default false.
    out.setdefault('_deleted', False)
    return out


def _parse_checkpoint(raw):
    """Parse a checkpoint JSON string from a query param. Empty → None."""
    if not raw:
        return None
    try:
        cp = json.loads(raw)
        return cp if isinstance(cp, dict) else None
    except (json.JSONDecodeError, TypeError):
        return False  # sentinel for "malformed"


def _parse_batch_size(raw):
    """Clamp batchSize to [1, PULL_MAX_BATCH]. Returns None on malformed input."""
    if raw is None:
        return PULL_DEFAULT_BATCH
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    if n < 1:
        return None
    return min(n, PULL_MAX_BATCH)


def _serialize_row(row: dict) -> dict:
    """Convert a dietary_settings row for JSON response."""
    row['id'] = str(row['id'])
    for field in ('effective_date', 'end_date', 'created_at', 'updated_at'):
        if row.get(field):
            row[field] = row[field].isoformat()
    # Convert numeric types for JSON
    for field in ('protein_target_g', 'carb_target_g', 'fat_target_g'):
        if row.get(field) is not None:
            row[field] = float(row[field])
    return row


@bp.route('/dietary-settings', methods=['GET'])
@require_auth
def get_dietary_settings():
    """Get dietary settings.

    Returns the active setting by default.
    With ?history=true, returns all settings ordered by effective_date DESC.
    """
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    show_history = request.args.get('history', '').lower() in ('true', '1', 'yes')

    conn = get_db_connection()
    cur = conn.cursor()

    if show_history:
        cur.execute("""
            SELECT id, diet_type, diet_codes, dietary_restrictions, calorie_target,
                   protein_target_g, carb_target_g, fat_target_g,
                   meal_count_per_day, notes, is_active, effective_date,
                   end_date, created_at, updated_at
            FROM dietary_settings
            WHERE tenant_id = %s AND user_id = %s AND deleted_at IS NULL
            ORDER BY effective_date DESC
        """, (tenant_id, user_id))

        rows = cur.fetchall()
        cur.close()
        conn.close()

        return jsonify([_serialize_row(row) for row in rows])
    else:
        cur.execute("""
            SELECT id, diet_type, diet_codes, dietary_restrictions, calorie_target,
                   protein_target_g, carb_target_g, fat_target_g,
                   meal_count_per_day, notes, is_active, effective_date,
                   end_date, created_at, updated_at
            FROM dietary_settings
            WHERE tenant_id = %s AND user_id = %s AND is_active = true AND deleted_at IS NULL
            ORDER BY effective_date DESC
            LIMIT 1
        """, (tenant_id, user_id))

        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            return jsonify(None)

        return jsonify(_serialize_row(row))


@bp.route('/dietary-settings', methods=['POST'])
@require_auth
def create_dietary_settings():
    """Create initial dietary settings.

    Use POST for the first setting. Use PUT to change settings
    (which preserves history).
    """
    data = request.json
    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    # Vegan-by-default: a user who arrives at first-setting creation without
    # an explicit preference (None, [] omitted key) is set to plant_based.
    # Per 2026-05-09 product directive — opt-out via PUT to any other code set.
    diet_codes = data.get('diet_codes') or ['plant_based']

    conn = get_db_connection()
    cur = conn.cursor()

    ok, unknown = _validate_diet_codes(cur, diet_codes)
    if not ok:
        cur.close()
        conn.close()
        return jsonify({
            'error': 'Unknown diet_codes',
            'unknown': unknown,
        }), 400

    # Check if active setting already exists (ignoring soft-deleted)
    cur.execute("""
        SELECT id FROM dietary_settings
        WHERE tenant_id = %s AND user_id = %s AND is_active = true AND deleted_at IS NULL
        LIMIT 1
    """, (tenant_id, user_id))

    if cur.fetchone():
        cur.close()
        conn.close()
        return jsonify({
            'error': 'Active dietary settings already exist. Use PUT to update.'
        }), 409

    setting_id = uuid.uuid4()
    now = datetime.now(pytz.utc)

    cur.execute("""
        INSERT INTO dietary_settings
        (tenant_id, id, user_id, diet_type, diet_codes, dietary_restrictions,
         calorie_target, protein_target_g, carb_target_g, fat_target_g,
         meal_count_per_day, notes, is_active, effective_date,
         created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true, %s, %s, %s)
        RETURNING id
    """, (
        tenant_id, setting_id, user_id,
        data.get('diet_type'),
        diet_codes,
        data.get('dietary_restrictions'),
        data.get('calorie_target'),
        data.get('protein_target_g'),
        data.get('carb_target_g'),
        data.get('fat_target_g'),
        data.get('meal_count_per_day', 3),
        data.get('notes'),
        data.get('effective_date', datetime.now(pytz.utc).date().isoformat()),
        now, now,
    ))

    result = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'id': str(result['id']), 'message': 'Dietary settings created'}), 201


@bp.route('/dietary-settings', methods=['PUT'])
@require_auth
def update_dietary_settings():
    """Update dietary settings by creating a new row.

    Deactivates the current active setting (sets is_active=false, end_date)
    and inserts a new active row. This preserves full history.
    """
    data = request.json
    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    now = datetime.now(pytz.utc)
    new_effective_date = data.get('effective_date', now.date().isoformat())
    # Vegan-by-default applies to PUT as well: a user who clears their
    # preference (None or empty) is re-defaulted to plant_based. Opt-out
    # is "choose a different diet," not "have no diet" — the latter is no
    # longer a representable state per 2026-05-09 product directive.
    diet_codes = data.get('diet_codes') or ['plant_based']

    conn = get_db_connection()
    cur = conn.cursor()

    ok, unknown = _validate_diet_codes(cur, diet_codes)
    if not ok:
        cur.close()
        conn.close()
        return jsonify({
            'error': 'Unknown diet_codes',
            'unknown': unknown,
        }), 400

    # Deactivate current active setting (skip soft-deleted)
    cur.execute("""
        UPDATE dietary_settings
        SET is_active = false, end_date = %s, updated_at = %s
        WHERE tenant_id = %s AND user_id = %s AND is_active = true AND deleted_at IS NULL
    """, (new_effective_date, now, tenant_id, user_id))

    # Insert new active setting
    setting_id = uuid.uuid4()

    cur.execute("""
        INSERT INTO dietary_settings
        (tenant_id, id, user_id, diet_type, diet_codes, dietary_restrictions,
         calorie_target, protein_target_g, carb_target_g, fat_target_g,
         meal_count_per_day, notes, is_active, effective_date,
         created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true, %s, %s, %s)
        RETURNING id
    """, (
        tenant_id, setting_id, user_id,
        data.get('diet_type'),
        diet_codes,
        data.get('dietary_restrictions'),
        data.get('calorie_target'),
        data.get('protein_target_g'),
        data.get('carb_target_g'),
        data.get('fat_target_g'),
        data.get('meal_count_per_day', 3),
        data.get('notes'),
        new_effective_date,
        now, now,
    ))

    result = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'id': str(result['id']), 'message': 'Dietary settings updated'})


@bp.route('/dietary-settings/<setting_id>', methods=['DELETE'])
@require_auth
def delete_dietary_settings(setting_id):
    """Soft-delete a dietary settings record.

    Sets deleted_at and bumps updated_at so RxDB clients sync the
    tombstone. The row stays in the table — hard delete would prevent
    other clients from learning about the deletion.

    If the active setting is deleted, the most recent inactive one is
    not automatically reactivated.
    """
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    now = datetime.now(pytz.utc)

    conn = get_db_connection()
    cur = conn.cursor()

    # Idempotent: a second DELETE on an already-deleted row is a no-op
    # (rowcount = 0 because of the deleted_at IS NULL guard) and returns 404.
    cur.execute("""
        UPDATE dietary_settings
        SET deleted_at = %s, updated_at = %s
        WHERE tenant_id = %s AND user_id = %s AND id = %s AND deleted_at IS NULL
    """, (now, now, tenant_id, user_id, uuid.UUID(setting_id),))

    rows_affected = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()

    if rows_affected == 0:
        return jsonify({'error': 'Dietary setting not found'}), 404

    return jsonify({'message': 'Dietary setting deleted'})


# ---------------------------------------------------------------------------
# RxDB replication endpoints
# ---------------------------------------------------------------------------
# Mobile (RxDB) clients do checkpoint-ordered pulls and conflict-aware pushes.
# Web/Claude/external clients keep using the legacy GET/POST/PUT/DELETE above.
# Both surfaces operate against the same table; they are not two views of
# a forked dataset.
#
# Checkpoint shape on the wire:
#   {"updated_at": "2026-05-02T12:00:00.123+00:00", "id": "uuid"}
# Server returns the next checkpoint after each pull; client echoes it back
# on the following pull. Empty / null on the first sync.
#
# Conflict detection is updated_at equality. If client's assumedMasterState
# updated_at != server's current updated_at, the row appears in errors and
# the mutation is skipped. Client merges and re-pushes.
#
# Auto-deactivation: when a push has is_active=true, any other row that's
# currently active (and not soft-deleted) is set is_active=false. Mirrors
# legacy PUT semantics so RxDB clients don't need to manage history rows.
# The deactivated row has its updated_at bumped, so the client picks it up
# on the next pull and reconciles its local state.


@bp.route('/dietary-settings/pull', methods=['GET'])
@require_auth
def pull_dietary_settings():
    """RxDB pull endpoint. Returns documents changed since the checkpoint.

    Query params:
        checkpoint  — JSON {"updated_at": "...", "id": "..."} (optional;
                      absent or null = full sync from beginning)
        batchSize   — max rows per response (default 100, max 1000)

    Response:
        {
          "documents": [{...row..., "_deleted": false_or_true}, ...],
          "checkpoint": {"updated_at": "...", "id": "..."}
        }

    Results are scoped to the authenticated user (explicit user_id predicate). Soft-deleted rows are
    included with _deleted=true so RxDB tombstones propagate.
    """
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    checkpoint = _parse_checkpoint(request.args.get('checkpoint'))
    if checkpoint is False:
        return jsonify({'error': 'invalid checkpoint JSON'}), 400

    batch_size = _parse_batch_size(request.args.get('batchSize'))
    if batch_size is None:
        return jsonify({'error': 'invalid batchSize'}), 400

    conn = get_db_connection()
    cur = conn.cursor()

    base_select = """
        SELECT id, user_id, diet_type, diet_codes, dietary_restrictions,
               calorie_target, protein_target_g, carb_target_g, fat_target_g,
               meal_count_per_day, notes, is_active, effective_date,
               end_date, created_at, updated_at,
               (deleted_at IS NOT NULL) AS _deleted
          FROM dietary_settings
         WHERE tenant_id = %s
           AND user_id = %s
    """

    if checkpoint and checkpoint.get('updated_at') and checkpoint.get('id'):
        cur.execute(base_select + """
               AND (updated_at, id) > (%s::timestamptz, %s::uuid)
             ORDER BY updated_at, id
             LIMIT %s
        """, (tenant_id, user_id, checkpoint['updated_at'], checkpoint['id'], batch_size))
    else:
        cur.execute(base_select + """
             ORDER BY updated_at, id
             LIMIT %s
        """, (tenant_id, user_id, batch_size))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    documents = [_serialize_pull_row(r) for r in rows]

    new_checkpoint = checkpoint
    if rows:
        last = rows[-1]
        new_checkpoint = {
            'updated_at': last['updated_at'].isoformat(),
            'id': str(last['id']),
        }

    return jsonify({'documents': documents, 'checkpoint': new_checkpoint})


@bp.route('/dietary-settings/push', methods=['POST'])
@require_auth
def push_dietary_settings():
    """RxDB push endpoint. Apply change rows from a client.

    Body:
        {"changeRows": [
            {"newDocumentState": {...}, "assumedMasterState": {...} | null},
            ...
        ]}

    Response:
        {"errors": [{"documentInDb": {...current server state...}}, ...]}

    Conflict semantics:
        * assumedMasterState=null and server has no row → INSERT.
        * assumedMasterState=null and server HAS a row → conflict (server wins).
        * assumedMasterState provided and updated_at matches server → apply.
        * assumedMasterState provided but updated_at mismatches → conflict.
        * assumedMasterState provided but server has no row → tombstone-conflict
          (server returns a synthetic _deleted row so the client knows the
          row was removed elsewhere).

    Validation:
        Pre-flight: every diet_codes entry across the whole batch must exist
        in diet_catalog. A single bad code aborts the batch with 400 — bad
        client input is not a sync conflict, it's a client bug.
    """
    data = request.json
    if not data or not isinstance(data.get('changeRows'), list):
        return jsonify({'error': 'changeRows array required'}), 400

    change_rows = data['changeRows']
    if len(change_rows) > PUSH_MAX_CHANGE_ROWS:
        return jsonify({
            'error': f'too many changeRows (max {PUSH_MAX_CHANGE_ROWS})'
        }), 400

    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    now = datetime.now(pytz.utc)

    conn = get_db_connection()
    cur = conn.cursor()

    # Pre-flight: validate every diet_codes value across all rows in one query.
    all_codes = set()
    for cr in change_rows:
        new_doc = cr.get('newDocumentState') or {}
        for code in (new_doc.get('diet_codes') or []):
            all_codes.add(code)

    if all_codes:
        ok, unknown = _validate_diet_codes(cur, list(all_codes))
        if not ok:
            cur.close()
            conn.close()
            return jsonify({
                'error': 'Unknown diet_codes',
                'unknown': unknown,
            }), 400

    errors = []

    for cr in change_rows:
        new_doc = cr.get('newDocumentState')
        assumed = cr.get('assumedMasterState')
        if not isinstance(new_doc, dict) or not new_doc.get('id'):
            # Malformed row — RxDB shouldn't send this. Skip silently rather
            # than aborting the whole batch.
            continue

        try:
            row_id = uuid.UUID(new_doc['id'])
        except (ValueError, TypeError):
            continue

        is_deleted = bool(new_doc.get('_deleted', False))
        is_active = bool(new_doc.get('is_active', False))
        effective_date = (
            new_doc.get('effective_date') or now.date().isoformat()
        )

        # Snapshot server state for conflict detection.
        cur.execute("""
            SELECT id, user_id, diet_type, diet_codes, dietary_restrictions,
                   calorie_target, protein_target_g, carb_target_g, fat_target_g,
                   meal_count_per_day, notes, is_active, effective_date,
                   end_date, created_at, updated_at,
                   (deleted_at IS NOT NULL) AS _deleted
              FROM dietary_settings
             WHERE tenant_id = %s AND user_id = %s AND id = %s
        """, (tenant_id, user_id, row_id))
        server_row = cur.fetchone()

        if server_row is None and assumed is not None:
            # Client thinks server has this row; server doesn't. Surface
            # a synthetic tombstone so the client can drop it locally.
            errors.append({'documentInDb': {
                'id': str(row_id),
                '_deleted': True,
            }})
            continue

        if server_row is not None:
            # Compare updated_at. assumedMasterState=None against an
            # existing server row is a conflict (client thinks INSERT,
            # but the server has a row already).
            if assumed is None:
                errors.append({
                    'documentInDb': _serialize_pull_row(dict(server_row))
                })
                continue
            assumed_updated = assumed.get('updated_at')
            try:
                # Postgres returns updated_at as timezone-aware datetime;
                # parse the client's ISO string the same way.
                assumed_dt = datetime.fromisoformat(
                    assumed_updated.replace('Z', '+00:00')
                ) if assumed_updated else None
            except (ValueError, AttributeError):
                assumed_dt = None
            if assumed_dt != server_row['updated_at']:
                errors.append({
                    'documentInDb': _serialize_pull_row(dict(server_row))
                })
                continue

        # Apply mutation.
        if is_deleted:
            cur.execute("""
                UPDATE dietary_settings
                   SET deleted_at = %s, updated_at = %s
                 WHERE tenant_id = %s AND user_id = %s AND id = %s
            """, (now, now, tenant_id, user_id, row_id))
            continue

        # Auto-deactivate any other currently-active row when this one
        # claims is_active=true. Bumps updated_at on the deactivated row
        # so the client picks it up on its next pull.
        if is_active:
            cur.execute("""
                UPDATE dietary_settings
                   SET is_active = false, end_date = %s, updated_at = %s
                 WHERE tenant_id = %s
                   AND user_id = %s
                   AND is_active = true
                   AND deleted_at IS NULL
                   AND id <> %s
            """, (effective_date, now, tenant_id, user_id, row_id))

        if server_row is None:
            cur.execute("""
                INSERT INTO dietary_settings
                (tenant_id, id, user_id, diet_type, diet_codes,
                 dietary_restrictions, calorie_target, protein_target_g,
                 carb_target_g, fat_target_g, meal_count_per_day, notes,
                 is_active, effective_date, end_date, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s)
            """, (
                tenant_id, row_id, user_id,
                new_doc.get('diet_type'),
                new_doc.get('diet_codes'),
                new_doc.get('dietary_restrictions'),
                new_doc.get('calorie_target'),
                new_doc.get('protein_target_g'),
                new_doc.get('carb_target_g'),
                new_doc.get('fat_target_g'),
                new_doc.get('meal_count_per_day', 3),
                new_doc.get('notes'),
                is_active,
                effective_date,
                new_doc.get('end_date'),
                now, now,
            ))
        else:
            # UPDATE in place. Preserves created_at; bumps updated_at;
            # clears deleted_at if the client is "un-deleting" (rare but
            # legal in RxDB protocol).
            cur.execute("""
                UPDATE dietary_settings
                   SET diet_type = %s,
                       diet_codes = %s,
                       dietary_restrictions = %s,
                       calorie_target = %s,
                       protein_target_g = %s,
                       carb_target_g = %s,
                       fat_target_g = %s,
                       meal_count_per_day = %s,
                       notes = %s,
                       is_active = %s,
                       effective_date = %s,
                       end_date = %s,
                       deleted_at = NULL,
                       updated_at = %s
                 WHERE tenant_id = %s AND user_id = %s AND id = %s
            """, (
                new_doc.get('diet_type'),
                new_doc.get('diet_codes'),
                new_doc.get('dietary_restrictions'),
                new_doc.get('calorie_target'),
                new_doc.get('protein_target_g'),
                new_doc.get('carb_target_g'),
                new_doc.get('fat_target_g'),
                new_doc.get('meal_count_per_day', 3),
                new_doc.get('notes'),
                is_active,
                effective_date,
                new_doc.get('end_date'),
                now,
                tenant_id, user_id, row_id,
            ))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'errors': errors})

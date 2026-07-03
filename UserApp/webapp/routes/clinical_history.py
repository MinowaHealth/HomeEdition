"""
Clinical History routes.

Blueprint for health conditions, allergies, blood work (labs), family history,
social history, and vaccinations.  All six tables follow the same CRUD pattern:
GET list, GET by id, POST, PUT, DELETE.
"""
from flask import Blueprint, request, jsonify, g, current_app
from datetime import datetime
import pytz
import uuid

from utils import (
    require_auth,
    get_db_connection,
    get_user_id,
    parse_pagination_params,
    paginated_response,
)

bp = Blueprint('clinical_history', __name__, url_prefix='/api/v1')


# ==================== HEALTH CONDITIONS ====================

@bp.route('/conditions', methods=['GET'])
@require_auth
def get_conditions():
    """Get a paginated list of health conditions for the authenticated user."""
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()
    limit, offset = parse_pagination_params()
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT count(*) OVER() AS _total,
               id, name, icd10_code, diagnosed_date, status, severity,
               treating_doctor, notes, custom_fields, created_at, updated_at
        FROM health_conditions
        WHERE tenant_id = %s AND user_id = %s
        ORDER BY diagnosed_date DESC NULLS LAST, name
        LIMIT %s OFFSET %s
    """, (tenant_id, user_id, limit, offset))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    total = rows[0]['_total'] if rows else 0
    for row in rows:
        row.pop('_total', None)
        row['id'] = str(row['id'])
        if row.get('diagnosed_date'):
            row['diagnosed_date'] = row['diagnosed_date'].isoformat()
        if row.get('created_at'):
            row['created_at'] = row['created_at'].isoformat()
        if row.get('updated_at'):
            row['updated_at'] = row['updated_at'].isoformat()

    return jsonify(paginated_response(rows, total, limit, offset, key='entries'))


@bp.route('/conditions/<condition_id>', methods=['GET'])
@require_auth
def get_condition(condition_id):
    """Get a single health condition by ID."""
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, name, icd10_code, diagnosed_date, status, severity,
               treating_doctor, notes, custom_fields, created_at, updated_at
        FROM health_conditions
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (tenant_id, user_id, uuid.UUID(condition_id),))

    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return jsonify({'error': 'Condition not found'}), 404

    row['id'] = str(row['id'])
    if row.get('diagnosed_date'):
        row['diagnosed_date'] = row['diagnosed_date'].isoformat()
    if row.get('created_at'):
        row['created_at'] = row['created_at'].isoformat()
    if row.get('updated_at'):
        row['updated_at'] = row['updated_at'].isoformat()

    return jsonify(row)


@bp.route('/conditions', methods=['POST'])
@require_auth
def create_condition():
    """Create a new health condition."""
    data = request.json
    if not data or 'name' not in data:
        return jsonify({'error': 'Missing required field: name'}), 400

    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    conn = get_db_connection()
    cur = conn.cursor()

    condition_id = uuid.uuid4()
    now = datetime.now(pytz.utc)

    cur.execute("""
        INSERT INTO health_conditions
        (tenant_id, id, user_id, name, icd10_code, diagnosed_date, status, severity,
         treating_doctor, notes, custom_fields, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        tenant_id, condition_id, user_id,
        data['name'], data.get('icd10_code'), data.get('diagnosed_date'),
        data.get('status', 'active'), data.get('severity'),
        data.get('treating_doctor'), data.get('notes'),
        data.get('custom_fields'), now, now
    ))

    result = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'id': str(result['id']), 'message': 'Condition created'}), 201


@bp.route('/conditions/<condition_id>', methods=['PUT'])
@require_auth
def update_condition(condition_id):
    """Update a health condition."""
    data = request.json
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()

    conn = get_db_connection()
    cur = conn.cursor()

    now = datetime.now(pytz.utc)

    cur.execute("""
        UPDATE health_conditions
        SET name = %s, icd10_code = %s, diagnosed_date = %s, status = %s,
            severity = %s, treating_doctor = %s, notes = %s, custom_fields = %s,
            updated_at = %s
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (
        data.get('name'), data.get('icd10_code'), data.get('diagnosed_date'),
        data.get('status'), data.get('severity'),
        data.get('treating_doctor'), data.get('notes'),
        data.get('custom_fields'), now, tenant_id, user_id, uuid.UUID(condition_id)
    ))

    rows_affected = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()

    if rows_affected == 0:
        return jsonify({'error': 'Condition not found'}), 404

    return jsonify({'message': 'Condition updated'})


@bp.route('/conditions/<condition_id>', methods=['DELETE'])
@require_auth
def delete_condition(condition_id):
    """Delete a health condition."""
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM health_conditions
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (tenant_id, user_id, uuid.UUID(condition_id),))

    rows_affected = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()

    if rows_affected == 0:
        return jsonify({'error': 'Condition not found'}), 404

    return jsonify({'message': 'Condition deleted'})


# ==================== HEALTH ALLERGIES ====================

@bp.route('/allergies', methods=['GET'])
@require_auth
def get_allergies():
    """Get a paginated list of allergies for the authenticated user."""
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()
    limit, offset = parse_pagination_params()
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT count(*) OVER() AS _total,
               id, allergen, allergy_type, reaction, severity, onset_date,
               status, notes, source, custom_fields, created_at, updated_at
        FROM health_allergies
        WHERE tenant_id = %s AND user_id = %s
        ORDER BY severity DESC NULLS LAST, allergen
        LIMIT %s OFFSET %s
    """, (tenant_id, user_id, limit, offset))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    total = rows[0]['_total'] if rows else 0
    for row in rows:
        row.pop('_total', None)
        row['id'] = str(row['id'])
        if row.get('onset_date'):
            row['onset_date'] = row['onset_date'].isoformat()
        if row.get('created_at'):
            row['created_at'] = row['created_at'].isoformat()
        if row.get('updated_at'):
            row['updated_at'] = row['updated_at'].isoformat()

    return jsonify(paginated_response(rows, total, limit, offset, key='entries'))


@bp.route('/allergies/<allergy_id>', methods=['GET'])
@require_auth
def get_allergy(allergy_id):
    """Get a single allergy by ID."""
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, allergen, allergy_type, reaction, severity, onset_date,
               status, notes, source, custom_fields, created_at, updated_at
        FROM health_allergies
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (tenant_id, user_id, uuid.UUID(allergy_id),))

    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return jsonify({'error': 'Allergy not found'}), 404

    row['id'] = str(row['id'])
    if row.get('onset_date'):
        row['onset_date'] = row['onset_date'].isoformat()
    if row.get('created_at'):
        row['created_at'] = row['created_at'].isoformat()
    if row.get('updated_at'):
        row['updated_at'] = row['updated_at'].isoformat()

    return jsonify(row)


@bp.route('/allergies', methods=['POST'])
@require_auth
def create_allergy():
    """Create a new allergy record."""
    data = request.json
    if not data or 'allergen' not in data:
        return jsonify({'error': 'Missing required field: allergen'}), 400

    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    conn = get_db_connection()
    cur = conn.cursor()

    allergy_id = uuid.uuid4()
    now = datetime.now(pytz.utc)

    cur.execute("""
        INSERT INTO health_allergies
        (tenant_id, id, user_id, allergen, allergy_type, reaction, severity,
         onset_date, status, notes, source, custom_fields, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        tenant_id, allergy_id, user_id,
        data['allergen'], data.get('allergy_type'), data.get('reaction'),
        data.get('severity'), data.get('onset_date'),
        data.get('status', 'active'), data.get('notes'),
        data.get('source', 'manual'), data.get('custom_fields'), now, now
    ))

    result = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'id': str(result['id']), 'message': 'Allergy created'}), 201


@bp.route('/allergies/<allergy_id>', methods=['PUT'])
@require_auth
def update_allergy(allergy_id):
    """Update an allergy record."""
    data = request.json
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()

    conn = get_db_connection()
    cur = conn.cursor()

    now = datetime.now(pytz.utc)

    cur.execute("""
        UPDATE health_allergies
        SET allergen = %s, allergy_type = %s, reaction = %s, severity = %s,
            onset_date = %s, status = %s, notes = %s, source = %s,
            custom_fields = %s, updated_at = %s
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (
        data.get('allergen'), data.get('allergy_type'), data.get('reaction'),
        data.get('severity'), data.get('onset_date'),
        data.get('status'), data.get('notes'),
        data.get('source'), data.get('custom_fields'),
        now, tenant_id, user_id, uuid.UUID(allergy_id)
    ))

    rows_affected = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()

    if rows_affected == 0:
        return jsonify({'error': 'Allergy not found'}), 404

    return jsonify({'message': 'Allergy updated'})


@bp.route('/allergies/<allergy_id>', methods=['DELETE'])
@require_auth
def delete_allergy(allergy_id):
    """Delete an allergy record."""
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM health_allergies
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (tenant_id, user_id, uuid.UUID(allergy_id),))

    rows_affected = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()

    if rows_affected == 0:
        return jsonify({'error': 'Allergy not found'}), 404

    return jsonify({'message': 'Allergy deleted'})


# ==================== BLOOD WORK (LAB RESULTS) ====================

@bp.route('/blood-work', methods=['GET'])
@require_auth
def get_blood_work():
    """Get a paginated list of blood work / lab results for the authenticated user."""
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()
    limit, offset = parse_pagination_params()
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT count(*) OVER() AS _total,
               id, test_date, test_name, value, unit, reference_range,
               is_abnormal, lab_name, loinc_code, panel_name, notes,
               created_at
        FROM health_blood_work
        WHERE tenant_id = %s AND user_id = %s
        ORDER BY test_date DESC NULLS LAST, panel_name, test_name
        LIMIT %s OFFSET %s
    """, (tenant_id, user_id, limit, offset))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    total = rows[0]['_total'] if rows else 0
    for row in rows:
        row.pop('_total', None)
        row['id'] = str(row['id'])
        if row.get('test_date'):
            row['test_date'] = row['test_date'].isoformat()
        if row.get('value') is not None:
            row['value'] = float(row['value'])
        if row.get('created_at'):
            row['created_at'] = row['created_at'].isoformat()

    return jsonify(paginated_response(rows, total, limit, offset, key='entries'))


@bp.route('/blood-work/<result_id>', methods=['GET'])
@require_auth
def get_blood_work_result(result_id):
    """Get a single blood work result by ID."""
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, test_date, test_name, value, unit, reference_range,
               is_abnormal, lab_name, loinc_code, panel_name, notes,
               created_at
        FROM health_blood_work
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (tenant_id, user_id, uuid.UUID(result_id),))

    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return jsonify({'error': 'Blood work result not found'}), 404

    row['id'] = str(row['id'])
    if row.get('test_date'):
        row['test_date'] = row['test_date'].isoformat()
    if row.get('value') is not None:
        row['value'] = float(row['value'])
    if row.get('created_at'):
        row['created_at'] = row['created_at'].isoformat()

    return jsonify(row)


@bp.route('/blood-work', methods=['POST'])
@require_auth
def create_blood_work():
    """Create a new blood work / lab result."""
    data = request.json
    if not data or 'test_name' not in data:
        return jsonify({'error': 'Missing required field: test_name'}), 400
    if 'test_date' not in data:
        return jsonify({'error': 'Missing required field: test_date'}), 400

    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    conn = get_db_connection()
    cur = conn.cursor()

    result_id = uuid.uuid4()
    now = datetime.now(pytz.utc)

    cur.execute("""
        INSERT INTO health_blood_work
        (tenant_id, id, user_id, test_date, test_name, value, unit,
         reference_range, is_abnormal, lab_name, loinc_code, panel_name,
         notes, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        tenant_id, result_id, user_id,
        data['test_date'], data['test_name'],
        float(data['value']) if data.get('value') is not None else None,
        data.get('unit'), data.get('reference_range'),
        data.get('is_abnormal', False), data.get('lab_name'),
        data.get('loinc_code'), data.get('panel_name'),
        data.get('notes'), now
    ))

    result = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'id': str(result['id']), 'message': 'Blood work result created'}), 201


@bp.route('/blood-work/<result_id>', methods=['PUT'])
@require_auth
def update_blood_work(result_id):
    """Update a blood work / lab result."""
    data = request.json
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE health_blood_work
        SET test_date = %s, test_name = %s, value = %s, unit = %s,
            reference_range = %s, is_abnormal = %s, lab_name = %s,
            loinc_code = %s, panel_name = %s, notes = %s
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (
        data.get('test_date'), data.get('test_name'),
        float(data['value']) if data.get('value') is not None else None,
        data.get('unit'), data.get('reference_range'),
        data.get('is_abnormal', False), data.get('lab_name'),
        data.get('loinc_code'), data.get('panel_name'),
        data.get('notes'), tenant_id, user_id, uuid.UUID(result_id)
    ))

    rows_affected = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()

    if rows_affected == 0:
        return jsonify({'error': 'Blood work result not found'}), 404

    return jsonify({'message': 'Blood work result updated'})


@bp.route('/blood-work/<result_id>', methods=['DELETE'])
@require_auth
def delete_blood_work(result_id):
    """Delete a blood work / lab result."""
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM health_blood_work
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (tenant_id, user_id, uuid.UUID(result_id),))

    rows_affected = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()

    if rows_affected == 0:
        return jsonify({'error': 'Blood work result not found'}), 404

    return jsonify({'message': 'Blood work result deleted'})


# ==================== FAMILY HISTORY ====================

@bp.route('/family-history', methods=['GET'])
@require_auth
def get_family_history():
    """Get a paginated list of family history records for the authenticated user."""
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()
    limit, offset = parse_pagination_params()
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT count(*) OVER() AS _total,
               id, relationship, relative_name, relative_age, vital_status,
               cause_of_death, condition_name, icd10_code, age_at_onset,
               notes, custom_fields, created_at, updated_at
        FROM health_family_history
        WHERE tenant_id = %s AND user_id = %s
        ORDER BY relationship, condition_name
        LIMIT %s OFFSET %s
    """, (tenant_id, user_id, limit, offset))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    total = rows[0]['_total'] if rows else 0
    for row in rows:
        row.pop('_total', None)
        row['id'] = str(row['id'])
        if row.get('created_at'):
            row['created_at'] = row['created_at'].isoformat()
        if row.get('updated_at'):
            row['updated_at'] = row['updated_at'].isoformat()

    return jsonify(paginated_response(rows, total, limit, offset, key='entries'))


@bp.route('/family-history/<entry_id>', methods=['GET'])
@require_auth
def get_family_history_entry(entry_id):
    """Get a single family history entry by ID."""
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, relationship, relative_name, relative_age, vital_status,
               cause_of_death, condition_name, icd10_code, age_at_onset,
               notes, custom_fields, created_at, updated_at
        FROM health_family_history
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (tenant_id, user_id, uuid.UUID(entry_id),))

    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return jsonify({'error': 'Family history entry not found'}), 404

    row['id'] = str(row['id'])
    if row.get('created_at'):
        row['created_at'] = row['created_at'].isoformat()
    if row.get('updated_at'):
        row['updated_at'] = row['updated_at'].isoformat()

    return jsonify(row)


@bp.route('/family-history', methods=['POST'])
@require_auth
def create_family_history():
    """Create a new family history entry."""
    data = request.json
    if not data or 'relationship' not in data:
        return jsonify({'error': 'Missing required field: relationship'}), 400

    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    conn = get_db_connection()
    cur = conn.cursor()

    entry_id = uuid.uuid4()
    now = datetime.now(pytz.utc)

    cur.execute("""
        INSERT INTO health_family_history
        (tenant_id, id, user_id, relationship, relative_name, relative_age,
         vital_status, cause_of_death, condition_name, icd10_code,
         age_at_onset, notes, custom_fields, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        tenant_id, entry_id, user_id,
        data['relationship'], data.get('relative_name'), data.get('relative_age'),
        data.get('vital_status'), data.get('cause_of_death'),
        data.get('condition_name'), data.get('icd10_code'),
        data.get('age_at_onset'), data.get('notes'),
        data.get('custom_fields'), now, now
    ))

    result = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'id': str(result['id']), 'message': 'Family history entry created'}), 201


@bp.route('/family-history/<entry_id>', methods=['PUT'])
@require_auth
def update_family_history(entry_id):
    """Update a family history entry."""
    data = request.json
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()

    conn = get_db_connection()
    cur = conn.cursor()

    now = datetime.now(pytz.utc)

    cur.execute("""
        UPDATE health_family_history
        SET relationship = %s, relative_name = %s, relative_age = %s,
            vital_status = %s, cause_of_death = %s, condition_name = %s,
            icd10_code = %s, age_at_onset = %s, notes = %s,
            custom_fields = %s, updated_at = %s
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (
        data.get('relationship'), data.get('relative_name'), data.get('relative_age'),
        data.get('vital_status'), data.get('cause_of_death'),
        data.get('condition_name'), data.get('icd10_code'),
        data.get('age_at_onset'), data.get('notes'),
        data.get('custom_fields'), now, tenant_id, user_id, uuid.UUID(entry_id)
    ))

    rows_affected = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()

    if rows_affected == 0:
        return jsonify({'error': 'Family history entry not found'}), 404

    return jsonify({'message': 'Family history entry updated'})


@bp.route('/family-history/<entry_id>', methods=['DELETE'])
@require_auth
def delete_family_history(entry_id):
    """Delete a family history entry."""
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM health_family_history
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (tenant_id, user_id, uuid.UUID(entry_id),))

    rows_affected = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()

    if rows_affected == 0:
        return jsonify({'error': 'Family history entry not found'}), 404

    return jsonify({'message': 'Family history entry deleted'})


# ==================== SOCIAL HISTORY ====================

@bp.route('/social-history', methods=['GET'])
@require_auth
def get_social_history():
    """Get a paginated list of social history records for the authenticated user."""
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()
    limit, offset = parse_pagination_params()
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT count(*) OVER() AS _total,
               id, category, status, detail, quantity, start_date, end_date,
               notes, custom_fields, created_at, updated_at
        FROM health_social_history
        WHERE tenant_id = %s AND user_id = %s
        ORDER BY category, status
        LIMIT %s OFFSET %s
    """, (tenant_id, user_id, limit, offset))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    total = rows[0]['_total'] if rows else 0
    for row in rows:
        row.pop('_total', None)
        row['id'] = str(row['id'])
        if row.get('start_date'):
            row['start_date'] = row['start_date'].isoformat()
        if row.get('end_date'):
            row['end_date'] = row['end_date'].isoformat()
        if row.get('created_at'):
            row['created_at'] = row['created_at'].isoformat()
        if row.get('updated_at'):
            row['updated_at'] = row['updated_at'].isoformat()

    return jsonify(paginated_response(rows, total, limit, offset, key='entries'))


@bp.route('/social-history/<entry_id>', methods=['GET'])
@require_auth
def get_social_history_entry(entry_id):
    """Get a single social history entry by ID."""
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, category, status, detail, quantity, start_date, end_date,
               notes, custom_fields, created_at, updated_at
        FROM health_social_history
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (tenant_id, user_id, uuid.UUID(entry_id),))

    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return jsonify({'error': 'Social history entry not found'}), 404

    row['id'] = str(row['id'])
    if row.get('start_date'):
        row['start_date'] = row['start_date'].isoformat()
    if row.get('end_date'):
        row['end_date'] = row['end_date'].isoformat()
    if row.get('created_at'):
        row['created_at'] = row['created_at'].isoformat()
    if row.get('updated_at'):
        row['updated_at'] = row['updated_at'].isoformat()

    return jsonify(row)


@bp.route('/social-history', methods=['POST'])
@require_auth
def create_social_history():
    """Create a new social history entry."""
    data = request.json
    if not data or 'category' not in data:
        return jsonify({'error': 'Missing required field: category'}), 400

    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    conn = get_db_connection()
    cur = conn.cursor()

    entry_id = uuid.uuid4()
    now = datetime.now(pytz.utc)

    cur.execute("""
        INSERT INTO health_social_history
        (tenant_id, id, user_id, category, status, detail, quantity,
         start_date, end_date, notes, custom_fields, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        tenant_id, entry_id, user_id,
        data['category'], data.get('status'), data.get('detail'),
        data.get('quantity'), data.get('start_date'), data.get('end_date'),
        data.get('notes'), data.get('custom_fields'), now, now
    ))

    result = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'id': str(result['id']), 'message': 'Social history entry created'}), 201


@bp.route('/social-history/<entry_id>', methods=['PUT'])
@require_auth
def update_social_history(entry_id):
    """Update a social history entry."""
    data = request.json
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()

    conn = get_db_connection()
    cur = conn.cursor()

    now = datetime.now(pytz.utc)

    cur.execute("""
        UPDATE health_social_history
        SET category = %s, status = %s, detail = %s, quantity = %s,
            start_date = %s, end_date = %s, notes = %s,
            custom_fields = %s, updated_at = %s
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (
        data.get('category'), data.get('status'), data.get('detail'),
        data.get('quantity'), data.get('start_date'), data.get('end_date'),
        data.get('notes'), data.get('custom_fields'),
        now, tenant_id, user_id, uuid.UUID(entry_id)
    ))

    rows_affected = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()

    if rows_affected == 0:
        return jsonify({'error': 'Social history entry not found'}), 404

    return jsonify({'message': 'Social history entry updated'})


@bp.route('/social-history/<entry_id>', methods=['DELETE'])
@require_auth
def delete_social_history(entry_id):
    """Delete a social history entry."""
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM health_social_history
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (tenant_id, user_id, uuid.UUID(entry_id),))

    rows_affected = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()

    if rows_affected == 0:
        return jsonify({'error': 'Social history entry not found'}), 404

    return jsonify({'message': 'Social history entry deleted'})


# ==================== VACCINATIONS ====================

@bp.route('/vaccinations', methods=['GET'])
@require_auth
def get_vaccinations():
    """Get a paginated list of vaccination records for the authenticated user."""
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()
    limit, offset = parse_pagination_params()
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT count(*) OVER() AS _total,
               id, vaccine_name, administered_date, lot_number, site,
               administered_by, location, next_dose_due, reaction_notes,
               created_at
        FROM health_vaccinations
        WHERE tenant_id = %s AND user_id = %s
        ORDER BY administered_date DESC NULLS LAST, vaccine_name
        LIMIT %s OFFSET %s
    """, (tenant_id, user_id, limit, offset))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    total = rows[0]['_total'] if rows else 0
    for row in rows:
        row.pop('_total', None)
        row['id'] = str(row['id'])
        if row.get('administered_date'):
            row['administered_date'] = row['administered_date'].isoformat()
        if row.get('next_dose_due'):
            row['next_dose_due'] = row['next_dose_due'].isoformat()
        if row.get('created_at'):
            row['created_at'] = row['created_at'].isoformat()

    return jsonify(paginated_response(rows, total, limit, offset, key='entries'))


@bp.route('/vaccinations/<vaccination_id>', methods=['GET'])
@require_auth
def get_vaccination(vaccination_id):
    """Get a single vaccination record by ID."""
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, vaccine_name, administered_date, lot_number, site,
               administered_by, location, next_dose_due, reaction_notes,
               created_at
        FROM health_vaccinations
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (tenant_id, user_id, uuid.UUID(vaccination_id),))

    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return jsonify({'error': 'Vaccination record not found'}), 404

    row['id'] = str(row['id'])
    if row.get('administered_date'):
        row['administered_date'] = row['administered_date'].isoformat()
    if row.get('next_dose_due'):
        row['next_dose_due'] = row['next_dose_due'].isoformat()
    if row.get('created_at'):
        row['created_at'] = row['created_at'].isoformat()

    return jsonify(row)


@bp.route('/vaccinations', methods=['POST'])
@require_auth
def create_vaccination():
    """Create a new vaccination record."""
    data = request.json
    if not data or 'vaccine_name' not in data:
        return jsonify({'error': 'Missing required field: vaccine_name'}), 400

    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    conn = get_db_connection()
    cur = conn.cursor()

    vaccination_id = uuid.uuid4()
    now = datetime.now(pytz.utc)

    cur.execute("""
        INSERT INTO health_vaccinations
        (tenant_id, id, user_id, vaccine_name, administered_date, lot_number,
         site, administered_by, location, next_dose_due, reaction_notes,
         created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        tenant_id, vaccination_id, user_id,
        data['vaccine_name'], data.get('administered_date'),
        data.get('lot_number'), data.get('site'),
        data.get('administered_by'), data.get('location'),
        data.get('next_dose_due'), data.get('reaction_notes'), now
    ))

    result = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'id': str(result['id']), 'message': 'Vaccination record created'}), 201


@bp.route('/vaccinations/<vaccination_id>', methods=['PUT'])
@require_auth
def update_vaccination(vaccination_id):
    """Update a vaccination record."""
    data = request.json
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE health_vaccinations
        SET vaccine_name = %s, administered_date = %s, lot_number = %s,
            site = %s, administered_by = %s, location = %s,
            next_dose_due = %s, reaction_notes = %s
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (
        data.get('vaccine_name'), data.get('administered_date'),
        data.get('lot_number'), data.get('site'),
        data.get('administered_by'), data.get('location'),
        data.get('next_dose_due'), data.get('reaction_notes'),
        tenant_id, user_id, uuid.UUID(vaccination_id)
    ))

    rows_affected = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()

    if rows_affected == 0:
        return jsonify({'error': 'Vaccination record not found'}), 404

    return jsonify({'message': 'Vaccination record updated'})


@bp.route('/vaccinations/<vaccination_id>', methods=['DELETE'])
@require_auth
def delete_vaccination(vaccination_id):
    """Delete a vaccination record."""
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM health_vaccinations
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (tenant_id, user_id, uuid.UUID(vaccination_id),))

    rows_affected = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()

    if rows_affected == 0:
        return jsonify({'error': 'Vaccination record not found'}), 404

    return jsonify({'message': 'Vaccination record deleted'})


# ==================== SURGICAL HISTORY ====================

@bp.route('/surgical-history', methods=['GET'])
@require_auth
def get_surgical_history():
    """Get a paginated list of surgical history records for the authenticated user."""
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()
    limit, offset = parse_pagination_params()
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT count(*) OVER() AS _total,
               id, procedure_name, procedure_date, surgeon, facility,
               outcome, complications, transfusions, anesthesia_type,
               notes, custom_fields, created_at, updated_at
        FROM health_surgical_history
        WHERE tenant_id = %s AND user_id = %s
        ORDER BY procedure_date DESC NULLS LAST, procedure_name
        LIMIT %s OFFSET %s
    """, (tenant_id, user_id, limit, offset))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    total = rows[0]['_total'] if rows else 0
    for row in rows:
        row.pop('_total', None)
        row['id'] = str(row['id'])
        if row.get('procedure_date'):
            row['procedure_date'] = row['procedure_date'].isoformat()
        if row.get('created_at'):
            row['created_at'] = row['created_at'].isoformat()
        if row.get('updated_at'):
            row['updated_at'] = row['updated_at'].isoformat()

    return jsonify(paginated_response(rows, total, limit, offset, key='entries'))

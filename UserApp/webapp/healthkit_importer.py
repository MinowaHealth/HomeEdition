#!/usr/bin/env python3
"""
HealthKit Import Module

Core functionality for importing Apple Health export data into PostgreSQL.
Refactored from standalone script for direct integration with Flask app.

Usage:
    from healthkit_importer import import_healthkit_export
    
    counts = import_healthkit_export(
        user_id='uuid-here',
        export_path=Path('/path/to/apple_health_export')
    )
"""

import json
import defusedxml.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
import db_driver


def parse_timestamp(ts_str: str | None) -> Optional[datetime]:
    """Parse HealthKit sample format (2024-12-11 12:22:00 -0800) or
    FHIR ISO 8601 (2025-06-17T23:14:00Z) from clinical records."""
    if not ts_str:
        return None
    try:
        # ISO 8601 incl. 'Z' (FHIR effectiveDateTime / authoredOn / issued)
        return datetime.fromisoformat(ts_str)
    except ValueError:
        pass
    try:
        # Format: YYYY-MM-DD HH:MM:SS +/-HHMM
        return datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S %z')
    except ValueError:
        try:
            # Fallback without timezone
            return datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return None


def get_or_create_record_type(cur, type_identifier: str) -> int:
    """Get or create record type and return its ID."""
    cur.execute(
        "SELECT id FROM hkit_record_types WHERE type_identifier = %s",
        (type_identifier,)
    )
    row = cur.fetchone()
    if row:
        return row['id']
    
    # Parse display name from identifier
    import re
    display_name = type_identifier.replace('HKQuantityTypeIdentifier', '')
    display_name = display_name.replace('HKCategoryTypeIdentifier', '')
    display_name = re.sub(r'([A-Z])', r' \1', display_name).strip()
    
    category = 'quantity' if 'Quantity' in type_identifier else 'category'
    
    cur.execute(
        """INSERT INTO hkit_record_types (type_identifier, category, display_name)
           VALUES (%s, %s, %s) RETURNING id""",
        (type_identifier, category, display_name)
    )
    return cur.fetchone()['id']


def get_or_create_source(cur, tenant_id: int, user_id: str, source_name: str,
                         source_version: Optional[str] = None) -> int:
    """Get or create source and return its ID."""
    cur.execute(
        """SELECT id FROM hkit_sources
           WHERE tenant_id = %s AND user_id = %s AND source_name = %s
                 AND COALESCE(source_version, '') = COALESCE(%s, '')""",
        (tenant_id, user_id, source_name, source_version)
    )
    row = cur.fetchone()
    if row:
        return row['id']

    cur.execute(
        """INSERT INTO hkit_sources (tenant_id, user_id, source_name, source_version)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (tenant_id, user_id, source_name, source_version)
    )
    return cur.fetchone()['id']


def import_me_element(cur, tenant_id: int, user_id: str, me_elem):
    """Import user profile from Me element."""
    print("Importing user profile...")

    dob = me_elem.get('HKCharacteristicTypeIdentifierDateOfBirth')
    sex = me_elem.get('HKCharacteristicTypeIdentifierBiologicalSex')
    blood_type = me_elem.get('HKCharacteristicTypeIdentifierBloodType')
    skin_type = me_elem.get('HKCharacteristicTypeIdentifierFitzpatrickSkinType')

    cur.execute(
        """INSERT INTO hkit_user_profile
           (tenant_id, user_id, date_of_birth, biological_sex, blood_type,
            fitzpatrick_skin_type, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, now())
           ON CONFLICT (tenant_id, user_id) DO UPDATE SET
           date_of_birth = EXCLUDED.date_of_birth,
           biological_sex = EXCLUDED.biological_sex,
           blood_type = EXCLUDED.blood_type,
           fitzpatrick_skin_type = EXCLUDED.fitzpatrick_skin_type,
           updated_at = now()""",
        (tenant_id, user_id, dob, sex, blood_type, skin_type)
    )
    print("  User profile imported.")


def import_records(cur, tenant_id: int, user_id: str, records, batch_size: int = 1000):
    """Import health records (steps, distance, calories, etc.)"""
    print(f"Importing {len(records)} records...")

    rows = []
    for elem in records:
        record_type = elem.get('type')
        source_name = elem.get('sourceName', 'Unknown')
        source_version = elem.get('sourceVersion')

        type_id = get_or_create_record_type(cur, record_type)
        source_id = get_or_create_source(cur, tenant_id, user_id, source_name, source_version)

        value_str = elem.get('value')
        try:
            value = float(value_str) if value_str else None
        except ValueError:
            value = None

        rows.append((
            tenant_id,
            user_id,
            type_id,
            source_id,
            value,
            elem.get('unit'),
            parse_timestamp(elem.get('startDate')),
            parse_timestamp(elem.get('endDate')),
            None  # metadata
        ))

    if rows:
        # executemany_simple calls psycopg3's pipelined cur.executemany —
        # batching is handled internally, no page_size needed.
        db_driver.executemany_simple(cur, """
            INSERT INTO hkit_records
            (tenant_id, user_id, record_type_id, source_id, value, unit, start_date, end_date, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, user_id, record_type_id, source_id, start_date, end_date)
            DO NOTHING
        """, rows)
        print(f"  Inserted {len(rows)} records.")


def import_activity_summaries(cur, tenant_id: int, user_id: str, summaries):
    """Import activity ring summaries."""
    print(f"Importing {len(summaries)} activity summaries...")

    rows = []
    for elem in summaries:
        date_str = elem.get('dateComponents')

        rows.append((
            tenant_id,
            user_id,
            date_str,
            float(elem.get('activeEnergyBurned', 0) or 0),
            float(elem.get('activeEnergyBurnedGoal', 0) or 0),
            int(float(elem.get('appleExerciseTime', 0) or 0)),
            int(float(elem.get('appleExerciseTimeGoal', 0) or 0)),
            int(float(elem.get('appleStandHours', 0) or 0)),
            int(float(elem.get('appleStandHoursGoal', 0) or 0)),
        ))

    if rows:
        db_driver.executemany_simple(cur, """
            INSERT INTO hkit_activity_summaries
            (tenant_id, user_id, date, active_energy_burned, active_energy_burned_goal,
             exercise_time, exercise_time_goal, stand_hours, stand_hours_goal)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, user_id, date) DO UPDATE SET
            active_energy_burned = EXCLUDED.active_energy_burned,
            active_energy_burned_goal = EXCLUDED.active_energy_burned_goal,
            exercise_time = EXCLUDED.exercise_time,
            exercise_time_goal = EXCLUDED.exercise_time_goal,
            stand_hours = EXCLUDED.stand_hours,
            stand_hours_goal = EXCLUDED.stand_hours_goal,
            created_at = now()
        """, rows)
        print(f"  Inserted {len(rows)} activity summaries.")


def import_workouts(cur, tenant_id: int, user_id: str, workouts):
    """Import workout sessions."""
    print(f"Importing {len(workouts)} workouts...")

    rows = []
    for elem in workouts:
        source_name = elem.get('sourceName', 'Unknown')
        source_version = elem.get('sourceVersion')
        source_id = get_or_create_source(cur, tenant_id, user_id, source_name, source_version)

        # Convert duration to seconds (HealthKit exports in minutes by default)
        duration_val = float(elem.get('duration', 0) or 0)
        duration_unit = elem.get('durationUnit', '')
        if 'min' in duration_unit.lower():
            duration_seconds = duration_val * 60
        else:
            duration_seconds = duration_val

        rows.append((
            tenant_id,
            user_id,
            elem.get('workoutActivityType'),
            source_id,
            parse_timestamp(elem.get('startDate')),
            parse_timestamp(elem.get('endDate')),
            duration_seconds,
            float(elem.get('totalDistance', 0) or 0) if elem.get('totalDistance') else None,
            float(elem.get('totalEnergyBurned', 0) or 0) if elem.get('totalEnergyBurned') else None,
            None  # metadata
        ))

    if rows:
        db_driver.executemany_simple(cur, """
            INSERT INTO hkit_workouts
            (tenant_id, user_id, workout_type, source_id, start_date, end_date,
             duration_seconds, total_distance, total_energy_burned, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, rows)
        print(f"  Inserted {len(rows)} workouts.")


def import_clinical_records(cur, tenant_id: int, user_id: str, clinical_elems, export_path: Path):
    """Import FHIR clinical records from JSON files."""
    print(f"Importing {len(clinical_elems)} clinical records...")

    clinical_dir = export_path / 'clinical-records'
    imported = 0

    for elem in clinical_elems:
        resource_type = elem.get('type')
        identifier = elem.get('identifier')
        display_name = elem.get('sourceName')
        source_url = elem.get('sourceURL')
        received_date = parse_timestamp(elem.get('receivedDate'))
        resource_path = elem.get('resourceFilePath')

        # Load JSON file
        json_file = clinical_dir / Path(resource_path).name if resource_path else None
        raw_fhir = None

        if json_file and json_file.exists():
            try:
                with open(json_file, 'r') as f:
                    raw_fhir = json.load(f)
            except Exception as e:
                print(f"  Warning: Could not read {json_file}: {e}")

        if not raw_fhir:
            continue

        # Insert clinical record. The ON CONFLICT clause is "DO UPDATE SET
        # <noop>" rather than "DO NOTHING" so RETURNING id works on the
        # conflict path — DO NOTHING would return no row, the fetchone() below
        # would get None, and the sub-table inserts that follow would create
        # orphaned rows with clinical_record_id=NULL. Setting fhir_resource_type
        # to its own EXCLUDED value is a true no-op (the column is NOT NULL and
        # the conflicting row necessarily has the same value, since they share
        # the same fhir_identifier from the same source EHR). The unique index
        # that backs this conflict target is partial — WHERE fhir_identifier
        # IS NOT NULL — so the conflict-target predicate must match exactly.
        try:
            cur.execute("""
                INSERT INTO hkit_clinical_records
                (tenant_id, user_id, fhir_resource_type, fhir_identifier,
                 display_name, fhir_source_url, received_date, raw_fhir)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, user_id, fhir_identifier)
                  WHERE fhir_identifier IS NOT NULL
                  DO UPDATE SET fhir_resource_type = EXCLUDED.fhir_resource_type
                RETURNING id
            """, (tenant_id, user_id, resource_type, identifier, display_name,
                  source_url, received_date, json.dumps(raw_fhir)))

            record_id = cur.fetchone()['id']

            # Extract to specialized tables based on resource type
            if resource_type == 'Observation':
                extract_lab_observation(cur, tenant_id, user_id, record_id, raw_fhir)
            elif resource_type == 'MedicationRequest':
                extract_medication(cur, tenant_id, user_id, record_id, raw_fhir)
            elif resource_type == 'Immunization':
                extract_immunization(cur, tenant_id, user_id, record_id, raw_fhir)
            elif resource_type == 'AllergyIntolerance':
                extract_allergy(cur, tenant_id, user_id, record_id, raw_fhir)

            imported += 1
        except Exception as e:
            print(f"  Error importing clinical record {identifier}: {e}")

    print(f"  Imported {imported} clinical records.")


def extract_lab_observation(cur, tenant_id: int, user_id: str, record_id: int, data: dict):
    """Extract lab observation details from FHIR Observation."""
    loinc_code = None
    display_name = None

    code_obj = data.get('code', {})
    display_name = code_obj.get('text')

    for coding in code_obj.get('coding', []):
        if coding.get('system') == 'http://loinc.org':
            loinc_code = coding.get('code')
            if not display_name:
                display_name = coding.get('display')
            break

    # Get value
    value_qty = data.get('valueQuantity', {})
    value = value_qty.get('value')
    unit = value_qty.get('unit')
    value_string = data.get('valueString')

    # Build reference range as text (v10 stores as single text field)
    reference_range = None
    ref_ranges = data.get('referenceRange', [])
    if ref_ranges:
        ref = ref_ranges[0]
        ref_low = ref.get('low', {}).get('value')
        ref_high = ref.get('high', {}).get('value')
        ref_unit = ref.get('low', {}).get('unit') or ref.get('high', {}).get('unit')
        if ref_low is not None and ref_high is not None:
            reference_range = f"{ref_low}-{ref_high}"
            if ref_unit:
                reference_range += f" {ref_unit}"
        elif ref_low is not None:
            reference_range = f">= {ref_low}"
        elif ref_high is not None:
            reference_range = f"<= {ref_high}"

    # Interpretation from FHIR interpretation field
    interpretation = None
    interp_list = data.get('interpretation', [])
    if interp_list:
        interp_codings = interp_list[0].get('coding', [])
        if interp_codings:
            interpretation = interp_codings[0].get('code')

    effective_date = parse_timestamp(data.get('effectiveDateTime'))

    if value is not None or loinc_code:
        # ON CONFLICT (tenant_id, user_id, clinical_record_id) DO NOTHING:
        # the partial unique index ux_hkit_lab_observations_parent enforces
        # one extracted lab observation per parent clinical record. The WHERE
        # predicate must mirror the index's partial WHERE exactly.
        cur.execute("""
            INSERT INTO hkit_lab_observations
            (tenant_id, user_id, clinical_record_id, loinc_code, display_name,
             value_quantity, value_unit, value_string, reference_range,
             interpretation, effective_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, user_id, clinical_record_id)
              WHERE clinical_record_id IS NOT NULL
              DO NOTHING
        """, (tenant_id, user_id, record_id, loinc_code, display_name,
              value, unit, value_string, reference_range, interpretation, effective_date))


def extract_medication(cur, tenant_id: int, user_id: str, record_id: int, data: dict):
    """Extract medication details from FHIR MedicationRequest."""
    med_ref = data.get('medicationReference', {})
    med_name = med_ref.get('display')

    if not med_name:
        med_codeable = data.get('medicationCodeableConcept', {})
        med_name = med_codeable.get('text')

    status = data.get('status')
    authored_date = parse_timestamp(data.get('authoredOn'))

    # Extract dosage if present
    dosage = None
    dosage_list = data.get('dosageInstruction', [])
    if dosage_list:
        dosage = dosage_list[0].get('text')

    if med_name:
        cur.execute("""
            INSERT INTO hkit_medications
            (tenant_id, user_id, clinical_record_id, medication_name, dosage, status, authored_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, user_id, clinical_record_id)
              WHERE clinical_record_id IS NOT NULL
              DO NOTHING
        """, (tenant_id, user_id, record_id, med_name, dosage, status, authored_date))


def extract_immunization(cur, tenant_id: int, user_id: str, record_id: int, data: dict):
    """Extract immunization details from FHIR Immunization."""
    vaccine = data.get('vaccineCode', {})
    vaccine_name = vaccine.get('text')
    vaccine_code = None

    for coding in vaccine.get('coding', []):
        vaccine_code = coding.get('code')
        if not vaccine_name:
            vaccine_name = coding.get('display')
        break

    administered_date = parse_timestamp(data.get('occurrenceDateTime'))
    lot_number = data.get('lotNumber')

    if vaccine_name:
        cur.execute("""
            INSERT INTO hkit_immunizations
            (tenant_id, user_id, clinical_record_id, vaccine_code, vaccine_name,
             administered_date, lot_number)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, user_id, clinical_record_id)
              WHERE clinical_record_id IS NOT NULL
              DO NOTHING
        """, (tenant_id, user_id, record_id, vaccine_code, vaccine_name,
              administered_date, lot_number))


def extract_allergy(cur, tenant_id: int, user_id: str, record_id: int, data: dict):
    """Extract allergy details from FHIR AllergyIntolerance."""
    code_obj = data.get('code', {})
    allergen = code_obj.get('text')

    if not allergen:
        for coding in code_obj.get('coding', []):
            allergen = coding.get('display')
            if allergen:
                break

    # Extract reaction text from FHIR reaction array
    reaction = None
    reactions = data.get('reaction', [])
    if reactions:
        manifestations = reactions[0].get('manifestation', [])
        if manifestations:
            reaction = manifestations[0].get('text')
            if not reaction:
                for coding in manifestations[0].get('coding', []):
                    reaction = coding.get('display')
                    break

    severity = data.get('criticality')

    if allergen:
        cur.execute("""
            INSERT INTO hkit_allergies
            (tenant_id, user_id, clinical_record_id, allergen, reaction, severity)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, user_id, clinical_record_id)
              WHERE clinical_record_id IS NOT NULL
              DO NOTHING
        """, (tenant_id, user_id, record_id, allergen, reaction, severity))


def import_healthkit_export(user_id: str, export_path: Path,
                           conn=None, job_id: Optional[str] = None,
                           tenant_id: int = 1) -> Dict[str, int]:
    """
    Import HealthKit export for a user.

    Args:
        user_id: UUID of user
        export_path: Path to extracted apple_health_export directory
        conn: Optional database connection (if None, uses db_manager)
        job_id: Optional job ID for status updates
        tenant_id: Tenant ID for multi-tenant isolation (default 1)

    Returns:
        dict with counts of imported records

    Raises:
        ValueError: If export.xml not found
        Exception: On import errors
    """
    xml_file = export_path / 'export.xml'
    if not xml_file.exists():
        raise ValueError(f"export.xml not found at {xml_file}")
    
    print(f"Parsing {xml_file}...")
    tree = ET.parse(xml_file)
    root = tree.getroot()
    assert root is not None, f"export.xml at {xml_file} parsed to an empty tree"

    # Collect elements
    me_elem = root.find('.//Me')
    records = root.findall('.//Record')
    activity_summaries = root.findall('.//ActivitySummary')
    workouts = root.findall('.//Workout')
    clinical_records = root.findall('.//ClinicalRecord')
    
    print(f"Found: {len(records)} records, {len(activity_summaries)} activity summaries, "
          f"{len(workouts)} workouts, {len(clinical_records)} clinical records")
    
    counts = {
        'records': 0,
        'activity_summaries': 0,
        'workouts': 0,
        'clinical_records': 0,
        'lab_results': 0
    }
    
    # Get connection if not provided
    close_conn = False
    if conn is None:
        from db_manager import get_direct_connection_for_user
        conn = get_direct_connection_for_user(user_id)
        close_conn = True
    
    try:
        cur = conn.cursor()

        # Import each section
        if me_elem is not None:
            import_me_element(cur, tenant_id, user_id, me_elem)

        if records:
            import_records(cur, tenant_id, user_id, records)
            counts['records'] = len(records)

        if activity_summaries:
            import_activity_summaries(cur, tenant_id, user_id, activity_summaries)
            counts['activity_summaries'] = len(activity_summaries)

        if workouts:
            import_workouts(cur, tenant_id, user_id, workouts)
            counts['workouts'] = len(workouts)

        if clinical_records:
            import_clinical_records(cur, tenant_id, user_id, clinical_records, export_path)
            counts['clinical_records'] = len(clinical_records)
            # Count lab results
            cur.execute("SELECT COUNT(*) FROM hkit_lab_observations WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            counts['lab_results'] = row['count'] if row else 0
        
        conn.commit()
        print("\nImport complete!")
        
        return counts
        
    except Exception as e:
        conn.rollback()
        raise
    finally:
        if close_conn:
            conn.close()

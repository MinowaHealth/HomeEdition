#!/usr/bin/env python3
"""
Garmin Background Worker - healthv10 version

Handles asynchronous Garmin data sync jobs.
Uses garminconnect library for authenticated API access.

Key design for v10:
  - Uses get_direct_connection_for_user() (app-level user_id scoping, no RLS)
  - All Garmin tables have composite PKs: (tenant_id, user_id, ...)
  - tenant_id DEFAULT 1 means we omit it in INSERTs; every query scopes by user_id
  - ON CONFLICT must reference the full composite PK
  - garmin_sync_jobs.status CHECK: pending, running, completed, failed, cancelled
  - Import counts stored in garmin_sync_jobs.progress (jsonb)
"""

import threading
import logging
import json
import base64
from pathlib import Path
import time as _time
from datetime import datetime, date, timedelta, timezone
import traceback


def setup_job_logger(job_id: str):
    """Set up a logger that writes to /app/logs/garmin_{job_id}.log"""
    log_dir = Path('/app/logs')
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / f'garmin_{job_id}.log'

    logger = logging.getLogger(f'garmin.{job_id}')
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.INFO)

    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    return logger


def process_garmin_sync(user_id: str, job_id: str, garth_session_b64: str,
                        sync_from: str, sync_to: str):
    """
    Process a Garmin sync job in background.

    Args:
        user_id: User ID (UUID) - scopes every query and is written into each row
        job_id: Unique job identifier (garmin_sync_jobs.id)
        garth_session_b64: Base64 encoded garth session data
        sync_from: Start date (YYYY-MM-DD)
        sync_to: End date (YYYY-MM-DD)
    """
    from garminconnect import Garmin
    from db_manager import get_direct_connection_for_user

    logger = setup_job_logger(job_id)
    conn = None

    try:
        logger.info(f"Starting Garmin sync for user_id: {user_id}")

        # Get an app-role connection for this user (queries scope by user_id; no RLS)
        conn = get_direct_connection_for_user(user_id)
        cur = conn.cursor()

        # Update job status to running (CHECK constraint: pending/running/completed/failed/cancelled)
        logger.info("Updating job status to 'running'")
        cur.execute("""
            UPDATE garmin_sync_jobs
            SET status = 'running', started_at = now()
            WHERE id = %s::uuid AND user_id = %s
        """, (job_id, user_id))
        conn.commit()

        # Restore garth session via garminconnect
        logger.info("Restoring Garmin session...")
        session_data = base64.b64decode(garth_session_b64).decode()

        garmin = Garmin()
        garmin.garth.loads(session_data)

        # Fetch profile to get display_name
        try:
            profile = garmin.garth.connectapi('/userprofile-service/socialProfile')
            garmin.display_name = profile.get('displayName') if isinstance(profile, dict) else None
            logger.info(f"Session restored, display_name: {garmin.display_name}")
        except Exception as e:
            logger.warning(f"Could not fetch profile: {e}")
            try:
                user_settings = garmin.garth.connectapi('/userprofile-service/userprofile/user-settings')
                garmin.display_name = user_settings.get('displayName') if isinstance(user_settings, dict) else None
            except Exception as e2:
                logger.warning(f"Could not get display_name: {e2}")

        # Parse dates
        from_date = datetime.strptime(sync_from, '%Y-%m-%d').date()
        to_date = datetime.strptime(sync_to, '%Y-%m-%d').date()
        total_days = (to_date - from_date).days + 1

        counts = {
            'daily_summaries': 0,
            'sleep': 0,
            'heart_rate': 0,
            'stress': 0
        }

        # Sync daily summaries
        logger.info(f"=== DAILY SUMMARIES: {from_date} to {to_date} ({total_days} days) ===")
        current_date = from_date
        day_num = 0
        while current_date <= to_date:
            day_num += 1
            date_str = current_date.isoformat()
            logger.info(f"[Summary {day_num}/{total_days}] Fetching {current_date}...")
            try:
                fetch_start = _time.perf_counter()
                summary = garmin.get_user_summary(date_str)
                fetch_ms = (_time.perf_counter() - fetch_start) * 1000

                if summary:
                    import_daily_summary(cur, user_id, current_date, summary)
                    counts['daily_summaries'] += 1
                    logger.info(f"[Summary {day_num}/{total_days}] {current_date} OK ({fetch_ms:.0f}ms)")
                else:
                    logger.info(f"[Summary {day_num}/{total_days}] {current_date} no data ({fetch_ms:.0f}ms)")
            except Exception as e:
                logger.warning(f"[Summary {day_num}/{total_days}] {current_date} FAILED: {e}")
            current_date += timedelta(days=1)

        conn.commit()
        logger.info(f"=== DAILY SUMMARIES COMPLETE: {counts['daily_summaries']} imported ===")

        # Sync sleep data
        logger.info(f"=== SLEEP DATA: {from_date} to {to_date} ({total_days} days) ===")
        try:
            counts['sleep'] = import_sleep_data(cur, user_id, from_date, to_date, garmin, logger)
            conn.commit()
            logger.info(f"=== SLEEP DATA COMPLETE: {counts['sleep']} imported ===")
        except Exception as e:
            logger.warning(f"Sleep sync FAILED: {e}")

        # Sync heart rate data
        logger.info(f"=== HEART RATE DATA: {from_date} to {to_date} ({total_days} days) ===")
        try:
            hr_count = import_heart_rate_data(cur, user_id, from_date, to_date, garmin, logger)
            counts['heart_rate'] = hr_count
            conn.commit()
            logger.info(f"=== HEART RATE COMPLETE: {hr_count} imported ===")
        except Exception as e:
            logger.warning(f"Heart rate sync FAILED: {e}")

        # Sync stress data
        logger.info(f"=== STRESS DATA: {from_date} to {to_date} ({total_days} days) ===")
        try:
            stress_count = import_stress_data(cur, user_id, from_date, to_date, garmin, logger)
            counts['stress'] = stress_count
            conn.commit()
            logger.info(f"=== STRESS COMPLETE: {stress_count} imported ===")
        except Exception as e:
            logger.warning(f"Stress sync FAILED: {e}")

        # Update job as completed — store counts in progress jsonb column
        logger.info("Updating job status to 'completed'")
        cur.execute("""
            UPDATE garmin_sync_jobs
            SET status = 'completed',
                completed_at = now(),
                progress = %s::jsonb
            WHERE id = %s::uuid AND user_id = %s
        """, (json.dumps(counts), job_id, user_id))

        # User-visible sync history (surfaced by /all-logs as type='sync')
        cur.execute("""
            INSERT INTO data_sync_log (tenant_id, user_id, source, job_id, status, detail, synced_at)
            VALUES (1, %s::uuid, 'garmin', %s::uuid, 'completed', %s::jsonb, now())
        """, (user_id, job_id, json.dumps(counts)))

        # Update last sync time in credentials
        cur.execute("""
            UPDATE garmin_credentials
            SET last_sync = now()
            WHERE user_id = %s
        """, (user_id,))

        conn.commit()
        logger.info("Sync completed successfully!")
        logger.info(f"  Daily summaries: {counts['daily_summaries']}")
        logger.info(f"  Sleep records: {counts['sleep']}")
        logger.info(f"  Heart rate readings: {counts['heart_rate']}")
        logger.info(f"  Stress readings: {counts['stress']}")

    except Exception as e:
        error_msg = str(e)
        error_trace = traceback.format_exc()

        logger.error(f"Sync failed: {error_msg}")
        logger.error(f"Traceback:\n{error_trace}")

        try:
            if conn is None:
                from db_manager import get_direct_connection_for_user
                conn = get_direct_connection_for_user(user_id)

            if conn:
                cur = conn.cursor()
                cur.execute("""
                    UPDATE garmin_sync_jobs
                    SET status = 'failed',
                        completed_at = now(),
                        error_message = %s
                    WHERE id = %s::uuid AND user_id = %s
                """, (error_msg, job_id, user_id))

                cur.execute("""
                    INSERT INTO data_sync_log (tenant_id, user_id, source, job_id, status, error_message, synced_at)
                    VALUES (1, %s::uuid, 'garmin', %s::uuid, 'failed', %s, now())
                """, (user_id, job_id, error_msg))

                conn.commit()
        except Exception as update_error:
            logger.error(f"Failed to update error status: {update_error}")

    finally:
        if conn:
            conn.close()


def import_daily_summary(cur, user_id: str, day: date, data: dict):
    """Import a daily summary record into garm_daily_summ.

    Maps garminconnect get_user_summary() response keys to v10 schema columns.
    PK: (tenant_id, user_id, calendar_date) — tenant_id DEFAULT 1 via schema.
    """
    cur.execute("""
        INSERT INTO garm_daily_summ (
            user_id, calendar_date,
            min_heart_rate, max_heart_rate, resting_heart_rate, avg_heart_rate,
            avg_stress_level, max_stress_level,
            daily_step_goal, total_steps,
            floors_climbed, floors_descended,
            total_distance_meters,
            calories_goal, total_kcals, bmr_kcals, active_kcals,
            body_battery_charged, body_battery_drained,
            body_battery_high, body_battery_low,
            sedentary_time_secs, active_time_secs,
            moderate_intensity_minutes, vigorous_intensity_minutes,
            intensity_minutes_goal,
            respiration_avg, respiration_high, respiration_low,
            spo2_avg, spo2_low
        )
        VALUES (
            %s, %s,
            %s, %s, %s, %s,
            %s, %s,
            %s, %s,
            %s, %s,
            %s,
            %s, %s, %s, %s,
            %s, %s,
            %s, %s,
            %s, %s,
            %s, %s,
            %s,
            %s, %s, %s,
            %s, %s
        )
        ON CONFLICT (tenant_id, user_id, calendar_date) DO UPDATE SET
            min_heart_rate = EXCLUDED.min_heart_rate,
            max_heart_rate = EXCLUDED.max_heart_rate,
            resting_heart_rate = EXCLUDED.resting_heart_rate,
            avg_heart_rate = EXCLUDED.avg_heart_rate,
            avg_stress_level = EXCLUDED.avg_stress_level,
            max_stress_level = EXCLUDED.max_stress_level,
            total_steps = EXCLUDED.total_steps,
            floors_climbed = EXCLUDED.floors_climbed,
            floors_descended = EXCLUDED.floors_descended,
            total_distance_meters = EXCLUDED.total_distance_meters,
            total_kcals = EXCLUDED.total_kcals,
            bmr_kcals = EXCLUDED.bmr_kcals,
            active_kcals = EXCLUDED.active_kcals,
            body_battery_charged = EXCLUDED.body_battery_charged,
            body_battery_drained = EXCLUDED.body_battery_drained,
            body_battery_high = EXCLUDED.body_battery_high,
            body_battery_low = EXCLUDED.body_battery_low,
            sedentary_time_secs = EXCLUDED.sedentary_time_secs,
            active_time_secs = EXCLUDED.active_time_secs,
            moderate_intensity_minutes = EXCLUDED.moderate_intensity_minutes,
            vigorous_intensity_minutes = EXCLUDED.vigorous_intensity_minutes,
            respiration_avg = EXCLUDED.respiration_avg,
            spo2_avg = EXCLUDED.spo2_avg
    """, (
        user_id,
        day,
        data.get('minHeartRate'),
        data.get('maxHeartRate'),
        data.get('restingHeartRate'),
        data.get('averageHeartRate'),
        data.get('averageStressLevel'),
        data.get('maxStressLevel'),
        data.get('dailyStepGoal'),
        data.get('totalSteps'),
        data.get('floorsAscended'),
        data.get('floorsDescended'),
        data.get('totalDistanceMeters'),
        data.get('netCalorieGoal'),
        data.get('totalKilocalories'),
        data.get('bmrKilocalories'),
        data.get('activeKilocalories'),
        data.get('bodyBatteryChargedValue'),
        data.get('bodyBatteryDrainedValue'),
        data.get('bodyBatteryHighestValue'),
        data.get('bodyBatteryLowestValue'),
        data.get('sedentarySeconds'),
        data.get('activeSeconds'),
        data.get('moderateIntensityMinutes'),
        data.get('vigorousIntensityMinutes'),
        data.get('intensityMinutesGoal'),
        data.get('averageRespirationValue'),
        data.get('highestRespirationValue'),
        data.get('lowestRespirationValue'),
        data.get('averageSpo2'),
        data.get('lowestSpo2'),
    ))


def import_sleep_data(cur, user_id: str, from_date: date, to_date: date, garmin, logger) -> int:
    """Import sleep records for date range.

    v10 schema: garm_sleep stores durations as integer seconds (not intervals).
    PK: (tenant_id, user_id, calendar_date).
    Sleep events: (tenant_id, user_id, start_time) with end_time and sleep_type.
    """
    count = 0
    current = from_date
    total_days = (to_date - from_date).days + 1
    day_num = 0

    SLEEP_LEVELS = {0: 'awake', 1: 'light', 2: 'deep', 3: 'rem'}

    while current <= to_date:
        day_num += 1
        date_str = current.isoformat()
        logger.info(f"[Sleep {day_num}/{total_days}] Fetching {current}...")
        try:
            fetch_start = _time.perf_counter()
            sleep = garmin.get_sleep_data(date_str)
            fetch_ms = (_time.perf_counter() - fetch_start) * 1000

            if sleep and sleep.get('dailySleepDTO'):
                dto = sleep['dailySleepDTO']
                cur.execute("""
                    INSERT INTO garm_sleep (
                        user_id, calendar_date, sleep_start, sleep_end,
                        deep_sleep_secs, light_sleep_secs, rem_sleep_secs, awake_secs,
                        avg_spo2, avg_respiration,
                        sleep_score, sleep_score_quality
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (tenant_id, user_id, calendar_date) DO UPDATE SET
                        sleep_start = EXCLUDED.sleep_start,
                        sleep_end = EXCLUDED.sleep_end,
                        deep_sleep_secs = EXCLUDED.deep_sleep_secs,
                        light_sleep_secs = EXCLUDED.light_sleep_secs,
                        rem_sleep_secs = EXCLUDED.rem_sleep_secs,
                        awake_secs = EXCLUDED.awake_secs,
                        avg_spo2 = EXCLUDED.avg_spo2,
                        avg_respiration = EXCLUDED.avg_respiration,
                        sleep_score = EXCLUDED.sleep_score,
                        sleep_score_quality = EXCLUDED.sleep_score_quality
                """, (
                    user_id,
                    current,
                    epoch_ms_to_timestamp(dto.get('sleepStartTimestampGMT')),
                    epoch_ms_to_timestamp(dto.get('sleepEndTimestampGMT')),
                    dto.get('deepSleepSeconds') or 0,
                    dto.get('lightSleepSeconds') or 0,
                    dto.get('remSleepSeconds') or 0,
                    dto.get('awakeSleepSeconds') or 0,
                    dto.get('averageSpO2Value'),
                    dto.get('averageRespirationValue'),
                    dto.get('sleepScores', {}).get('overall', {}).get('value'),
                    dto.get('sleepScores', {}).get('overall', {}).get('qualifierKey')
                ))
                count += 1

                # Import sleep events (stage transitions)
                sleep_levels_data = sleep.get('sleepLevels')
                events_imported = 0

                if isinstance(sleep_levels_data, list):
                    for level in sleep_levels_data:
                        if not isinstance(level, dict):
                            continue

                        start_str = level.get('startGMT')
                        end_str = level.get('endGMT')
                        event_code = level.get('activityLevel')

                        if event_code is not None:
                            sleep_type = SLEEP_LEVELS.get(int(event_code), f'unknown_{event_code}')
                        else:
                            sleep_type = 'unknown'

                        start_ts = _parse_garmin_gmt(start_str)
                        end_ts = _parse_garmin_gmt(end_str)

                        if start_ts and end_ts:
                            cur.execute("""
                                INSERT INTO garm_sleep_events (user_id, start_time, end_time, sleep_type)
                                VALUES (%s, %s, %s, %s)
                                ON CONFLICT (tenant_id, user_id, start_time) DO UPDATE SET
                                    end_time = EXCLUDED.end_time,
                                    sleep_type = EXCLUDED.sleep_type
                            """, (user_id, start_ts, end_ts, sleep_type))
                            events_imported += 1

                logger.info(f"[Sleep {day_num}/{total_days}] {current} COMPLETE - {events_imported} events")
            else:
                logger.info(f"[Sleep {day_num}/{total_days}] {current} no data ({fetch_ms:.0f}ms)")
        except Exception as e:
            logger.warning(f"[Sleep {day_num}/{total_days}] {current} FAILED: {e}")
            cur.connection.rollback()
        current += timedelta(days=1)

    return count


def _parse_garmin_gmt(value) -> datetime | None:
    """Parse a Garmin startGMT/endGMT string to UTC-aware datetime."""
    if not value or not isinstance(value, str):
        return None
    try:
        naive = datetime.fromisoformat(value.rstrip('Z').replace('.0', ''))
        return naive.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def epoch_ms_to_timestamp(epoch_ms):
    """Convert epoch milliseconds to UTC-aware datetime, or return None if invalid.

    Garmin API returns epoch-ms in UTC (field names like sleepStartTimestampGMT confirm this).
    """
    if epoch_ms is None:
        return None
    try:
        return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def import_heart_rate_data(cur, user_id: str, from_date: date, to_date: date, garmin, logger) -> int:
    """Import heart rate data for date range.

    PK: (tenant_id, user_id, timestamp) — tenant_id DEFAULT 1 via schema.
    """
    count = 0
    current = from_date
    total_days = (to_date - from_date).days + 1
    day_num = 0

    while current <= to_date:
        day_num += 1
        date_str = current.isoformat()
        logger.info(f"[HR {day_num}/{total_days}] Fetching {current}...")
        try:
            fetch_start = _time.perf_counter()
            hr_data = garmin.get_heart_rates(date_str)
            fetch_ms = (_time.perf_counter() - fetch_start) * 1000

            if hr_data and hr_data.get('heartRateValues'):
                hr_values = hr_data.get('heartRateValues', [])
                imported = 0
                for entry in hr_values:
                    if entry and len(entry) >= 2:
                        timestamp_ms, heart_rate = entry[0], entry[1]
                        if heart_rate is not None and heart_rate > 0:
                            ts = epoch_ms_to_timestamp(timestamp_ms)
                            if ts:
                                cur.execute("""
                                    INSERT INTO garm_hr (user_id, "timestamp", heart_rate)
                                    VALUES (%s, %s, %s)
                                    ON CONFLICT (tenant_id, user_id, "timestamp") DO UPDATE SET
                                        heart_rate = EXCLUDED.heart_rate
                                """, (user_id, ts, heart_rate))
                                imported += 1
                count += imported
                logger.info(f"[HR {day_num}/{total_days}] {current} OK ({fetch_ms:.0f}ms) - {imported} readings")
            else:
                logger.info(f"[HR {day_num}/{total_days}] {current} no data ({fetch_ms:.0f}ms)")
        except Exception as e:
            logger.warning(f"[HR {day_num}/{total_days}] {current} FAILED: {e}")
            cur.connection.rollback()
        current += timedelta(days=1)

    return count


def import_stress_data(cur, user_id: str, from_date: date, to_date: date, garmin, logger) -> int:
    """Import stress data for date range.

    PK: (tenant_id, user_id, timestamp) — tenant_id DEFAULT 1 via schema.
    """
    count = 0
    current = from_date
    total_days = (to_date - from_date).days + 1
    day_num = 0

    while current <= to_date:
        day_num += 1
        date_str = current.isoformat()
        logger.info(f"[Stress {day_num}/{total_days}] Fetching {current}...")
        try:
            fetch_start = _time.perf_counter()
            stress_data = garmin.get_stress_data(date_str)
            fetch_ms = (_time.perf_counter() - fetch_start) * 1000

            if stress_data and stress_data.get('stressValuesArray'):
                stress_values = stress_data.get('stressValuesArray', [])
                imported = 0
                for entry in stress_values:
                    if entry and len(entry) >= 2:
                        timestamp_ms, stress_level = entry[0], entry[1]
                        if stress_level is not None and stress_level >= 0:
                            ts = epoch_ms_to_timestamp(timestamp_ms)
                            if ts:
                                cur.execute("""
                                    INSERT INTO garm_stress (user_id, "timestamp", garm_stress)
                                    VALUES (%s, %s, %s)
                                    ON CONFLICT (tenant_id, user_id, "timestamp") DO UPDATE SET
                                        garm_stress = EXCLUDED.garm_stress
                                """, (user_id, ts, stress_level))
                                imported += 1
                count += imported
                logger.info(f"[Stress {day_num}/{total_days}] {current} OK ({fetch_ms:.0f}ms) - {imported} readings")
            else:
                logger.info(f"[Stress {day_num}/{total_days}] {current} no data ({fetch_ms:.0f}ms)")
        except Exception as e:
            logger.warning(f"[Stress {day_num}/{total_days}] {current} FAILED: {e}")
            cur.connection.rollback()
        current += timedelta(days=1)

    return count


def queue_garmin_sync(user_id: str, job_id: str, garth_session: str,
                      sync_from: str, sync_to: str):
    """Queue a Garmin sync job for background processing."""
    logger = setup_job_logger(job_id)
    logger.info("Queuing Garmin sync job")
    logger.info(f"  User ID: {user_id}")
    logger.info(f"  Date range: {sync_from} to {sync_to}")

    thread = threading.Thread(
        target=process_garmin_sync,
        args=(user_id, job_id, garth_session, sync_from, sync_to),
        daemon=True,
        name=f"garmin-sync-{job_id}"
    )
    thread.start()

    logger.info("Background thread started")

"""
Projection logic for reminders.

This module handles the projection of reminders from timeframes. When a stack or
standalone health_input is linked to a timeframe, a projected_reminder is created
that derives its schedule from the timeframe's time_of_day.

Projection is triggered when:
- A stack is created/updated with a timeframe_id
- A health_input is created/updated with a timeframe_id
- A timeframe's time_of_day, frequency, or recurrence settings change
"""
from datetime import datetime
from typing import Optional
from uuid import UUID
import logging

import pytz

logger = logging.getLogger(__name__)


def project_reminder_for_stack(
    conn,
    stack_id: UUID,
    timeframe_id: Optional[UUID],
    user_id: UUID,
    tenant_id: int = 1
) -> Optional[UUID]:
    """
    Project a reminder for a stack from its timeframe.

    Called when a stack is created or updated with a timeframe_id.
    Deletes any existing projected_reminder for this stack, then creates
    a new one if timeframe_id is set.

    Args:
        conn: Active database connection (app-level user_id scoping; no RLS)
        stack_id: The stack being projected
        timeframe_id: The timeframe to derive schedule from (None to delete only)
        user_id: The user who owns the stack
        tenant_id: Tenant ID (default 1)

    Returns:
        The UUID of the created projected_reminder, or None if deleted/skipped
    """
    cur = conn.cursor()

    try:
        # Delete any existing projected_reminder for this stack
        cur.execute("""
            DELETE FROM projected_reminders
            WHERE tenant_id = %s AND user_id = %s AND stack_id = %s
        """, (tenant_id, user_id, stack_id))
        deleted = cur.rowcount
        if deleted:
            logger.debug("Deleted %d existing projected_reminder(s) for stack %s", deleted, stack_id)

        if not timeframe_id:
            logger.debug("No timeframe_id for stack %s, skipping projection", stack_id)
            return None

        # Get timeframe details
        cur.execute("""
            SELECT time_of_day, frequency, custom_days, start_date
            FROM timeframes
            WHERE tenant_id = %s AND user_id = %s AND id = %s
        """, (tenant_id, user_id, timeframe_id))
        timeframe = cur.fetchone()

        if not timeframe:
            logger.warning("Timeframe %s not found for stack %s projection", timeframe_id, stack_id)
            return None

        if not timeframe['time_of_day']:
            logger.debug("Timeframe %s has no time_of_day, skipping projection for stack %s",
                         timeframe_id, stack_id)
            return None

        # Get user's timezone preference
        cur.execute("""
            SELECT timezone_reminder_mode
            FROM user_preferences
            WHERE tenant_id = %s AND user_id = %s
        """, (tenant_id, user_id))
        prefs = cur.fetchone()
        tz_mode = prefs['timezone_reminder_mode'] if prefs and prefs.get('timezone_reminder_mode') else 'local'

        # Insert new projected reminder
        now = datetime.now(pytz.utc)
        cur.execute("""
            INSERT INTO projected_reminders
                (tenant_id, user_id, stack_id, timeframe_id, scheduled_time,
                 frequency, custom_days, start_date, timezone_mode,
                 enabled, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            tenant_id, user_id, stack_id, timeframe_id,
            timeframe['time_of_day'],
            timeframe.get('frequency') or 'daily',
            timeframe.get('custom_days'),
            timeframe.get('start_date'),
            tz_mode,
            True,
            now, now
        ))
        result = cur.fetchone()
        reminder_id = result['id']
        logger.info("Created projected_reminder %s for stack %s from timeframe %s",
                    reminder_id, stack_id, timeframe_id)
        return reminder_id

    finally:
        cur.close()


def project_reminder_for_health_input(
    conn,
    health_input_id: UUID,
    timeframe_id: Optional[UUID],
    user_id: UUID,
    tenant_id: int = 1
) -> Optional[UUID]:
    """
    Project a reminder for a standalone health_input from its timeframe.

    Called when a health_input (not in a stack) is created or updated with a timeframe_id.
    Deletes any existing projected_reminder for this health_input, then creates
    a new one if timeframe_id is set.

    Args:
        conn: Active database connection (app-level user_id scoping; no RLS)
        health_input_id: The health_input being projected
        timeframe_id: The timeframe to derive schedule from (None to delete only)
        user_id: The user who owns the health_input
        tenant_id: Tenant ID (default 1)

    Returns:
        The UUID of the created projected_reminder, or None if deleted/skipped
    """
    cur = conn.cursor()

    try:
        # Delete any existing projected_reminder for this health_input
        cur.execute("""
            DELETE FROM projected_reminders
            WHERE tenant_id = %s AND user_id = %s AND health_input_id = %s
        """, (tenant_id, user_id, health_input_id))
        deleted = cur.rowcount
        if deleted:
            logger.debug("Deleted %d existing projected_reminder(s) for health_input %s",
                         deleted, health_input_id)

        if not timeframe_id:
            logger.debug("No timeframe_id for health_input %s, skipping projection", health_input_id)
            return None

        # Get timeframe details
        cur.execute("""
            SELECT time_of_day, frequency, custom_days, start_date
            FROM timeframes
            WHERE tenant_id = %s AND user_id = %s AND id = %s
        """, (tenant_id, user_id, timeframe_id))
        timeframe = cur.fetchone()

        if not timeframe:
            logger.warning("Timeframe %s not found for health_input %s projection",
                           timeframe_id, health_input_id)
            return None

        if not timeframe['time_of_day']:
            logger.debug("Timeframe %s has no time_of_day, skipping projection for health_input %s",
                         timeframe_id, health_input_id)
            return None

        # Get user's timezone preference
        cur.execute("""
            SELECT timezone_reminder_mode
            FROM user_preferences
            WHERE tenant_id = %s AND user_id = %s
        """, (tenant_id, user_id))
        prefs = cur.fetchone()
        tz_mode = prefs['timezone_reminder_mode'] if prefs and prefs.get('timezone_reminder_mode') else 'local'

        # Insert new projected reminder
        now = datetime.now(pytz.utc)
        cur.execute("""
            INSERT INTO projected_reminders
                (tenant_id, user_id, health_input_id, timeframe_id, scheduled_time,
                 frequency, custom_days, start_date, timezone_mode,
                 enabled, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            tenant_id, user_id, health_input_id, timeframe_id,
            timeframe['time_of_day'],
            timeframe.get('frequency') or 'daily',
            timeframe.get('custom_days'),
            timeframe.get('start_date'),
            tz_mode,
            True,
            now, now
        ))
        result = cur.fetchone()
        reminder_id = result['id']
        logger.info("Created projected_reminder %s for health_input %s from timeframe %s",
                    reminder_id, health_input_id, timeframe_id)
        return reminder_id

    finally:
        cur.close()


def update_projections_for_timeframe(conn, timeframe_id: UUID, user_id: UUID, tenant_id: int = 1) -> int:
    """
    Update all projected_reminders linked to a timeframe.

    Called when a timeframe's time_of_day, frequency, or recurrence settings change.
    Updates the scheduled_time and recurrence fields on all linked projected_reminders.

    Args:
        conn: Active database connection (app-level user_id scoping; no RLS)
        timeframe_id: The timeframe that changed
        tenant_id: Tenant ID (default 1)

    Returns:
        Number of projected_reminders updated
    """
    cur = conn.cursor()

    try:
        now = datetime.now(pytz.utc)
        cur.execute("""
            UPDATE projected_reminders pr
            SET scheduled_time = tf.time_of_day,
                frequency = COALESCE(tf.frequency, 'daily'),
                custom_days = tf.custom_days,
                start_date = tf.start_date,
                updated_at = %s
            FROM timeframes tf
            WHERE pr.timeframe_id = tf.id
              AND pr.tenant_id = %s
              AND pr.user_id = %s
              AND pr.timeframe_id = %s
        """, (now, tenant_id, user_id, timeframe_id))

        updated = cur.rowcount
        if updated:
            logger.info("Updated %d projected_reminder(s) for timeframe %s", updated, timeframe_id)
        return updated

    finally:
        cur.close()

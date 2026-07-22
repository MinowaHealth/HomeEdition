"""Recurrence expansion — server-side mirror of src/scheduling/recurrenceUtils.ts.

Pure functions on python `date` objects. The frequency vocabulary matches
the mobile RecurrenceRule type: 'daily' | 'weekly' | 'monthly' | 'annual'.

Mobile treats `frequency=='none'` and missing rule as "no recurrence" —
isRecurringOnDate returns False even on the anchor day. We mirror that
exactly: `is_recurring_on_date(None, anchor, anchor)` returns False.

This module is consumed by the shopping-list aggregation in
routes/shopping.py to walk recurring scheduled_meal rows and decide
which dates they contribute to over the requested window.
"""
from datetime import date as date_cls
from typing import Optional


VALID_FREQUENCIES = frozenset({'daily', 'weekly', 'monthly', 'annual'})


def is_recurring_on_date(
    frequency: Optional[str],
    anchor: Optional[date_cls],
    target: date_cls,
) -> bool:
    """Does a rule with the given (frequency, anchor) occur on `target`?

    Returns False for:
      * frequency is None / 'none' (no recurrence — caller handles
        one-off via direct date comparison, not via this function)
      * anchor is None (malformed rule)
      * target is before anchor (recurrence has not started yet)
      * unknown frequency string

    Returns True when:
      * daily — every day from anchor onwards
      * weekly — same weekday as anchor (date.weekday()), on or after anchor
      * monthly — same day-of-month as anchor; months without that day skip
        (e.g. anchor on the 31st skips months that have only 28-30 days)
      * annual — same month+day as anchor
    """
    if frequency is None or frequency == 'none':
        return False
    if anchor is None:
        return False
    if target < anchor:
        return False

    if frequency == 'daily':
        return True
    if frequency == 'weekly':
        return target.weekday() == anchor.weekday()
    if frequency == 'monthly':
        return target.day == anchor.day
    if frequency == 'annual':
        return target.month == anchor.month and target.day == anchor.day

    # Unknown frequency — treat as "no recurrence" rather than raising.
    # The schema CHECK constraint already prevents bad values from
    # landing in the DB; this is just defensive against future drift.
    return False

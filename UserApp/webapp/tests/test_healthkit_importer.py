# Date: 2026-07-03 01:45 PDT
"""Tests for healthkit_importer.parse_timestamp."""
from datetime import datetime, timezone

from healthkit_importer import parse_timestamp


def test_fhir_iso8601_z_suffix():
    # FHIR clinical records (effectiveDateTime/authoredOn) — regression:
    # these silently parsed to None, leaving all lab dates NULL.
    dt = parse_timestamp('2025-06-17T23:14:00Z')
    assert dt == datetime(2025, 6, 17, 23, 14, tzinfo=timezone.utc)


def test_healthkit_sample_format():
    dt = parse_timestamp('2024-12-11 12:22:00 -0800')
    assert dt is not None
    assert dt.utcoffset() is not None


def test_naive_fallback_and_none():
    assert parse_timestamp('2024-12-11 12:22:00') == datetime(2024, 12, 11, 12, 22)
    assert parse_timestamp(None) is None
    assert parse_timestamp('garbage') is None

"""
Integration tests for /api/v1/healthkit/sync runtime behavior.

These tests target production-like failure modes:
1) Missing optional dedupe index must not break sync inserts.
2) Blood pressure sync payloads must not fail tenant_id casting.
"""

from datetime import datetime, timezone

import pytest


def _iso_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class TestHealthkitSyncEndpoint:
    def test_sync_clinical_without_dedupe_index(self, api, db_conn):
        """
        /healthkit/sync should still return 200 when the optional health_metrics
        dedupe index is absent.
        """
        cur = db_conn.cursor()
        try:
            cur.execute("DROP INDEX IF EXISTS idx_health_metrics_sync_dedupe")
            db_conn.commit()

            payload = {
                "samples": [
                    {
                        "type": "immunization_record",
                        "start_time": _iso_now(),
                        "source": "pytest",
                        "metadata": {
                            "record_type": "immunization_record",
                            "record": {
                                "displayName": "Pytest Vaccine",
                                "resourceType": "Immunization",
                                "fhir": '{"resourceType":"Immunization","status":"completed"}',
                            },
                        },
                    }
                ]
            }
            resp = api.post("/healthkit/sync", json=payload)
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert "received" in data
            assert "inserted" in data
            assert "skipped" in data
            assert data["received"] == 1
        finally:
            # Restore optional dedupe index for subsequent test runs/environments.
            cur.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_health_metrics_sync_dedupe
                ON health_metrics (tenant_id, user_id, metric_type, recorded_at, value, unit, source)
                """
            )
            db_conn.commit()
            cur.close()

    def test_sync_blood_pressure_payload(self, api):
        """Blood pressure payload should not fail with smallint tenant_id cast errors."""
        payload = {
            "samples": [
                {
                    "type": "blood_pressure",
                    "start_time": _iso_now(),
                    "end_time": _iso_now(),
                    "systolic": 140,
                    "diastolic": 80,
                    "unit": "mmHg",
                    "source": "pytest",
                }
            ]
        }
        resp = api.post("/healthkit/sync", json=payload)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "inserted_bp" in data
        assert data["received"] == 1


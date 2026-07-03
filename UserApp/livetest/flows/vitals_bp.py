"""Live test: /api/v1/blood-pressure POST/GET/DELETE.

Note: the POST handler does NOT return the created row's id (it returns
only a success message). So this flow identifies the created row by
recovering it via GET /blood-pressure using a sentinel systolic/diastolic
pair that is extremely unlikely to collide with real readings. We then
delete by that id and verify the delta returns to baseline.

There is no name-keyed column to prefix with `livetest-`, so the cleanup
subcommand cannot target this table the same way it targets the Phase A
ones. The flow cleans up after itself via the DELETE step, and any row
that survives an aborted run is identifiable by its sentinel values.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from livetest.auth import login
from livetest.config import load_config
from livetest.pagination_assertions import assert_pagination_envelope
from livetest.pg import count_rows, open_rls_connection
from livetest.report import print_live, write_markdown
from livetest.runner import Flow, FlowResult

# Sentinel readings — intentionally outside any plausible real range so
# we can distinguish livetest rows from real ones during lookup. Systolic
# 222 / diastolic 111 would trigger an emergency alert if it were real;
# no user will ever actually log that pair manually.
SENTINEL_SYSTOLIC = 222
SENTINEL_DIASTOLIC = 111
SENTINEL_PULSE = 99


class VitalsBpFlow(Flow):
    name = "vitals_bp"

    def run(self) -> FlowResult:
        cur = self.conn.cursor()
        reading_id: str | None = None
        before = 0
        measured_at_iso = datetime.now(timezone.utc).isoformat()

        with self.step("count rows before"):
            before = count_rows(
                cur,
                "health_blood_pressure_readings",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )

        with self.step("POST /api/v1/blood-pressure"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/blood-pressure",
                json={
                    "systolic": SENTINEL_SYSTOLIC,
                    "diastolic": SENTINEL_DIASTOLIC,
                    "heart_rate": SENTINEL_PULSE,
                    "timestamp": measured_at_iso,
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"expected 201, got {resp.status_code}: {resp.text}"
            )

        with self.step("verify row exists in DB (delta check)"):
            after = count_rows(
                cur,
                "health_blood_pressure_readings",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )
            assert after == before + 1, (
                f"delta {after - before}, expected 1 "
                f"(before={before}, after={after})"
            )

        with self.step("GET /api/v1/blood-pressure locates sentinel reading"):
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/blood-pressure",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            readings = assert_pagination_envelope(resp.json(), "entries")
            matches = [
                r for r in readings
                if r.get("systolic") == SENTINEL_SYSTOLIC
                and r.get("diastolic") == SENTINEL_DIASTOLIC
            ]
            assert matches, (
                f"sentinel BP reading not found in GET response "
                f"({len(readings)} readings returned)"
            )
            # Newest first, take the freshest match.
            reading_id = matches[0]["id"]

        with self.step("DELETE /api/v1/blood-pressure/{id}"):
            assert reading_id is not None, "reading_id not set"
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/blood-pressure/{reading_id}",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )

        with self.step("verify delete returns rowcount to baseline"):
            final = count_rows(
                cur,
                "health_blood_pressure_readings",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )
            assert final == before, (
                f"post-delete count {final}, expected {before}"
            )

        return self.result()


def main() -> None:
    cfg = load_config(sys.argv[1:])
    session = login(cfg)
    resp = session.get(
        f"{cfg.base_url}/api/v1/session", timeout=cfg.timeout
    )
    resp.raise_for_status()
    user_id = resp.json()["user_id"]

    conn = open_rls_connection(cfg, user_id)
    try:
        flow = VitalsBpFlow(cfg, session, conn, user_id)
        result = flow.run()
    finally:
        conn.close()

    print_live(result)
    report_path = write_markdown([result], cfg)
    print(f"\nReport: {report_path}")
    sys.exit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()

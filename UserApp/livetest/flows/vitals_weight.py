"""Live test: /api/v1/weight POST/GET/DELETE.

Same shape as vitals_bp: POST returns no id, so we identify the row by a
sentinel weight value and delete via /api/v1/weight/{id}. Row lives in
health_metrics with metric_type='weight'.
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

# Intentionally out-of-range to avoid collision with real weight entries.
SENTINEL_WEIGHT = 333.3
SENTINEL_UNIT = "lbs"


class VitalsWeightFlow(Flow):
    name = "vitals_weight"

    def run(self) -> FlowResult:
        cur = self.conn.cursor()
        weight_id: str | None = None
        before = 0
        timestamp_iso = datetime.now(timezone.utc).isoformat()

        with self.step("count rows before"):
            before = count_rows(
                cur,
                "health_metrics",
                "tenant_id=%s AND user_id=%s AND metric_type='weight'",
                (self.cfg.tenant_id, self.user_id),
            )

        with self.step("POST /api/v1/weight"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/weight",
                json={
                    "weight": SENTINEL_WEIGHT,
                    "unit": SENTINEL_UNIT,
                    "timestamp": timestamp_iso,
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"expected 201, got {resp.status_code}: {resp.text}"
            )

        with self.step("verify row exists in DB (delta check)"):
            after = count_rows(
                cur,
                "health_metrics",
                "tenant_id=%s AND user_id=%s AND metric_type='weight'",
                (self.cfg.tenant_id, self.user_id),
            )
            assert after == before + 1, (
                f"delta {after - before}, expected 1 "
                f"(before={before}, after={after})"
            )

        with self.step("GET /api/v1/weight locates sentinel reading"):
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/weight",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            readings = assert_pagination_envelope(resp.json(), "entries")
            matches = [
                r for r in readings
                if abs(float(r.get("weight", 0)) - SENTINEL_WEIGHT) < 0.01
            ]
            assert matches, (
                f"sentinel weight reading not found in GET response "
                f"({len(readings)} readings returned)"
            )
            weight_id = matches[0]["id"]

        with self.step("DELETE /api/v1/weight/{id}"):
            assert weight_id is not None, "weight_id not set"
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/weight/{weight_id}",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )

        with self.step("verify delete returns rowcount to baseline"):
            final = count_rows(
                cur,
                "health_metrics",
                "tenant_id=%s AND user_id=%s AND metric_type='weight'",
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
        flow = VitalsWeightFlow(cfg, session, conn, user_id)
        result = flow.run()
    finally:
        conn.close()

    print_live(result)
    report_path = write_markdown([result], cfg)
    print(f"\nReport: {report_path}")
    sys.exit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()

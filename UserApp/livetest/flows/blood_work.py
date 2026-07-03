"""Live test: /api/v1/blood-work CRUD (health_blood_work table).

Named blood_work rather than 'labs' because the spec's placeholder name
(`labs`) doesn't match the actual endpoint (`/blood-work`) or table
(`health_blood_work`). Keep the filename aligned with the URL so the
flow name maps cleanly to the route.
"""
from __future__ import annotations

import sys
import uuid
from datetime import date

from livetest.auth import login
from livetest.config import load_config
from livetest.pagination_assertions import assert_pagination_envelope
from livetest.pg import count_rows, open_rls_connection
from livetest.report import print_live, write_markdown
from livetest.runner import Flow, FlowResult


class BloodWorkFlow(Flow):
    name = "blood_work"

    def run(self) -> FlowResult:
        cur = self.conn.cursor()
        result_id: str | None = None
        test_name = f"livetest-test-{uuid.uuid4().hex[:8]}"
        before = 0

        with self.step("count rows before"):
            before = count_rows(
                cur,
                "health_blood_work",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )

        with self.step("POST /api/v1/blood-work"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/blood-work",
                json={
                    "test_name": test_name,
                    "test_date": date.today().isoformat(),
                    "value": 5.4,
                    "unit": "mmol/L",
                    "reference_range": "4.0-6.0",
                    "is_abnormal": False,
                    "lab_name": "livetest-lab",
                    "panel_name": "livetest-panel",
                    "notes": "created by livetest harness",
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"expected 201, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert "id" in body, f"POST response missing 'id': {body}"
            result_id = body["id"]

        with self.step("verify row exists in DB (delta check)"):
            after = count_rows(
                cur,
                "health_blood_work",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )
            assert after == before + 1, (
                f"delta {after - before}, expected 1 "
                f"(before={before}, after={after})"
            )

        with self.step("GET /api/v1/blood-work includes created result"):
            assert result_id is not None, "result_id not set"
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/blood-work",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            items = assert_pagination_envelope(resp.json(), "entries")
            assert any(r.get("id") == result_id for r in items), (
                f"blood-work result {result_id} not in GET response "
                f"({len(items)} items returned)"
            )

        with self.step("GET /api/v1/blood-work/{id}"):
            assert result_id is not None, "result_id not set"
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/blood-work/{result_id}",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert body.get("test_name") == test_name, (
                f"test_name mismatch: got {body.get('test_name')!r}, "
                f"expected {test_name!r}"
            )

        with self.step("PUT /api/v1/blood-work/{id}"):
            assert result_id is not None, "result_id not set"
            resp = self.session.put(
                f"{self.cfg.base_url}/api/v1/blood-work/{result_id}",
                json={
                    "test_name": test_name,
                    "test_date": date.today().isoformat(),
                    "value": 5.7,
                    "unit": "mmol/L",
                    "is_abnormal": False,
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )

        with self.step("DELETE /api/v1/blood-work/{id}"):
            assert result_id is not None, "result_id not set"
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/blood-work/{result_id}",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
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
        flow = BloodWorkFlow(cfg, session, conn, user_id)
        result = flow.run()
    finally:
        conn.close()

    print_live(result)
    report_path = write_markdown([result], cfg)
    print(f"\nReport: {report_path}")
    sys.exit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()

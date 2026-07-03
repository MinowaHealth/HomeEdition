"""Live test: GET /api/v1/health-input-log ?input_type filter (PLAN-007).

Provisions one medication + one supplement health_input, logs each once,
then asserts:
  * GET .../health-input-log               returns both
  * GET .../health-input-log?input_type=medication  returns only the med
  * GET .../health-input-log?input_type=supplement  returns only the supp
  * GET .../health-input-log?input_type=bogus       returns 400

Closes PLAN-007 at the live-endpoint level.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone

from livetest.auth import login
from livetest.config import load_config
from livetest.pagination_assertions import assert_pagination_envelope
from livetest.pg import open_rls_connection
from livetest.report import print_live, write_markdown
from livetest.runner import Flow, FlowResult


class InputTypeFilterFlow(Flow):
    name = "input_type_filter"

    def run(self) -> FlowResult:
        med_id: str | None = None
        supp_id: str | None = None
        med_name = f"livetest-med-{uuid.uuid4().hex[:8]}"
        supp_name = f"livetest-supp-{uuid.uuid4().hex[:8]}"
        now_iso = datetime.now(timezone.utc).isoformat()

        with self.step("provision health_input medication"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/health-inputs",
                json={
                    "name": med_name,
                    "input_type": "medication",
                    "default_dosage": "10",
                    "default_unit": "mg",
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"med prereq failed: {resp.status_code} {resp.text}"
            )
            med_id = resp.json()["id"]

        with self.step("provision health_input supplement"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/health-inputs",
                json={
                    "name": supp_name,
                    "input_type": "supplement",
                    "default_dosage": "500",
                    "default_unit": "mg",
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"supp prereq failed: {resp.status_code} {resp.text}"
            )
            supp_id = resp.json()["id"]

        with self.step("log one dose of each"):
            for iid in (med_id, supp_id):
                resp = self.session.post(
                    f"{self.cfg.base_url}/api/v1/log-health-input",
                    json={
                        "input_id": iid,
                        "dosage": "1",
                        "timestamp": now_iso,
                    },
                    timeout=self.cfg.timeout,
                )
                assert resp.status_code == 201, (
                    f"log POST failed: {resp.status_code} {resp.text}"
                )

        with self.step("GET /health-input-log unfiltered returns both"):
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/health-input-log",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            entries = assert_pagination_envelope(resp.json(), "entries")
            found_med = any(e.get("input_name") == med_name for e in entries)
            found_supp = any(e.get("input_name") == supp_name for e in entries)
            assert found_med and found_supp, (
                f"unfiltered feed missing one: med={found_med}, supp={found_supp}"
            )

        with self.step("?input_type=medication returns only the med"):
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/health-input-log",
                params={"input_type": "medication"},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            entries = assert_pagination_envelope(resp.json(), "entries")
            assert all(e.get("input_type") == "medication" for e in entries), (
                f"non-medication rows leaked into medication filter: "
                f"{[e.get('input_type') for e in entries]}"
            )
            assert any(e.get("input_name") == med_name for e in entries), (
                f"medication filter did not return our test med {med_name}"
            )
            assert not any(e.get("input_name") == supp_name for e in entries), (
                f"medication filter returned our test supplement {supp_name}"
            )

        with self.step("?input_type=supplement returns only the supplement"):
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/health-input-log",
                params={"input_type": "supplement"},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            entries = assert_pagination_envelope(resp.json(), "entries")
            assert all(e.get("input_type") == "supplement" for e in entries), (
                f"non-supplement rows leaked into supplement filter: "
                f"{[e.get('input_type') for e in entries]}"
            )

        with self.step("?input_type=bogus is rejected"):
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/health-input-log",
                params={"input_type": "bogus"},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 400, (
                f"expected 400 for unknown input_type, got "
                f"{resp.status_code}: {resp.text}"
            )

        # Cleanup — log rows stay (no DELETE), but the health_inputs go
        with self.step("cleanup: DELETE medication input"):
            assert med_id is not None
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/health-inputs/{med_id}",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"med cleanup failed: {resp.status_code} {resp.text}"
            )

        with self.step("cleanup: DELETE supplement input"):
            assert supp_id is not None
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/health-inputs/{supp_id}",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"supp cleanup failed: {resp.status_code} {resp.text}"
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
        flow = InputTypeFilterFlow(cfg, session, conn, user_id)
        result = flow.run()
    finally:
        conn.close()

    print_live(result)
    report_path = write_markdown([result], cfg)
    print(f"\nReport: {report_path}")
    sys.exit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()

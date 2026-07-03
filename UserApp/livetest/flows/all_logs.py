"""Live test: /api/v1/all-logs read-only feed.

Combined log feed endpoint — merges health_input_log,
health_blood_pressure_readings, health_metrics, and others into a
single list sorted by timestamp. This flow exercises just the
health_input_log path.

Since the log endpoint returns generated log row ids (not mappable
from the POST /log-stack response), the flow uses the *stack name*
as the correlation key: it provisions a stack with a unique
``livetest-alllogs-stack-<hex>`` name, logs it, and asserts at
least one entry in /all-logs has ``stack == <that name>`` and
``type == 'health_input'``.

Write-only log endpoints don't have a DELETE counterpart, so the
log row is left behind. Cleanup only touches the parent stack and
health_input.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone

from livetest.auth import login
from livetest.config import load_config
from livetest.pagination_assertions import assert_pagination_envelope
from livetest.pg import count_rows, open_rls_connection
from livetest.report import print_live, write_markdown
from livetest.runner import Flow, FlowResult


class AllLogsFlow(Flow):
    name = "all_logs"

    def run(self) -> FlowResult:
        cur = self.conn.cursor()
        input_id: str | None = None
        stack_id: str | None = None
        input_name = f"livetest-alllogs-vit-{uuid.uuid4().hex[:8]}"
        stack_name = f"livetest-alllogs-stack-{uuid.uuid4().hex[:8]}"
        input_log_before = 0
        now_iso = datetime.now(timezone.utc).isoformat()

        with self.step("provision prereq health_input"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/health-inputs",
                json={
                    "name": input_name,
                    "input_type": "supplement",
                    "default_dosage": "250",
                    "default_unit": "mg",
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"health_input prereq failed: {resp.status_code} {resp.text}"
            )
            input_id = resp.json()["id"]

        with self.step("provision prereq stack"):
            assert input_id is not None, "input_id not set"
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/stacks",
                json={
                    "name": stack_name,
                    "inputs": [
                        {"input_id": input_id, "dosage_override": "500"},
                    ],
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"stack prereq failed: {resp.status_code} {resp.text}"
            )
            stack_id = resp.json()["id"]

        with self.step("count health_input_log rows before"):
            input_log_before = count_rows(
                cur,
                "health_input_log",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )

        with self.step("POST /api/v1/log-stack (creates the log row)"):
            assert stack_id is not None, "stack_id not set"
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/log-stack",
                json={"stack_id": stack_id, "timestamp": now_iso},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"expected 201, got {resp.status_code}: {resp.text}"
            )

        with self.step("verify log row in DB (delta check)"):
            input_log_after = count_rows(
                cur,
                "health_input_log",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )
            assert input_log_after == input_log_before + 1, (
                f"health_input_log delta "
                f"{input_log_after - input_log_before}, expected 1 "
                f"(before={input_log_before}, after={input_log_after})"
            )

        with self.step("GET /api/v1/all-logs includes log entry"):
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/all-logs",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            logs = assert_pagination_envelope(resp.json(), "entries")
            match = [
                log for log in logs
                if log.get("type") == "health_input"
                and log.get("stack") == stack_name
            ]
            assert match, (
                f"no health_input log with stack={stack_name!r} in "
                f"/all-logs response ({len(logs)} total entries)"
            )

        # Cleanup parents only; log row stays behind (no DELETE endpoint).
        with self.step("cleanup: DELETE stack"):
            assert stack_id is not None, "stack_id not set"
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/stacks/{stack_id}",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"stack cleanup failed: {resp.status_code} {resp.text}"
            )

        with self.step("cleanup: DELETE health_input"):
            assert input_id is not None, "input_id not set"
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/health-inputs/{input_id}",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"health_input cleanup failed: {resp.status_code} {resp.text}"
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
        flow = AllLogsFlow(cfg, session, conn, user_id)
        result = flow.run()
    finally:
        conn.close()

    print_live(result)
    report_path = write_markdown([result], cfg)
    print(f"\nReport: {report_path}")
    sys.exit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()

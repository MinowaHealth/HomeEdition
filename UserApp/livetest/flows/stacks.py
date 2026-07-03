"""Live test: /api/v1/stacks CRUD against stacks + stack_inputs.

Provisions a temporary health_input as a prereq, creates a stack that
references it, verifies both parent and child rows exist, then
cleans up in reverse order.

Note: the stack API accepts ``inputs[].input_id`` but the database
column is ``stack_inputs.health_input_id``. The route handler maps
``input_id`` → ``health_input_id`` on insert; don't send the DB
column name on the wire.
"""
from __future__ import annotations

import sys
import uuid

from livetest.auth import login
from livetest.config import load_config
from livetest.pagination_assertions import assert_pagination_envelope
from livetest.pg import count_rows, open_rls_connection
from livetest.report import print_live, write_markdown
from livetest.runner import Flow, FlowResult


class StacksFlow(Flow):
    name = "stacks"

    def run(self) -> FlowResult:
        cur = self.conn.cursor()
        input_id: str | None = None
        stack_id: str | None = None
        input_name = f"livetest-vitamin-c-{uuid.uuid4().hex[:8]}"
        stack_name = f"livetest-morning-stack-{uuid.uuid4().hex[:8]}"
        stacks_before = 0
        stack_inputs_before = 0

        with self.step("provision prereq health_input"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/health-inputs",
                json={
                    "name": input_name,
                    "input_type": "supplement",
                    "default_dosage": "500",
                    "default_unit": "mg",
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"prereq health_input POST failed: "
                f"{resp.status_code} {resp.text}"
            )
            body = resp.json()
            assert "id" in body, f"prereq POST response missing 'id': {body}"
            input_id = body["id"]

        with self.step("count rows before"):
            stacks_before = count_rows(
                cur,
                "stacks",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )
            stack_inputs_before = count_rows(
                cur,
                "stack_inputs",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )

        with self.step("POST /api/v1/stacks"):
            assert input_id is not None, "input_id not set (prereq failed)"
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/stacks",
                json={
                    "name": stack_name,
                    "inputs": [
                        {"input_id": input_id, "dosage_override": "1000"},
                    ],
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"expected 201, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert "id" in body, f"POST response missing 'id' field: {body}"
            stack_id = body["id"]

        with self.step("verify stack + stack_input rows in DB (delta check)"):
            stacks_after = count_rows(
                cur,
                "stacks",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )
            stack_inputs_after = count_rows(
                cur,
                "stack_inputs",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )
            assert stacks_after == stacks_before + 1, (
                f"stacks delta {stacks_after - stacks_before}, expected 1 "
                f"(before={stacks_before}, after={stacks_after})"
            )
            assert stack_inputs_after == stack_inputs_before + 1, (
                f"stack_inputs delta "
                f"{stack_inputs_after - stack_inputs_before}, expected 1 "
                f"(before={stack_inputs_before}, after={stack_inputs_after})"
            )

        with self.step("GET /api/v1/stacks includes created stack"):
            assert stack_id is not None, "stack_id not set (prior step failed)"
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/stacks",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            stacks = assert_pagination_envelope(resp.json(), "entries")
            assert any(s.get("id") == stack_id for s in stacks), (
                f"stack {stack_id} not in GET response "
                f"({len(stacks)} stacks returned)"
            )

        with self.step("DELETE /api/v1/stacks/{id}"):
            assert stack_id is not None, "stack_id not set (prior step failed)"
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/stacks/{stack_id}",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )

        with self.step("cleanup prereq health_input"):
            assert input_id is not None, "input_id not set (prereq failed)"
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/health-inputs/{input_id}",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"prereq cleanup failed: {resp.status_code} {resp.text}"
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
        flow = StacksFlow(cfg, session, conn, user_id)
        result = flow.run()
    finally:
        conn.close()

    print_live(result)
    report_path = write_markdown([result], cfg)
    print(f"\nReport: {report_path}")
    sys.exit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()

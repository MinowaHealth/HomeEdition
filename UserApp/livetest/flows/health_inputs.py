"""Live test: /api/v1/health-inputs CRUD against health_inputs.

Leaf flow — no prereq. Tests the full CRUD cycle (POST/GET/PUT/DELETE)
plus a DB delta check. Uses ``input_type='supplement'`` as the
required type discriminator; the plan's suggested ``category`` field
lives in the ``custom_fields`` JSONB column and is an optional
annotation, not a required field.
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


class HealthInputsFlow(Flow):
    name = "health_inputs"

    def run(self) -> FlowResult:
        cur = self.conn.cursor()
        input_id: str | None = None
        name = f"livetest-vitamin-d-{uuid.uuid4().hex[:8]}"
        before = 0

        with self.step("count rows before"):
            before = count_rows(
                cur,
                "health_inputs",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )

        with self.step("POST /api/v1/health-inputs"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/health-inputs",
                json={
                    "name": name,
                    "input_type": "supplement",
                    "default_dosage": "1000",
                    # Alias on purpose — the server must normalize 'IU' -> 'iu'
                    "default_unit": "IU",
                    "category": "supplement",
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"expected 201, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert "id" in body, f"POST response missing 'id' field: {body}"
            input_id = body["id"]

        with self.step("verify row exists in DB (delta check)"):
            after = count_rows(
                cur,
                "health_inputs",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )
            assert after == before + 1, (
                f"delta {after - before}, expected 1 "
                f"(before={before}, after={after})"
            )

        with self.step("verify default_unit normalized to canonical 'iu'"):
            cur.execute(
                "SELECT default_unit FROM health_inputs "
                "WHERE tenant_id=%s AND user_id=%s AND id=%s",
                (self.cfg.tenant_id, self.user_id, input_id),
            )
            row = cur.fetchone()
            assert row is not None, f"health_input {input_id} not found in DB"
            assert row["default_unit"] == "iu", (
                f"expected normalized 'iu', got {row['default_unit']!r}"
            )

        with self.step("GET /api/v1/health-inputs includes created item"):
            assert input_id is not None, "input_id not set (prior step failed)"
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/health-inputs",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            items = assert_pagination_envelope(resp.json(), "entries")
            assert any(i.get("id") == input_id for i in items), (
                f"health_input {input_id} not in GET response "
                f"({len(items)} items returned)"
            )

        with self.step("PUT /api/v1/health-inputs/{id}"):
            assert input_id is not None, "input_id not set (prior step failed)"
            # PUT merges with existing row — only send the fields we want to change.
            resp = self.session.put(
                f"{self.cfg.base_url}/api/v1/health-inputs/{input_id}",
                json={"default_dosage": "2000"},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )

        with self.step("DELETE /api/v1/health-inputs/{id}"):
            assert input_id is not None, "input_id not set (prior step failed)"
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/health-inputs/{input_id}",
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
        flow = HealthInputsFlow(cfg, session, conn, user_id)
        result = flow.run()
    finally:
        conn.close()

    print_live(result)
    report_path = write_markdown([result], cfg)
    print(f"\nReport: {report_path}")
    sys.exit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()

"""Live test: /api/v1/observations CRUD.

Observations are free-text health notes (health_observations table).
POST returns {id, message}; a background thread embeds the content, but the
row is committed synchronously so the delta check is deterministic.
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


class ObservationsFlow(Flow):
    name = "observations"

    def run(self) -> FlowResult:
        cur = self.conn.cursor()
        obs_id: str | None = None
        content = f"livetest-obs-{uuid.uuid4().hex[:8]} felt unusually energetic"
        before = 0

        with self.step("count rows before"):
            before = count_rows(
                cur,
                "health_observations",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )

        with self.step("POST /api/v1/observations"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/observations",
                json={
                    "observation": content,
                    "source_type": "text",
                    "mental_health_flag": False,
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"expected 201, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert "id" in body, f"POST response missing 'id': {body}"
            obs_id = body["id"]

        with self.step("verify row exists in DB (delta check)"):
            after = count_rows(
                cur,
                "health_observations",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )
            assert after == before + 1, (
                f"delta {after - before}, expected 1 "
                f"(before={before}, after={after})"
            )

        with self.step("GET /api/v1/observations includes created observation"):
            assert obs_id is not None, "obs_id not set (prior step failed)"
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/observations",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            items = assert_pagination_envelope(resp.json(), "entries")
            assert any(o.get("id") == obs_id for o in items), (
                f"observation {obs_id} not in GET response "
                f"({len(items)} items returned)"
            )

        with self.step("PUT /api/v1/observations/{id}"):
            assert obs_id is not None, "obs_id not set (prior step failed)"
            resp = self.session.put(
                f"{self.cfg.base_url}/api/v1/observations/{obs_id}",
                json={
                    "observation": f"{content} (updated)",
                    "source_type": "text",
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )

        with self.step("DELETE /api/v1/observations/{id}"):
            assert obs_id is not None, "obs_id not set (prior step failed)"
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/observations/{obs_id}",
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
        flow = ObservationsFlow(cfg, session, conn, user_id)
        result = flow.run()
    finally:
        conn.close()

    print_live(result)
    report_path = write_markdown([result], cfg)
    print(f"\nReport: {report_path}")
    sys.exit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()

"""Live test: /api/v1/timeframes CRUD against timeframes.

Leaf flow — no prereq. Tests the full CRUD cycle.

Note: unlike health_inputs, the timeframes PUT handler is NOT a
partial update — it fully rewrites the row, and ``data['name']`` is
required. The flow's PUT step sends the same name plus an updated
``sort_order`` to exercise an actual change.
"""
from __future__ import annotations

import sys
import uuid

from livetest.auth import login
from livetest.config import load_config
from livetest.pg import count_rows, open_rls_connection
from livetest.report import print_live, write_markdown
from livetest.runner import Flow, FlowResult


class TimeframesFlow(Flow):
    name = "timeframes"

    def run(self) -> FlowResult:
        cur = self.conn.cursor()
        timeframe_id: str | None = None
        name = f"livetest-tf-{uuid.uuid4().hex[:8]}"
        before = 0

        with self.step("count rows before"):
            before = count_rows(
                cur,
                "timeframes",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )

        with self.step("POST /api/v1/timeframes"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/timeframes",
                json={
                    "name": name,
                    "time_of_day": "08:00",
                    "sort_order": 1,
                    "is_active": True,
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"expected 201, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert "id" in body, f"POST response missing 'id' field: {body}"
            timeframe_id = body["id"]

        with self.step("verify row exists in DB (delta check)"):
            after = count_rows(
                cur,
                "timeframes",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )
            assert after == before + 1, (
                f"delta {after - before}, expected 1 "
                f"(before={before}, after={after})"
            )

        with self.step("GET /api/v1/timeframes includes created item"):
            assert timeframe_id is not None, "timeframe_id not set (prior step failed)"
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/timeframes",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            items = resp.json()
            assert any(i.get("id") == timeframe_id for i in items), (
                f"timeframe {timeframe_id} not in GET response "
                f"({len(items)} items returned)"
            )

        with self.step("PUT /api/v1/timeframes/{id}"):
            assert timeframe_id is not None, "timeframe_id not set (prior step failed)"
            # PUT is a full rewrite — must send name. Change sort_order to
            # exercise an actual value flip.
            resp = self.session.put(
                f"{self.cfg.base_url}/api/v1/timeframes/{timeframe_id}",
                json={
                    "name": name,
                    "time_of_day": "09:00",
                    "sort_order": 2,
                    "is_active": True,
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )

        with self.step("DELETE /api/v1/timeframes/{id}"):
            assert timeframe_id is not None, "timeframe_id not set (prior step failed)"
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/timeframes/{timeframe_id}",
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
        flow = TimeframesFlow(cfg, session, conn, user_id)
        result = flow.run()
    finally:
        conn.close()

    print_live(result)
    report_path = write_markdown([result], cfg)
    print(f"\nReport: {report_path}")
    sys.exit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()

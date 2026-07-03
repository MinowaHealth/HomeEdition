"""Live test: /api/v1/reminders CRUD + /complete + /snooze.

Reminders have side endpoints (POST /complete, POST /snooze) beyond plain
CRUD. The spec flags both, so this flow exercises all of them in one pass.
The `title` column carries the `livetest-` prefix for cleanup targeting.
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


class RemindersFlow(Flow):
    name = "reminders"

    def run(self) -> FlowResult:
        cur = self.conn.cursor()
        reminder_id: str | None = None
        title = f"livetest-reminder-{uuid.uuid4().hex[:8]}"
        before = 0

        with self.step("count rows before"):
            before = count_rows(
                cur,
                "reminders",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )

        with self.step("POST /api/v1/reminders"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/reminders",
                json={
                    "title": title,
                    "time": "08:30",
                    "category": "medication",
                    "frequency": "daily",
                    "enabled": True,
                    "notes": "created by livetest harness",
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"expected 201, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert "id" in body, f"POST response missing 'id': {body}"
            reminder_id = body["id"]

        with self.step("verify row exists in DB (delta check)"):
            after = count_rows(
                cur,
                "reminders",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )
            assert after == before + 1, (
                f"delta {after - before}, expected 1 "
                f"(before={before}, after={after})"
            )

        with self.step("GET /api/v1/reminders includes created reminder"):
            assert reminder_id is not None, "reminder_id not set"
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/reminders",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            items = assert_pagination_envelope(resp.json(), "entries")
            assert any(r.get("id") == reminder_id for r in items), (
                f"reminder {reminder_id} not in GET response "
                f"({len(items)} items returned)"
            )

        with self.step("PUT /api/v1/reminders/{id}"):
            assert reminder_id is not None, "reminder_id not set"
            resp = self.session.put(
                f"{self.cfg.base_url}/api/v1/reminders/{reminder_id}",
                json={
                    "title": title,
                    "time": "09:15",
                    "category": "medication",
                    "frequency": "daily",
                    "enabled": True,
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )

        with self.step("POST /api/v1/reminders/{id}/complete"):
            assert reminder_id is not None, "reminder_id not set"
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/reminders/{reminder_id}/complete",
                json={},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )

        with self.step("POST /api/v1/reminders/{id}/snooze"):
            assert reminder_id is not None, "reminder_id not set"
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/reminders/{reminder_id}/snooze",
                json={"minutes": 15},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )

        with self.step("DELETE /api/v1/reminders/{id}"):
            assert reminder_id is not None, "reminder_id not set"
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/reminders/{reminder_id}",
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
        flow = RemindersFlow(cfg, session, conn, user_id)
        result = flow.run()
    finally:
        conn.close()

    print_live(result)
    report_path = write_markdown([result], cfg)
    print(f"\nReport: {report_path}")
    sys.exit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()

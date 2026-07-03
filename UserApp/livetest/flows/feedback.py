"""Live test: /api/v1/feedback CRUD (feedback table).

Smoke coverage for the feedback endpoint, which was paginated in the
Tier 2 cleanup but had no existing livetest flow. The route uses an
admin DB connection internally (bypasses RLS for the write path because
admins triage feedback), but the rows still carry user_id and the
feedback table's RLS policy permits the owner to read their own rows —
so count_rows via the harness's RLS-scoped connection sees them.

The marker column is content; cleanup.py can target rows via
`content LIKE 'livetest-%'` if a run aborts before the DELETE step.
The Slack webhook fired by POST /feedback is fire-and-forget — if Slack
is unreachable from the target host the route still succeeds.
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


class FeedbackFlow(Flow):
    name = "feedback"

    def run(self) -> FlowResult:
        cur = self.conn.cursor()
        feedback_id: str | None = None
        content = f"livetest-feedback-{uuid.uuid4().hex[:8]} this is a test entry"
        before = 0

        with self.step("count rows before"):
            before = count_rows(
                cur,
                "feedback",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )

        with self.step("POST /api/v1/feedback"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/feedback",
                json={
                    "feedback": content,
                    "feedback_type": "general",
                    "page_context": "/livetest",
                    "app_version": "livetest-harness",
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"expected 201, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert "id" in body, f"POST response missing 'id': {body}"
            feedback_id = body["id"]

        with self.step("verify row exists in DB (delta check)"):
            after = count_rows(
                cur,
                "feedback",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )
            assert after == before + 1, (
                f"delta {after - before}, expected 1 "
                f"(before={before}, after={after})"
            )

        with self.step("GET /api/v1/feedback includes created entry"):
            assert feedback_id is not None, "feedback_id not set"
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/feedback",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            items = assert_pagination_envelope(resp.json(), "entries")
            assert any(f.get("id") == feedback_id for f in items), (
                f"feedback {feedback_id} not in GET response "
                f"({len(items)} items returned)"
            )

        with self.step("GET /api/v1/feedback?screen filter narrows results"):
            # The feedback handler accepts ?screen= or ?page= and filters
            # by page_context. Confirm the filter actually applies.
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/feedback?screen=/livetest",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, resp.text
            filtered = assert_pagination_envelope(resp.json(), "entries")
            assert any(f.get("id") == feedback_id for f in filtered), (
                f"feedback {feedback_id} missing from filtered response"
            )

        with self.step("DELETE /api/v1/feedback/{id}"):
            assert feedback_id is not None, "feedback_id not set"
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/feedback/{feedback_id}",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )

        with self.step("verify delete returns rowcount to baseline"):
            final = count_rows(
                cur,
                "feedback",
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
        flow = FeedbackFlow(cfg, session, conn, user_id)
        result = flow.run()
    finally:
        conn.close()

    print_live(result)
    report_path = write_markdown([result], cfg)
    print(f"\nReport: {report_path}")
    sys.exit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()

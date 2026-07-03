"""Live test: /api/v1/log-promotions CRUD (log_promotions table).

Smoke coverage for the log-promotions endpoint, which was paginated in
the Tier 2 cleanup but had no existing livetest flow.

The schema does not enforce a FK on source_log_id (column is `uuid NOT
NULL` but unconstrained), so this flow uses a random UUID for the
source_log_id field — a real source row is not required for the smoke
test. The marker column for cleanup is `free_text_original`; cleanup.py
can target rows via `free_text_original LIKE 'livetest-%'` if a run
aborts before the DELETE step.
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


class LogPromotionsFlow(Flow):
    name = "log_promotions"

    def run(self) -> FlowResult:
        cur = self.conn.cursor()
        promo_id: str | None = None
        free_text = f"livetest-promo-{uuid.uuid4().hex[:8]}"
        synthetic_source_log_id = str(uuid.uuid4())
        before = 0

        with self.step("count rows before"):
            before = count_rows(
                cur,
                "log_promotions",
                "tenant_id=%s AND user_id=%s AND is_deleted = 0",
                (self.cfg.tenant_id, self.user_id),
            )

        with self.step("POST /api/v1/log-promotions"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/log-promotions",
                json={
                    "source_table": "health_input_log",
                    "source_log_id": synthetic_source_log_id,
                    "free_text_original": free_text,
                    "match_confidence": 0.85,
                    "match_method": "fuzzy",
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"expected 201, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert "id" in body, f"POST response missing 'id': {body}"
            promo_id = body["id"]

        with self.step("verify row exists in DB (delta check)"):
            after = count_rows(
                cur,
                "log_promotions",
                "tenant_id=%s AND user_id=%s AND is_deleted = 0",
                (self.cfg.tenant_id, self.user_id),
            )
            assert after == before + 1, (
                f"delta {after - before}, expected 1 "
                f"(before={before}, after={after})"
            )

        with self.step("GET /api/v1/log-promotions includes created promo"):
            assert promo_id is not None, "promo_id not set"
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/log-promotions",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            items = assert_pagination_envelope(resp.json(), "entries")
            assert any(p.get("id") == promo_id for p in items), (
                f"promotion {promo_id} not in GET response "
                f"({len(items)} items returned)"
            )

        with self.step("GET /api/v1/log-promotions?status=pending filter"):
            # Newly-created promos default to status='pending'; the filter
            # should still surface our row.
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/log-promotions?status=pending",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, resp.text
            filtered = assert_pagination_envelope(resp.json(), "entries")
            assert any(p.get("id") == promo_id for p in filtered), (
                f"promotion {promo_id} missing from status=pending response"
            )

        with self.step("PUT /api/v1/log-promotions/{id} dismiss"):
            assert promo_id is not None, "promo_id not set"
            resp = self.session.put(
                f"{self.cfg.base_url}/api/v1/log-promotions/{promo_id}",
                json={"status": "dismissed"},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )

        with self.step("DELETE /api/v1/log-promotions/{id}"):
            assert promo_id is not None, "promo_id not set"
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/log-promotions/{promo_id}",
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
        flow = LogPromotionsFlow(cfg, session, conn, user_id)
        result = flow.run()
    finally:
        conn.close()

    print_live(result)
    report_path = write_markdown([result], cfg)
    print(f"\nReport: {report_path}")
    sys.exit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()

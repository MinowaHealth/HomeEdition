"""Live test: /api/v1/food-items CRUD against health_food_itemsv2."""
from __future__ import annotations

import sys
import uuid

from livetest.auth import login
from livetest.config import load_config
from livetest.pagination_assertions import assert_pagination_envelope
from livetest.pg import count_rows, open_rls_connection
from livetest.report import print_live, write_markdown
from livetest.runner import Flow, FlowResult


class FoodItemsFlow(Flow):
    name = "food_items"

    def run(self) -> FlowResult:
        cur = self.conn.cursor()
        item_id: str | None = None
        name = f"livetest-apple-{uuid.uuid4().hex[:8]}"
        before = 0

        with self.step("count rows before"):
            before = count_rows(
                cur,
                "health_food_itemsv2",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )

        with self.step("POST /api/v1/food-items"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/food-items",
                json={
                    "name": name,
                    "calories": 95,
                    "protein_g": 0.5,
                    "carbs_g": 25.0,
                    "fat_g": 0.3,
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"expected 201, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert "id" in body, f"POST response missing 'id' field: {body}"
            item_id = body["id"]

        with self.step("verify row exists in DB (delta check)"):
            after = count_rows(
                cur,
                "health_food_itemsv2",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )
            assert after == before + 1, (
                f"delta {after - before}, expected 1 "
                f"(before={before}, after={after})"
            )

        with self.step("GET /api/v1/food-items includes created item"):
            assert item_id is not None, "item_id not set (prior step failed)"
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/food-items",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            items = assert_pagination_envelope(resp.json(), "entries")
            assert any(i.get("id") == item_id for i in items), (
                f"item {item_id} not in GET response ({len(items)} items returned)"
            )

        # Pagination contract verification — exercises the four contract
        # points the UserAPIPagination spec promises:
        #
        #   1. limit clamps the page size
        #   2. has_more is true when there's another page available
        #   3. offset advances the window without changing total
        #   4. an out-of-range offset returns an empty page (not 4xx)
        #
        # Done here in food_items because food-items is the canonical
        # exemplar in LiveTests.md and food-items has a high enough default
        # max_limit (500) that we can exercise the contract without bumping
        # into clamps. The same envelope shape applies to every paginated
        # endpoint, so verifying it here is a regression net for all of
        # them.
        with self.step("pagination contract: limit=1 returns 1 item, has_more"):
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/food-items?limit=1&offset=0",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            items = assert_pagination_envelope(body, "entries")
            assert len(items) == 1, f"expected 1 item with limit=1, got {len(items)}"
            page1_total = body["pagination"]["total"]
            assert page1_total >= 1, f"total {page1_total} < 1, expected our row"
            # has_more must be true iff there's a row past offset 0
            assert body["pagination"]["has_more"] == (page1_total > 1), (
                f"has_more={body['pagination']['has_more']} disagrees with "
                f"total={page1_total}"
            )
            page1_id = items[0].get("id")

        with self.step("pagination contract: offset advances window"):
            # Only meaningful if there's > 1 row to page over
            if page1_total > 1:
                resp = self.session.get(
                    f"{self.cfg.base_url}/api/v1/food-items?limit=1&offset=1",
                    timeout=self.cfg.timeout,
                )
                assert resp.status_code == 200, resp.text
                body = resp.json()
                items = assert_pagination_envelope(body, "entries")
                assert len(items) == 1, f"expected 1 item, got {len(items)}"
                # Total must be stable across pages
                assert body["pagination"]["total"] == page1_total, (
                    f"total drifted: page1={page1_total}, "
                    f"page2={body['pagination']['total']}"
                )
                # The offset=1 row must be a different id than offset=0
                assert items[0].get("id") != page1_id, (
                    f"offset=1 returned the same id as offset=0: {page1_id}"
                )

        with self.step("pagination contract: out-of-range offset returns empty"):
            far_offset = max(page1_total + 100, 999)
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/food-items"
                f"?limit=10&offset={far_offset}",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            items = assert_pagination_envelope(body, "entries")
            assert items == [], f"expected empty page past end, got {len(items)} items"
            assert body["pagination"]["has_more"] is False, (
                f"has_more should be False past the end, got {body['pagination']['has_more']}"
            )
            # NOTE: pagination.total is NOT asserted here. When the page is
            # empty, count(*) OVER() has no rows to project a value into, so
            # the backend's `total = rows[0]['_total'] if rows else 0` falls
            # back to 0. This is a known limitation of the single-query
            # window-function approach — total is only meaningful when
            # items is non-empty. Hardening this would require either a
            # separate COUNT query on every empty page (extra roundtrip)
            # or a CTE-with-sentinel-row pattern (much uglier SQL).
            # Documented as a follow-up in UserAPIPagination.md.

        with self.step("pagination contract: limit above max_limit is clamped"):
            # food-items uses default_limit=100, max_limit=500. Sending
            # limit=99999 must clamp to 500, not error and not honor it.
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/food-items?limit=99999&offset=0",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert_pagination_envelope(body, "entries")
            assert body["pagination"]["limit"] == 500, (
                f"limit not clamped: expected 500, got {body['pagination']['limit']}"
            )

        with self.step("PUT /api/v1/food-items/{id}"):
            assert item_id is not None, "item_id not set (prior step failed)"
            resp = self.session.put(
                f"{self.cfg.base_url}/api/v1/food-items/{item_id}",
                json={
                    "name": name,
                    "calories": 100,
                    "protein_g": 0.5,
                    "carbs_g": 26.0,
                    "fat_g": 0.3,
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )

        with self.step("DELETE /api/v1/food-items/{id}"):
            assert item_id is not None, "item_id not set (prior step failed)"
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/food-items/{item_id}",
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
        flow = FoodItemsFlow(cfg, session, conn, user_id)
        result = flow.run()
    finally:
        conn.close()

    print_live(result)
    report_path = write_markdown([result], cfg)
    print(f"\nReport: {report_path}")
    sys.exit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()

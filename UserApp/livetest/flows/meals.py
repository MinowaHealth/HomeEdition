"""Live test: /api/v1/meals CRUD against meals + meal_items.

Provisions a temporary food item as a prereq, creates a meal that
references it, verifies the meal + meal_item rows exist, then cleans
up both in reverse order.
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


class MealsFlow(Flow):
    name = "meals"

    def run(self) -> FlowResult:
        cur = self.conn.cursor()
        food_id: str | None = None
        meal_id: str | None = None
        food_name = f"livetest-banana-{uuid.uuid4().hex[:8]}"
        meal_name = f"livetest-breakfast-{uuid.uuid4().hex[:8]}"
        meals_before = 0
        items_before = 0

        with self.step("provision prereq food item"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/food-items",
                json={
                    "name": food_name,
                    "calories": 105,
                    "carbs_g": 27.0,
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"prereq food item POST failed: "
                f"{resp.status_code} {resp.text}"
            )
            body = resp.json()
            assert "id" in body, f"prereq POST response missing 'id': {body}"
            food_id = body["id"]

        with self.step("count rows before"):
            meals_before = count_rows(
                cur,
                "meals",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )
            items_before = count_rows(
                cur,
                "meal_items",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )

        with self.step("POST /api/v1/meals"):
            assert food_id is not None, "food_id not set (prereq step failed)"
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/meals",
                json={
                    "name": meal_name,
                    "items": [
                        {"food_item_id": food_id, "servings": 1},
                    ],
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"expected 201, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert "id" in body, f"POST response missing 'id' field: {body}"
            meal_id = body["id"]

        with self.step("verify meal + meal_item rows in DB (delta check)"):
            meals_after = count_rows(
                cur,
                "meals",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )
            items_after = count_rows(
                cur,
                "meal_items",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )
            assert meals_after == meals_before + 1, (
                f"meals delta {meals_after - meals_before}, expected 1 "
                f"(before={meals_before}, after={meals_after})"
            )
            assert items_after == items_before + 1, (
                f"meal_items delta {items_after - items_before}, expected 1 "
                f"(before={items_before}, after={items_after})"
            )

        with self.step("GET /api/v1/meals includes created meal"):
            assert meal_id is not None, "meal_id not set (prior step failed)"
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/meals",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            meals = assert_pagination_envelope(resp.json(), "entries")
            assert any(m.get("id") == meal_id for m in meals), (
                f"meal {meal_id} not in GET response "
                f"({len(meals)} meals returned)"
            )

        with self.step("DELETE /api/v1/meals/{id}"):
            assert meal_id is not None, "meal_id not set (prior step failed)"
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/meals/{meal_id}",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )

        with self.step("cleanup prereq food item"):
            assert food_id is not None, "food_id not set (prereq step failed)"
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/food-items/{food_id}",
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
        flow = MealsFlow(cfg, session, conn, user_id)
        result = flow.run()
    finally:
        conn.close()

    print_live(result)
    report_path = write_markdown([result], cfg)
    print(f"\nReport: {report_path}")
    sys.exit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()

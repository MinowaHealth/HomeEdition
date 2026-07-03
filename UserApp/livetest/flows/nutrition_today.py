"""Live test: nutrition projection food_log → health_metrics + /nutrition/today.

PilotFeedback.md § B4. Verifies the write-path projection
that bridges health_food_logv2 (what was eaten) and health_metrics
(what the read path queries) — and the /nutrition/today convenience
endpoint that aggregates the day's totals in the user's local timezone.

Coverage:
  - POST /log-food-item with a catalog item projects one nutrition row
  - POST /log-meal projects one nutrition row per meal_item
  - /nutrition/today totals match the sum of logged macros
  - /nutrition/today entries include the per-meal breakdown
  - Freeform log (free_text only) does NOT project — projector returns None

Append-only logs are not deleted (matches log_stack_meal pattern); only
parent resources (food_item, meal) and projected metric rows are cleaned
up. Metric cleanup is direct DB DELETE since there's no API for it.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone

from livetest.auth import login
from livetest.config import load_config
from livetest.pg import count_rows, open_rls_connection
from livetest.report import print_live, write_markdown
from livetest.runner import Flow, FlowResult


# Per-serving nutrition for the prereq food item. Picked so doubling
# (servings=2) and re-summing produces non-trivial round numbers that
# would catch a missing or duplicated multiply.
_FOOD_CALORIES = 100
_FOOD_PROTEIN_G = 5.0
_FOOD_CARBS_G = 20.0
_FOOD_FAT_G = 2.5


class NutritionTodayFlow(Flow):
    name = "nutrition_today"

    def run(self) -> FlowResult:
        cur = self.conn.cursor()
        food_id: str | None = None
        meal_id: str | None = None
        log_food_id: str | None = None
        log_meal_id: str | None = None  # noqa: F841 (returned/unused after assert)
        food_name = f"livetest-nut-food-{uuid.uuid4().hex[:8]}"
        meal_name = f"livetest-nut-meal-{uuid.uuid4().hex[:8]}"
        # Use 'now' so the projection lands inside today's user-tz window.
        # The /nutrition/today endpoint reads today_local; if the test ran
        # at 23:59 local on a day-boundary, the projection might end up in
        # tomorrow — accepted risk for a livetest, server clock skew is
        # the bigger source of flakes either way.
        now_iso = datetime.now(timezone.utc).isoformat()

        with self.step("baseline: count nutrition rows in health_metrics"):
            metrics_before = count_rows(
                cur,
                "health_metrics",
                "tenant_id=%s AND user_id=%s AND metric_type='nutrition'",
                (self.cfg.tenant_id, self.user_id),
            )

        with self.step("provision prereq food item with full macros"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/food-items",
                json={
                    "name": food_name,
                    "calories": _FOOD_CALORIES,
                    "protein_g": _FOOD_PROTEIN_G,
                    "carbs_g": _FOOD_CARBS_G,
                    "fat_g": _FOOD_FAT_G,
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"prereq food item POST failed: "
                f"{resp.status_code} {resp.text}"
            )
            food_id = resp.json()["id"]

        with self.step("POST /api/v1/log-food-item (catalog, servings=1)"):
            assert food_id is not None
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/log-food-item",
                json={
                    "food_item_id": food_id,
                    "servings": 1,
                    "timestamp": now_iso,
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"expected 201, got {resp.status_code}: {resp.text}"
            )
            log_food_id = resp.json()["id"]

        with self.step("verify projector wrote 1 nutrition metric row"):
            metrics_after_one = count_rows(
                cur,
                "health_metrics",
                "tenant_id=%s AND user_id=%s AND metric_type='nutrition'",
                (self.cfg.tenant_id, self.user_id),
            )
            assert metrics_after_one == metrics_before + 1, (
                f"health_metrics(nutrition) delta "
                f"{metrics_after_one - metrics_before}, expected 1 "
                f"(before={metrics_before}, after={metrics_after_one})"
            )

        with self.step("verify source_log_id provenance set on projected row"):
            assert log_food_id is not None
            cur.execute(
                """
                SELECT value, unit, source, source_log_id, notes
                FROM health_metrics
                WHERE tenant_id=%s AND user_id=%s AND metric_type='nutrition'
                  AND source_log_id=%s
                """,
                (self.cfg.tenant_id, self.user_id, log_food_id),
            )
            row = cur.fetchone()
            assert row is not None, (
                f"no nutrition metric row found with source_log_id={log_food_id}"
            )
            assert float(row["value"]) == float(_FOOD_CALORIES), (
                f"expected value={_FOOD_CALORIES}, got {row['value']}"
            )
            assert row["unit"] == "kcal", f"expected unit=kcal, got {row['unit']}"
            assert row["source"] == "food_log", (
                f"expected source=food_log, got {row['source']}"
            )

        with self.step("provision prereq meal with same food, servings=2"):
            assert food_id is not None
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/meals",
                json={
                    "name": meal_name,
                    "items": [{"food_item_id": food_id, "servings": 2}],
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"meal prereq failed: {resp.status_code} {resp.text}"
            )
            meal_id = resp.json()["id"]

        with self.step("POST /api/v1/log-meal projects per-item nutrition"):
            assert meal_id is not None
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/log-meal",
                json={"meal_id": meal_id, "timestamp": now_iso},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"expected 201, got {resp.status_code}: {resp.text}"
            )
            assert resp.json().get("items_logged") == 1

            metrics_after_two = count_rows(
                cur,
                "health_metrics",
                "tenant_id=%s AND user_id=%s AND metric_type='nutrition'",
                (self.cfg.tenant_id, self.user_id),
            )
            assert metrics_after_two == metrics_before + 2, (
                f"after meal log, expected metrics_before+2={metrics_before + 2}, "
                f"got {metrics_after_two}"
            )

        with self.step("GET /api/v1/nutrition/today aggregates both logs"):
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/nutrition/today",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert "totals" in body, f"response missing totals: {body}"
            assert "entries" in body, f"response missing entries: {body}"
            totals = body["totals"]
            entries = body["entries"]

            # We logged: 1 serving + 2 servings = 3 servings of the prereq food.
            # Other livetest runs may have left other nutrition rows for today
            # (food_items flow doesn't log, but earlier nutrition_today runs
            # could). Filter to entries from THIS run via source_log_id.
            assert log_food_id is not None
            mine = [
                e for e in entries
                if e.get("source_log_id") == log_food_id
                or e.get("food_name") == food_name
            ]
            assert len(mine) == 2, (
                f"expected 2 entries from this run (1 food + 1 meal), "
                f"found {len(mine)} of {len(entries)} total entries"
            )

            # Sum macros for our 3 servings only.
            mine_calories = sum(e.get("calories", 0) or 0 for e in mine)
            mine_protein = sum(e.get("protein_g", 0) or 0 for e in mine)
            mine_carbs = sum(e.get("carbs_g", 0) or 0 for e in mine)
            mine_fat = sum(e.get("fat_g", 0) or 0 for e in mine)

            assert mine_calories == _FOOD_CALORIES * 3, (
                f"expected {_FOOD_CALORIES * 3} calories from this run, "
                f"got {mine_calories}"
            )
            assert mine_protein == _FOOD_PROTEIN_G * 3, (
                f"expected {_FOOD_PROTEIN_G * 3}g protein, got {mine_protein}"
            )
            assert mine_carbs == _FOOD_CARBS_G * 3, (
                f"expected {_FOOD_CARBS_G * 3}g carbs, got {mine_carbs}"
            )
            assert mine_fat == _FOOD_FAT_G * 3, (
                f"expected {_FOOD_FAT_G * 3}g fat, got {mine_fat}"
            )

            # Sanity: top-level totals must be at least our contribution.
            assert totals.get("calories", 0) >= mine_calories, (
                f"totals.calories ({totals.get('calories')}) < our "
                f"contribution ({mine_calories})"
            )

        with self.step("freeform log does NOT project (no nutrition data)"):
            metrics_pre_freeform = count_rows(
                cur,
                "health_metrics",
                "tenant_id=%s AND user_id=%s AND metric_type='nutrition'",
                (self.cfg.tenant_id, self.user_id),
            )
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/log-food-item",
                json={
                    "free_text": f"livetest-freeform-{uuid.uuid4().hex[:8]}",
                    "timestamp": now_iso,
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"freeform log failed: {resp.status_code} {resp.text}"
            )
            metrics_post_freeform = count_rows(
                cur,
                "health_metrics",
                "tenant_id=%s AND user_id=%s AND metric_type='nutrition'",
                (self.cfg.tenant_id, self.user_id),
            )
            assert metrics_post_freeform == metrics_pre_freeform, (
                f"freeform log should not project, but metrics grew "
                f"from {metrics_pre_freeform} to {metrics_post_freeform}"
            )

        # Cleanup: delete projected metric rows + parent resources.
        # Log rows are append-only and stay (Pass 4 cleanup handles them).
        with self.step("cleanup: delete projected nutrition metric rows"):
            assert food_id is not None
            cur.execute(
                """
                DELETE FROM health_metrics
                WHERE tenant_id=%s AND user_id=%s AND metric_type='nutrition'
                  AND source_log_id IN (
                      SELECT id FROM health_food_logv2
                      WHERE tenant_id=%s AND user_id=%s AND food_item_id=%s
                  )
                """,
                (self.cfg.tenant_id, self.user_id,
                 self.cfg.tenant_id, self.user_id, food_id),
            )
            self.conn.commit()

        with self.step("cleanup: DELETE meal"):
            assert meal_id is not None
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/meals/{meal_id}",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"meal cleanup failed: {resp.status_code} {resp.text}"
            )

        with self.step("cleanup: DELETE food_item"):
            assert food_id is not None
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/food-items/{food_id}",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"food_item cleanup failed: {resp.status_code} {resp.text}"
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
        flow = NutritionTodayFlow(cfg, session, conn, user_id)
        result = flow.run()
    finally:
        conn.close()

    print_live(result)
    report_path = write_markdown([result], cfg)
    print(f"\nReport: {report_path}")
    sys.exit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()

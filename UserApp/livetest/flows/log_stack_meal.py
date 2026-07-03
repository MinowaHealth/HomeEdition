"""Live test: /api/v1/log-stack + /api/v1/log-meal write-only flow.

Non-CRUD flow. Both endpoints are append-only — logs cannot be
deleted via the API, so this flow deliberately leaves log rows
behind (Pass 4's cleanup subcommand is responsible for removing
them). The flow provisions four parent resources, logs against
them, verifies the log rows exist, then deletes only the parent
resources (not the log rows).

Table mapping (NOT what the Pass 2 plan says — plan is wrong):
- ``POST /log-stack`` writes to ``health_input_log`` (one row per
  stack input).
- ``POST /log-meal`` writes to ``health_food_logv2`` (one row per
  meal item).

The flow counts both tables before/after and asserts each grew by
exactly 1 (since the prereq stack has 1 input and the prereq meal
has 1 item).
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


class LogStackMealFlow(Flow):
    name = "log_stack_meal"

    def run(self) -> FlowResult:
        cur = self.conn.cursor()
        food_id: str | None = None
        meal_id: str | None = None
        input_id: str | None = None
        stack_id: str | None = None
        food_name = f"livetest-apple-{uuid.uuid4().hex[:8]}"
        meal_name = f"livetest-meal-{uuid.uuid4().hex[:8]}"
        input_name = f"livetest-vitb-{uuid.uuid4().hex[:8]}"
        stack_name = f"livetest-stack-{uuid.uuid4().hex[:8]}"
        input_log_before = 0
        food_log_before = 0
        now_iso = datetime.now(timezone.utc).isoformat()

        with self.step("provision prereq food_item"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/food-items",
                json={"name": food_name, "calories": 80, "carbs_g": 20.0},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"food_item prereq failed: {resp.status_code} {resp.text}"
            )
            food_id = resp.json()["id"]

        with self.step("provision prereq meal"):
            assert food_id is not None, "food_id not set"
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
                f"meal prereq failed: {resp.status_code} {resp.text}"
            )
            meal_id = resp.json()["id"]

        with self.step("provision prereq health_input"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/health-inputs",
                json={
                    "name": input_name,
                    "input_type": "supplement",
                    "default_dosage": "100",
                    "default_unit": "mg",
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"health_input prereq failed: {resp.status_code} {resp.text}"
            )
            input_id = resp.json()["id"]

        with self.step("provision prereq stack"):
            assert input_id is not None, "input_id not set"
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/stacks",
                json={
                    "name": stack_name,
                    "inputs": [
                        {"input_id": input_id, "dosage_override": "200"},
                    ],
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"stack prereq failed: {resp.status_code} {resp.text}"
            )
            stack_id = resp.json()["id"]

        with self.step("count log rows before"):
            input_log_before = count_rows(
                cur,
                "health_input_log",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )
            food_log_before = count_rows(
                cur,
                "health_food_logv2",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )

        with self.step("POST /api/v1/log-stack"):
            assert stack_id is not None, "stack_id not set"
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/log-stack",
                json={"stack_id": stack_id, "timestamp": now_iso},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"expected 201, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert body.get("inputs_logged") == 1, (
                f"expected inputs_logged=1, got {body}"
            )

        with self.step("POST /api/v1/log-meal"):
            assert meal_id is not None, "meal_id not set"
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/log-meal",
                json={"meal_id": meal_id, "timestamp": now_iso},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"expected 201, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert body.get("items_logged") == 1, (
                f"expected items_logged=1, got {body}"
            )

        with self.step("verify log rows in DB (delta check)"):
            input_log_after = count_rows(
                cur,
                "health_input_log",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )
            food_log_after = count_rows(
                cur,
                "health_food_logv2",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )
            assert input_log_after == input_log_before + 1, (
                f"health_input_log delta "
                f"{input_log_after - input_log_before}, expected 1 "
                f"(before={input_log_before}, after={input_log_after})"
            )
            assert food_log_after == food_log_before + 1, (
                f"health_food_logv2 delta "
                f"{food_log_after - food_log_before}, expected 1 "
                f"(before={food_log_before}, after={food_log_after})"
            )

        # Cleanup: delete parents only. Log rows stay (append-only).
        with self.step("cleanup: DELETE stack"):
            assert stack_id is not None, "stack_id not set"
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/stacks/{stack_id}",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"stack cleanup failed: {resp.status_code} {resp.text}"
            )

        with self.step("cleanup: DELETE health_input"):
            assert input_id is not None, "input_id not set"
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/health-inputs/{input_id}",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"health_input cleanup failed: {resp.status_code} {resp.text}"
            )

        with self.step("cleanup: DELETE meal"):
            assert meal_id is not None, "meal_id not set"
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/meals/{meal_id}",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"meal cleanup failed: {resp.status_code} {resp.text}"
            )

        with self.step("cleanup: DELETE food_item"):
            assert food_id is not None, "food_id not set"
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
        flow = LogStackMealFlow(cfg, session, conn, user_id)
        result = flow.run()
    finally:
        conn.close()

    print_live(result)
    report_path = write_markdown([result], cfg)
    print(f"\nReport: {report_path}")
    sys.exit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()

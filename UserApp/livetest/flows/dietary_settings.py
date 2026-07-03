"""Live test: /api/v1/dietary-settings singleton-with-history.

Dietary settings are a singleton per tenant — at most one row has
is_active=true at a time. PUT deactivates the current active row and
inserts a new one, preserving history. POST is only valid when no active
setting exists (returns 409 otherwise).

Strategy:
  1. GET to determine whether an active setting exists.
  2. If absent, POST. If present, PUT. Either way a new row lands.
  3. Delta check confirms +1 row (history preservation).
  4. GET again and verify the active notes carry the livetest marker.

The `notes` column carries the `livetest-` prefix for cleanup targeting
since there is no name column on this table.

No DELETE step — there is no delete endpoint for dietary_settings, and
the cleanup subcommand handles accumulated livetest rows.
"""
from __future__ import annotations

import sys
import uuid

from livetest.auth import login
from livetest.config import load_config
from livetest.pg import count_rows, open_rls_connection
from livetest.report import print_live, write_markdown
from livetest.runner import Flow, FlowResult


class DietarySettingsFlow(Flow):
    name = "dietary_settings"

    def run(self) -> FlowResult:
        cur = self.conn.cursor()
        marker = f"livetest-{uuid.uuid4().hex[:8]}"
        before = 0
        had_active = False

        with self.step("count rows before"):
            before = count_rows(
                cur,
                "dietary_settings",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )

        with self.step("GET /api/v1/dietary-settings baseline"):
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/dietary-settings",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            had_active = body is not None

        payload = {
            "diet_type": "omnivore",
            "dietary_restrictions": [],
            "calorie_target": 2000,
            "protein_target_g": 150,
            "carb_target_g": 200,
            "fat_target_g": 70,
            "meal_count_per_day": 3,
            "notes": marker,
        }

        if had_active:
            with self.step("PUT /api/v1/dietary-settings (had active)"):
                resp = self.session.put(
                    f"{self.cfg.base_url}/api/v1/dietary-settings",
                    json=payload,
                    timeout=self.cfg.timeout,
                )
                assert resp.status_code == 200, (
                    f"expected 200, got {resp.status_code}: {resp.text}"
                )
                body = resp.json()
                assert "id" in body, f"PUT response missing 'id': {body}"
        else:
            with self.step("POST /api/v1/dietary-settings (no active)"):
                resp = self.session.post(
                    f"{self.cfg.base_url}/api/v1/dietary-settings",
                    json=payload,
                    timeout=self.cfg.timeout,
                )
                assert resp.status_code == 201, (
                    f"expected 201, got {resp.status_code}: {resp.text}"
                )
                body = resp.json()
                assert "id" in body, f"POST response missing 'id': {body}"

        with self.step("verify delta +1 (history preservation)"):
            after = count_rows(
                cur,
                "dietary_settings",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )
            assert after == before + 1, (
                f"delta {after - before}, expected 1 "
                f"(before={before}, after={after})"
            )

        with self.step("GET /api/v1/dietary-settings returns livetest marker"):
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/dietary-settings",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert body is not None, "active dietary settings unexpectedly null"
            assert body.get("notes") == marker, (
                f"notes mismatch: got {body.get('notes')!r}, "
                f"expected {marker!r}"
            )

        with self.step("GET /api/v1/dietary-settings?history=true"):
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/dietary-settings",
                params={"history": "true"},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            history = resp.json()
            assert isinstance(history, list), (
                f"history response not a list: {type(history).__name__}"
            )
            assert any(h.get("notes") == marker for h in history), (
                f"history missing livetest marker {marker!r} "
                f"({len(history)} rows returned)"
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
        flow = DietarySettingsFlow(cfg, session, conn, user_id)
        result = flow.run()
    finally:
        conn.close()

    print_live(result)
    report_path = write_markdown([result], cfg)
    print(f"\nReport: {report_path}")
    sys.exit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()

"""Live test: /api/v1/allergies CRUD (health_allergies table)."""
from __future__ import annotations

import sys
import uuid

from livetest.auth import login
from livetest.config import load_config
from livetest.pagination_assertions import assert_pagination_envelope
from livetest.pg import count_rows, open_rls_connection
from livetest.report import print_live, write_markdown
from livetest.runner import Flow, FlowResult


class AllergiesFlow(Flow):
    name = "allergies"

    def run(self) -> FlowResult:
        cur = self.conn.cursor()
        allergy_id: str | None = None
        allergen = f"livetest-allergen-{uuid.uuid4().hex[:8]}"
        before = 0

        with self.step("count rows before"):
            before = count_rows(
                cur,
                "health_allergies",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )

        with self.step("POST /api/v1/allergies"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/allergies",
                json={
                    "allergen": allergen,
                    "allergy_type": "food",
                    "reaction": "hives",
                    "severity": "mild",
                    "status": "active",
                    "notes": "created by livetest harness",
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"expected 201, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert "id" in body, f"POST response missing 'id': {body}"
            allergy_id = body["id"]

        with self.step("verify row exists in DB (delta check)"):
            after = count_rows(
                cur,
                "health_allergies",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )
            assert after == before + 1, (
                f"delta {after - before}, expected 1 "
                f"(before={before}, after={after})"
            )

        with self.step("GET /api/v1/allergies includes created allergy"):
            assert allergy_id is not None, "allergy_id not set"
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/allergies",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            items = assert_pagination_envelope(resp.json(), "entries")
            assert any(a.get("id") == allergy_id for a in items), (
                f"allergy {allergy_id} not in GET response "
                f"({len(items)} items returned)"
            )

        with self.step("PUT /api/v1/allergies/{id}"):
            assert allergy_id is not None, "allergy_id not set"
            resp = self.session.put(
                f"{self.cfg.base_url}/api/v1/allergies/{allergy_id}",
                json={
                    "allergen": allergen,
                    "allergy_type": "food",
                    "reaction": "hives, swelling",
                    "severity": "moderate",
                    "status": "active",
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )

        with self.step("DELETE /api/v1/allergies/{id}"):
            assert allergy_id is not None, "allergy_id not set"
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/allergies/{allergy_id}",
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
        flow = AllergiesFlow(cfg, session, conn, user_id)
        result = flow.run()
    finally:
        conn.close()

    print_live(result)
    report_path = write_markdown([result], cfg)
    print(f"\nReport: {report_path}")
    sys.exit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()

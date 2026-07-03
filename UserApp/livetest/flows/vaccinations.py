"""Live test: /api/v1/vaccinations CRUD (health_vaccinations table).

Smoke coverage for the vaccinations endpoint, which was paginated in the
Tier 0 rollout but had no existing livetest flow. Pattern mirrors
conditions.py — POST a livetest-prefixed vaccine_name, delta-check the
DB, GET via the paginated envelope to confirm the row is visible, PUT
to verify update, DELETE to clean up.

The marker column is vaccine_name; cleanup.py can target rows via
`vaccine_name LIKE 'livetest-%'` if a run aborts before the DELETE step.
"""
from __future__ import annotations

import sys
import uuid
from datetime import date

from livetest.auth import login
from livetest.config import load_config
from livetest.pagination_assertions import assert_pagination_envelope
from livetest.pg import count_rows, open_rls_connection
from livetest.report import print_live, write_markdown
from livetest.runner import Flow, FlowResult


class VaccinationsFlow(Flow):
    name = "vaccinations"

    def run(self) -> FlowResult:
        cur = self.conn.cursor()
        vaccination_id: str | None = None
        vaccine_name = f"livetest-vaccine-{uuid.uuid4().hex[:8]}"
        before = 0

        with self.step("count rows before"):
            before = count_rows(
                cur,
                "health_vaccinations",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )

        with self.step("POST /api/v1/vaccinations"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/vaccinations",
                json={
                    "vaccine_name": vaccine_name,
                    "administered_date": date.today().isoformat(),
                    "lot_number": "livetest-lot",
                    "site": "left deltoid",
                    "administered_by": "livetest-clinician",
                    "location": "livetest-clinic",
                    "reaction_notes": "no reaction",
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"expected 201, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert "id" in body, f"POST response missing 'id': {body}"
            vaccination_id = body["id"]

        with self.step("verify row exists in DB (delta check)"):
            after = count_rows(
                cur,
                "health_vaccinations",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )
            assert after == before + 1, (
                f"delta {after - before}, expected 1 "
                f"(before={before}, after={after})"
            )

        with self.step("GET /api/v1/vaccinations includes created vaccination"):
            assert vaccination_id is not None, "vaccination_id not set"
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/vaccinations",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            items = assert_pagination_envelope(resp.json(), "entries")
            assert any(v.get("id") == vaccination_id for v in items), (
                f"vaccination {vaccination_id} not in GET response "
                f"({len(items)} items returned)"
            )

        with self.step("PUT /api/v1/vaccinations/{id}"):
            assert vaccination_id is not None, "vaccination_id not set"
            resp = self.session.put(
                f"{self.cfg.base_url}/api/v1/vaccinations/{vaccination_id}",
                json={
                    "vaccine_name": vaccine_name,
                    "administered_date": date.today().isoformat(),
                    "lot_number": "livetest-lot-updated",
                    "site": "right deltoid",
                    "administered_by": "livetest-clinician",
                    "location": "livetest-clinic",
                    "reaction_notes": "mild soreness",
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )

        with self.step("DELETE /api/v1/vaccinations/{id}"):
            assert vaccination_id is not None, "vaccination_id not set"
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/vaccinations/{vaccination_id}",
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
        flow = VaccinationsFlow(cfg, session, conn, user_id)
        result = flow.run()
    finally:
        conn.close()

    print_live(result)
    report_path = write_markdown([result], cfg)
    print(f"\nReport: {report_path}")
    sys.exit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()

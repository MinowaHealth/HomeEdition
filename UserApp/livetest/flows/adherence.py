"""Live test: GET /api/v1/adherence (PLAN-002 closes at tool level).

Provisions three inputs — one scheduled medication (doses_per_day=2),
one PRN supplement (doses_per_day=-1), and one unspecified input
(doses_per_day=NULL) — logs the scheduled one once over a 1-day window,
and asserts:
  * scheduled input appears in `inputs` with scheduled_doses=2, logged=1
  * PRN input appears in `excluded_prn`
  * unspecified input appears in `excluded_unspecified`
  * bogus input_ids param is rejected with 400
  * out-of-range window (>90 days) is rejected with 400

Depends on /api/v1/health-inputs PUT support to set doses_per_day after
creation (the POST schema may not accept it directly).
"""
from __future__ import annotations

import sys
import uuid
from datetime import date, datetime, timedelta, timezone

from livetest.auth import login
from livetest.config import load_config
from livetest.pg import open_rls_connection
from livetest.report import print_live, write_markdown
from livetest.runner import Flow, FlowResult


class AdherenceFlow(Flow):
    name = "adherence"

    def _create_input(self, name: str, itype: str, doses_per_day: int | None) -> str:
        """Create a health_input and (if needed) set doses_per_day via PUT.

        The POST endpoint may or may not accept doses_per_day depending on
        schema; this helper hides that detail so the test body stays clean.
        """
        resp = self.session.post(
            f"{self.cfg.base_url}/api/v1/health-inputs",
            json={
                "name": name,
                "input_type": itype,
                "default_dosage": "1",
                # 'dose' is not a canonical unit (reserved HealthKit magic
                # value) — tablet is the closest real dose form.
                "default_unit": "tablet",
                "doses_per_day": doses_per_day,
            },
            timeout=self.cfg.timeout,
        )
        assert resp.status_code == 201, (
            f"POST /health-inputs failed: {resp.status_code} {resp.text}"
        )
        input_id = resp.json()["id"]
        # If the POST didn't persist doses_per_day, fall back to a PUT so
        # the adherence report has the value it needs.
        if resp.json().get("doses_per_day") != doses_per_day:
            put = self.session.put(
                f"{self.cfg.base_url}/api/v1/health-inputs/{input_id}",
                json={"doses_per_day": doses_per_day},
                timeout=self.cfg.timeout,
            )
            assert put.status_code in (200, 204), (
                f"PUT doses_per_day failed: {put.status_code} {put.text}"
            )
        return input_id

    def run(self) -> FlowResult:
        scheduled_name = f"livetest-sched-{uuid.uuid4().hex[:8]}"
        prn_name = f"livetest-prn-{uuid.uuid4().hex[:8]}"
        unspec_name = f"livetest-unspec-{uuid.uuid4().hex[:8]}"
        scheduled_id = prn_id = unspec_id = None

        # Use a 3-day window ending today so the logged dose lands inside it
        today = date.today()
        start = (today - timedelta(days=2)).isoformat()
        end = today.isoformat()

        with self.step("provision scheduled medication (doses_per_day=2)"):
            scheduled_id = self._create_input(scheduled_name, "medication", 2)

        with self.step("provision PRN supplement (doses_per_day=-1)"):
            prn_id = self._create_input(prn_name, "supplement", -1)

        with self.step("provision unspecified input (doses_per_day=NULL)"):
            unspec_id = self._create_input(unspec_name, "supplement", None)

        with self.step("log one dose of the scheduled medication"):
            now_iso = datetime.now(timezone.utc).isoformat()
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/log-health-input",
                json={
                    "input_id": scheduled_id,
                    "dosage": "1",
                    "timestamp": now_iso,
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"log POST failed: {resp.status_code} {resp.text}"
            )

        with self.step("GET /adherence: scheduled input appears with counts"):
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/adherence",
                params={"start_date": start, "end_date": end},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            inputs = body.get("inputs") or []
            row = next((i for i in inputs if i.get("input_id") == scheduled_id), None)
            assert row is not None, (
                f"scheduled input {scheduled_id} not in adherence report "
                f"({len(inputs)} rows)"
            )
            # 3-day window × 2 doses/day = 6 scheduled
            assert row.get("scheduled_doses") == 6, (
                f"expected 6 scheduled doses, got {row.get('scheduled_doses')}"
            )
            assert row.get("logged_doses") == 1, (
                f"expected 1 logged dose, got {row.get('logged_doses')}"
            )
            assert row.get("pct_adherence") is not None, (
                "pct_adherence missing from row"
            )

        with self.step("PRN input surfaces under excluded_prn"):
            excluded_prn = body.get("excluded_prn") or []
            assert any(e.get("input_id") == prn_id for e in excluded_prn), (
                f"PRN input {prn_id} not in excluded_prn "
                f"(got {[e.get('input_id') for e in excluded_prn]})"
            )
            # And must NOT appear in the main inputs list
            assert not any(i.get("input_id") == prn_id for i in inputs), (
                "PRN input leaked into inputs list"
            )

        with self.step("unspecified input surfaces under excluded_unspecified"):
            excluded_unspec = body.get("excluded_unspecified") or []
            assert any(e.get("input_id") == unspec_id for e in excluded_unspec), (
                f"unspecified input {unspec_id} not in excluded_unspecified"
            )
            assert not any(i.get("input_id") == unspec_id for i in inputs), (
                "unspecified input leaked into inputs list"
            )

        with self.step("bogus input_ids is rejected"):
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/adherence",
                params={"start_date": start, "end_date": end, "input_ids": "not-a-uuid"},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 400, (
                f"expected 400 for bogus input_ids, got "
                f"{resp.status_code}: {resp.text}"
            )

        with self.step("window > 90 days is rejected"):
            wide_start = (today - timedelta(days=365)).isoformat()
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/adherence",
                params={"start_date": wide_start, "end_date": end},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 400, (
                f"expected 400 for >90-day window, got "
                f"{resp.status_code}: {resp.text}"
            )

        with self.step("cleanup: DELETE all three health_inputs"):
            for iid in (scheduled_id, prn_id, unspec_id):
                assert iid is not None
                resp = self.session.delete(
                    f"{self.cfg.base_url}/api/v1/health-inputs/{iid}",
                    timeout=self.cfg.timeout,
                )
                assert resp.status_code == 200, (
                    f"cleanup failed for {iid}: {resp.status_code} {resp.text}"
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
        flow = AdherenceFlow(cfg, session, conn, user_id)
        result = flow.run()
    finally:
        conn.close()

    print_live(result)
    report_path = write_markdown([result], cfg)
    print(f"\nReport: {report_path}")
    sys.exit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()

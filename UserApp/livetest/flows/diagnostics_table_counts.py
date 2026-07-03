"""Live test: /api/v1/diagnostics/table-counts read-only sanity check.

Unlike the other Phase B flows, this one writes nothing. It exists to
verify that:
  1. The diagnostics endpoint is reachable via the authenticated session
  2. It returns a well-shaped JSON body (tables list, total_tables count)
  3. Known Phase A + Phase B tables are present in the response
  4. Counts are non-negative integers (proof the savepoint-per-table
     error-isolation path in the handler is working — a broken savepoint
     surfaces as count=-1 with an error string)

Cross-reference: the handler at
UserApp/webapp/routes/analytics.py::get_table_counts wraps each COUNT in
a savepoint so one failing table does not poison the whole TX. This flow
catches regressions in that guarantee by asserting all counts are >= 0.
"""
from __future__ import annotations

import sys

from livetest.auth import login
from livetest.config import load_config
from livetest.pg import open_rls_connection
from livetest.report import print_live, write_markdown
from livetest.runner import Flow, FlowResult

# Minimum set of tables the harness expects to exist. Keep this short so
# an alpha schema rename doesn't immediately break the flow — just the
# load-bearing stuff from Phases A and B.
EXPECTED_TABLES = {
    "users",
    "sessions",
    "health_food_itemsv2",
    "health_inputs",
    "meals",
    "stacks",
    "timeframes",
    "health_blood_pressure_readings",
    "health_metrics",
    "health_observations",
    "health_conditions",
    "health_allergies",
    "health_blood_work",
    "health_family_history",
    "reminders",
    "dietary_settings",
}


class DiagnosticsTableCountsFlow(Flow):
    name = "diagnostics_table_counts"

    def run(self) -> FlowResult:
        with self.step("GET /api/v1/diagnostics/table-counts"):
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/diagnostics/table-counts",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()

        with self.step("response has tables list + total_tables"):
            assert "tables" in body, (
                f"response missing 'tables' key: {body}"
            )
            assert "total_tables" in body, (
                f"response missing 'total_tables' key: {body}"
            )
            tables = body["tables"]
            assert isinstance(tables, list), (
                f"tables not a list: {type(tables).__name__}"
            )
            assert body["total_tables"] == len(tables), (
                f"total_tables {body['total_tables']} != "
                f"len(tables) {len(tables)}"
            )

        with self.step("expected tables present in response"):
            found_names = {t.get("table") for t in body["tables"]}
            missing = EXPECTED_TABLES - found_names
            assert not missing, (
                f"expected tables missing from diagnostics response: "
                f"{sorted(missing)}"
            )

        with self.step("all counts are non-negative integers"):
            errors = [
                t for t in body["tables"]
                if not isinstance(t.get("count"), int) or t["count"] < 0
            ]
            assert not errors, (
                f"{len(errors)} tables returned bad counts — first 3: "
                f"{errors[:3]}"
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
        flow = DiagnosticsTableCountsFlow(cfg, session, conn, user_id)
        result = flow.run()
    finally:
        conn.close()

    print_live(result)
    report_path = write_markdown([result], cfg)
    print(f"\nReport: {report_path}")
    sys.exit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()

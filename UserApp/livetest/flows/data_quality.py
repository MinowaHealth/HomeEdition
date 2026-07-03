"""Live test: data-quality assertions against the test account (PLAN-003).

Historically, smoke tests ran as the maintainer's account and left junk rows
("Curl Test Vitamin", "v2 Test Vitamin", "Curl Test Stack") in
`health_inputs` and `stacks`, which then surfaced in MCP responses as
if they were real medications. PLAN-003 moved smoke tests to the test
account; this flow is the regression guard.

The flow reads `health_inputs.name` and `stacks.name` for the currently
authenticated user (RLS filters to just that row set) and fails if any
row title matches the forbidden pattern. Running this in the Phase-1
live-test suite catches regressions the moment a future test script
writes to the wrong account.
"""
from __future__ import annotations

import re
import sys

# livetest/__init__.py puts UserApp/webapp on sys.path so db_driver is importable.
from db_driver import sql

from livetest.auth import login
from livetest.config import load_config
from livetest.pg import open_rls_connection
from livetest.report import print_live, write_markdown
from livetest.runner import Flow, FlowResult


# Case-insensitive substrings. "v2 test" used to land as literal titles;
# "curl" and "test vitamin" are the other two patterns PLAN-003 called out.
# The pattern is deliberately broad — false positives from a legitimate
# product named something like "CURL Immunity" are acceptable for an alpha
# harness; the developer can then rename either the product or this guard.
_FORBIDDEN_PATTERN = re.compile(r"curl|v2 test|test vitamin", re.IGNORECASE)


class DataQualityFlow(Flow):
    name = "data_quality"

    def _scan_table(self, table: str, column: str = "name") -> list[str]:
        # Access by column name (not row[0]) — psycopg3's dict_row factory
        # returns dict-style rows and does NOT support integer indexing.
        query = sql.SQL(
            "SELECT {col} FROM {tbl} WHERE {col} IS NOT NULL"
        ).format(col=sql.Identifier(column), tbl=sql.Identifier(table))
        with self.conn.cursor() as cur:
            cur.execute(query)
            return [row[column] for row in cur.fetchall()]

    def run(self) -> FlowResult:
        with self.step("health_inputs has no test-artifact titles"):
            titles = self._scan_table("health_inputs", "name")
            offenders = [t for t in titles if _FORBIDDEN_PATTERN.search(t)]
            assert not offenders, (
                f"health_inputs contains {len(offenders)} test-artifact "
                f"row(s) — first 5: {offenders[:5]}. PLAN-003 requires "
                f"smoke tests to write to the test account only."
            )

        with self.step("stacks has no test-artifact titles"):
            titles = self._scan_table("stacks", "name")
            offenders = [t for t in titles if _FORBIDDEN_PATTERN.search(t)]
            assert not offenders, (
                f"stacks contains {len(offenders)} test-artifact row(s) — "
                f"first 5: {offenders[:5]}. PLAN-003."
            )

        with self.step("health_food_itemsv2 has no test-artifact titles"):
            # Food-item naming is more permissive so this is a lower-stakes
            # check, but catches drive-by test writes that slipped past.
            titles = self._scan_table("health_food_itemsv2", "name")
            offenders = [t for t in titles if _FORBIDDEN_PATTERN.search(t)]
            assert not offenders, (
                f"health_food_itemsv2 contains {len(offenders)} test-"
                f"artifact row(s) — first 5: {offenders[:5]}."
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
        flow = DataQualityFlow(cfg, session, conn, user_id)
        result = flow.run()
    finally:
        conn.close()

    print_live(result)
    report_path = write_markdown([result], cfg)
    print(f"\nReport: {report_path}")
    sys.exit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()

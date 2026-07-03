"""Stage 6 read-only verification — runs the spec's Stage 6 assertions and
produces a PASS/FAIL report.

Adds row-count guards on embedding columns: a column with 0 rows passes
the NULL-count check vacuously, so we annotate "no rows" in the detail
message to distinguish "fully embedded 10 rows" from "no rows existed".
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from .cohort_gate import check_cohort, CohortGateError, EXPECTED_USERS
from .embeddings import EMBEDDING_COLUMNS

# Floor proving the activity loop generated metric volume for the 6-user
# household at SCALE=1. Volume testing dials this far higher via SCALE.
HEALTH_METRICS_THRESHOLD = 25

@dataclass
class Assertion:
    name: str
    passed: bool
    detail: str

@dataclass
class VerifyReport:
    assertions: dict[str, Assertion] = field(default_factory=dict)

    def add(self, a: Assertion) -> None:
        self.assertions[a.name] = a

    def all_pass(self) -> bool:
        return all(a.passed for a in self.assertions.values())

    def render(self) -> str:
        lines = ["== SEED VERIFICATION =="]
        for name, a in self.assertions.items():
            tag = "[PASS]" if a.passed else "[FAIL]"
            lines.append(f"{tag} {name:40} {a.detail}")
        lines.append("")
        lines.append(
            "== ALL ASSERTIONS PASSED ==" if self.all_pass()
            else "== ASSERTIONS FAILED =="
        )
        return "\n".join(lines)

def run_verification(conn: Any) -> VerifyReport:
    report = VerifyReport()

    # Cohort gate.
    try:
        check_cohort(conn)
        report.add(Assertion(
            "Cohort gate", True,
            f"{EXPECTED_USERS} household users",
        ))
    except CohortGateError as e:
        report.add(Assertion("Cohort gate", False, str(e)))
        return report  # short-circuit; other assertions meaningless

    # health_metrics threshold.
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM health_metrics WHERE tenant_id=1;")
        n = cur.fetchone()[0]
    report.add(Assertion(
        "Row count: health_metrics",
        n > HEALTH_METRICS_THRESHOLD,
        f"{n:,} rows (threshold {HEALTH_METRICS_THRESHOLD:,})",
    ))

    # Embedding completeness — with row-count guard to distinguish
    # "all embedded" from "no rows".
    for table, pk_col, text_col, vec_col in EMBEDDING_COLUMNS:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            row_count = cur.fetchone()[0]
            cur.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {vec_col} IS NULL"
            )
            null_count = cur.fetchone()[0]
        if row_count == 0:
            detail = "0 rows (no rows; vacuous pass — seeder does not populate)"
        else:
            detail = f"{null_count} NULLs / {row_count} rows"
        report.add(Assertion(
            f"Embeddings: {table}.{vec_col}",
            null_count == 0,
            detail,
        ))
    return report

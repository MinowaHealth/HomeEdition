"""Live test integration runner — iterates over FLOW_ORDER, writes one report."""
from __future__ import annotations

import importlib
import sys

from livetest.auth import login
from livetest.config import load_config
from livetest.pg import open_rls_connection
from livetest.report import print_live, write_markdown
from livetest.runner import Flow, FlowResult


# Pass 2 Phase A — CRUD basics plus logging. Order rationale: leaf
# resources first (food_items, health_inputs, timeframes), then
# resources that depend on leaves (meals references food_items, stacks
# references health_inputs), then non-CRUD logging flows. Each flow is
# still self-contained — the order is for human readability of the
# report, not for cross-flow state dependencies.
#
# Pass 3 Phase B — frontend API surface beyond the meds/food stack:
# vitals (three tables' worth of readings), observations (free-text
# notes), clinical history (conditions/allergies/blood_work/family),
# reminders, dietary_settings, and a diagnostics read-only check.
# Grouped by subject-matter so the markdown report reads in sections.
FLOW_ORDER: list[str] = [
    # Phase A
    "food_items",
    "health_inputs",
    "timeframes",
    "meals",
    "stacks",
    # PilotFeedback.md § B5 — partial unique index on
    # (tenant_id, user_id, lower(name)) WHERE is_active=true.
    "unique_names",
    # PilotFeedback.md § B4 — food_log → health_metrics
    # nutrition projection + /nutrition/today aggregator.
    "nutrition_today",
    "log_stack_meal",
    "all_logs",
    "log_promotions",
    # Phase 1 MCP redesign — /health-input-log filter + /adherence endpoint
    "input_type_filter",
    "adherence",
    # Phase B — vitals
    "vitals_bp",
    "vitals_weight",
    "vitals_temp",
    # Phase B — observations (free-text)
    "observations",
    # Phase B — clinical history
    "conditions",
    "allergies",
    "blood_work",
    "family_history",
    "vaccinations",
    # Phase B — reminders + settings
    "reminders",
    "dietary_settings",
    # Phase B — feedback (admin-triaged alpha feedback)
    "feedback",
    # Phase C — UserDocs end-to-end (upload → OCR → annotation → download → soft-delete)
    "documents",
    # Phase B — diagnostics (read-only sanity check)
    "diagnostics_table_counts",
    # Phase 3 MCP redesign — PLAN-003 regression guard against test-artifact titles
    "data_quality",
    # Track 4a (SecurityHardening.md) — auth-defense flows. Each flow exercises
    # one finding from SecurityHardening.md against the running app. Steps for
    # not-yet-fixed defects are wrapped in xfail markers so the run stays green;
    # the markers flip to xpass (= fail) when the underlying fix lands, forcing
    # removal in the same PR.
    "security_csrf",            # F1 — forged-origin POST should be rejected
]


def _load_flow_class(flow_name: str) -> type[Flow]:
    module = importlib.import_module(f"livetest.flows.{flow_name}")
    for value in vars(module).values():
        if (
            isinstance(value, type)
            and issubclass(value, Flow)
            and value is not Flow
        ):
            return value
    raise RuntimeError(f"no Flow subclass found in livetest.flows.{flow_name}")


def main() -> None:
    cfg = load_config(sys.argv[1:])
    session = login(cfg)
    resp = session.get(
        f"{cfg.base_url}/api/v1/session", timeout=cfg.timeout
    )
    resp.raise_for_status()
    user_id = resp.json()["user_id"]

    conn = open_rls_connection(cfg, user_id)
    results: list[FlowResult] = []
    try:
        for flow_name in FLOW_ORDER:
            try:
                FlowClass = _load_flow_class(flow_name)
                flow = FlowClass(cfg, session, conn, user_id)
                result = flow.run()
            except Exception as e:  # noqa: BLE001
                result = FlowResult.from_exception(flow_name, e)
            print_live(result)
            results.append(result)
    finally:
        conn.close()

    report_path = write_markdown(results, cfg)
    failed = sum(1 for r in results if r.status == "fail")
    total = len(results)
    print(
        f"\n{total - failed}/{total} flows passed. Report: {report_path}"
    )
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()

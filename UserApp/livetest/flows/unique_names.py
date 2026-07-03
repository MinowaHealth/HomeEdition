"""Live test: unique-name constraint on health_inputs and stacks.

Plan: PilotFeedback.md § B5.
Delta: Infrastructure/deltas/2026-04-30-stacks_inputs_unique_names.sql

Exercises the partial unique indexes
``ux_health_inputs_active_name`` and ``ux_stacks_active_name`` end-to-end
through the Flask routes, asserting:

  - Duplicate active POST → 409 with code DUPLICATE_NAME
  - Case-insensitive collision → 409
  - PUT-rename into existing name → 409
  - Archive (is_active=false) then POST same name → 201 (rename-then-recreate)
  - Same matrix for stacks

Cleanup is best-effort: the flow archives all rows it created so a re-run
on the same login session doesn't trip its own constraint.
"""
from __future__ import annotations

import sys
import uuid

from livetest.auth import login
from livetest.config import load_config
from livetest.pg import open_rls_connection
from livetest.report import print_live, write_markdown
from livetest.runner import Flow, FlowResult


class UniqueNamesFlow(Flow):
    name = "unique_names"

    def run(self) -> FlowResult:
        suffix = uuid.uuid4().hex[:8]
        hi_name = f"livetest-uniq-med-{suffix}"
        stack_name = f"livetest-uniq-stack-{suffix}"

        hi_id_a: str | None = None
        hi_id_b: str | None = None
        hi_id_c: str | None = None
        stack_id_a: str | None = None
        stack_id_b: str | None = None

        # ------------------------------------------------------------------
        # health_inputs duplicate-rejection matrix
        # ------------------------------------------------------------------
        with self.step("POST /health-inputs A → 201"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/health-inputs",
                json={"name": hi_name, "input_type": "medication"},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"first POST should succeed; got {resp.status_code}: {resp.text}"
            )
            hi_id_a = resp.json()["id"]

        with self.step("POST /health-inputs same name → 409 DUPLICATE_NAME"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/health-inputs",
                json={"name": hi_name, "input_type": "medication"},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 409, (
                f"duplicate POST should be 409; got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert body.get("code") == "DUPLICATE_NAME", (
                f"expected code=DUPLICATE_NAME, got {body!r}"
            )

        with self.step("POST /health-inputs case-variant → 409"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/health-inputs",
                json={"name": hi_name.upper(), "input_type": "medication"},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 409, (
                f"case-variant should collide; got {resp.status_code}: {resp.text}"
            )

        with self.step("POST /health-inputs different name B → 201"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/health-inputs",
                json={"name": hi_name + "-B", "input_type": "medication"},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"distinct-name POST should succeed; got {resp.status_code}: {resp.text}"
            )
            hi_id_b = resp.json()["id"]

        with self.step("PUT /health-inputs/B name → A → 409"):
            assert hi_id_b is not None
            resp = self.session.put(
                f"{self.cfg.base_url}/api/v1/health-inputs/{hi_id_b}",
                json={"name": hi_name},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 409, (
                f"rename-into-collision should be 409; got {resp.status_code}: {resp.text}"
            )

        with self.step("PUT /health-inputs/A is_active=false (archive)"):
            assert hi_id_a is not None
            resp = self.session.put(
                f"{self.cfg.base_url}/api/v1/health-inputs/{hi_id_a}",
                json={"is_active": False},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"archive PUT should succeed; got {resp.status_code}: {resp.text}"
            )

        with self.step("POST /health-inputs same name after archive → 201"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/health-inputs",
                json={"name": hi_name, "input_type": "medication"},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                "after archiving the original, same name should be allowed; "
                f"got {resp.status_code}: {resp.text}"
            )
            hi_id_c = resp.json()["id"]

        # ------------------------------------------------------------------
        # stacks duplicate-rejection matrix
        # ------------------------------------------------------------------
        with self.step("POST /stacks A → 201"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/stacks",
                json={"name": stack_name},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"first stack POST should succeed; got {resp.status_code}: {resp.text}"
            )
            stack_id_a = resp.json()["id"]

        with self.step("POST /stacks same name → 409 DUPLICATE_NAME"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/stacks",
                json={"name": stack_name},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 409, (
                f"duplicate stack POST should be 409; got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert body.get("code") == "DUPLICATE_NAME", (
                f"expected code=DUPLICATE_NAME, got {body!r}"
            )

        with self.step("PUT /stacks/A is_active=false then POST same name → 201"):
            assert stack_id_a is not None
            resp = self.session.put(
                f"{self.cfg.base_url}/api/v1/stacks/{stack_id_a}",
                json={"name": stack_name, "is_active": False},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"archive PUT should succeed; got {resp.status_code}: {resp.text}"
            )
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/stacks",
                json={"name": stack_name},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                "after archive, same stack name should be allowed; "
                f"got {resp.status_code}: {resp.text}"
            )
            stack_id_b = resp.json()["id"]

        # ------------------------------------------------------------------
        # Cleanup: archive everything we left active so a rerun is clean.
        # DELETE would also work but archiving better mirrors how the
        # production rename workflow looks.
        # ------------------------------------------------------------------
        with self.step("cleanup — archive all created rows"):
            for hi_id in (hi_id_b, hi_id_c):
                if hi_id:
                    self.session.put(
                        f"{self.cfg.base_url}/api/v1/health-inputs/{hi_id}",
                        json={"is_active": False},
                        timeout=self.cfg.timeout,
                    )
            if stack_id_b:
                self.session.put(
                    f"{self.cfg.base_url}/api/v1/stacks/{stack_id_b}",
                    json={"name": stack_name, "is_active": False},
                    timeout=self.cfg.timeout,
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
        flow = UniqueNamesFlow(cfg, session, conn, user_id)
        result = flow.run()
    finally:
        conn.close()

    print_live(result)
    report_path = write_markdown([result], cfg)
    print(f"\nReport: {report_path}")
    sys.exit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()

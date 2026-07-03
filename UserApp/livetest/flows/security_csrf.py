"""Live test: CSRF rejection on session-auth mutating routes.

Track 4a (Phase 4a — inside test) of [SecurityHardening.md](../../../SecurityHardening.md).
This flow targets the F1 finding: session-auth POST/PUT/DELETE/PATCH
routes must reject requests that arrive with a forged ``Origin`` header.

The defense is **not yet in place** — CSRFProtect has not been registered
on any blueprint. So the rejection assertion is wrapped in
``self.step(..., xfail="F1: ...")``: today the route returns 200/201/204,
the assertion fails, and the runner records ``xfail`` (counts as pass).
When CSRFProtect rolls out and the route returns 403, the step succeeds
unexpectedly — the runner records ``xpass`` (counts as fail), forcing
the implementer to remove the xfail marker.

Two negative-control steps run unconditionally and pass today:
  * a *same-origin* POST (no Origin header) succeeds — proves the test
    infra isn't accidentally rejecting everything.
  * a request without a session cookie returns 401 — proves the route is
    auth-gated at all.
"""
from __future__ import annotations

import sys
import uuid

from livetest.auth import login
from livetest.config import load_config
from livetest.pg import open_rls_connection
from livetest.report import print_live, write_markdown
from livetest.runner import Flow, FlowResult


_FORGED_ORIGIN = "https://attacker.invalid"
_TARGET_PATH = "/api/v1/timeframes"  # session-auth POST that's cheap to round-trip


class SecurityCsrfFlow(Flow):
    name = "security_csrf"

    def run(self) -> FlowResult:
        def body_template() -> dict[str, object]:
            return {
                "name": f"livetest-csrf-{uuid.uuid4().hex[:8]}",
                "time_of_day": "08:00",
                "sort_order": 1,
                "is_active": True,
            }

        # Negative control 1 — un-authenticated request must fail.
        with self.step("baseline: un-authed POST returns 401"):
            from httpx import Client
            anon = Client(follow_redirects=True, timeout=self.cfg.timeout)
            resp = anon.post(
                f"{self.cfg.base_url}{_TARGET_PATH}",
                json=body_template(),
                timeout=self.cfg.timeout,
            )
            assert resp.status_code in (401, 403), (
                f"expected 401/403 for un-authed POST, got {resp.status_code}: "
                f"{resp.text[:200]}"
            )

        # Negative control 2 — same-origin authed POST must succeed.
        # Confirms the route works under normal conditions, so any later
        # 4xx is genuinely about CSRF, not about the test setup.
        created_ids: list[str] = []
        with self.step("baseline: same-origin authed POST returns 201"):
            resp = self.session.post(
                f"{self.cfg.base_url}{_TARGET_PATH}",
                json=body_template(),
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"expected 201 for same-origin authed POST, got "
                f"{resp.status_code}: {resp.text[:200]}"
            )
            created_ids.append(resp.json()["id"])

        # F1 ratchet — forged-origin POST must be rejected.
        # Currently fails (no CSRFProtect). xfail keeps CI green; xpass
        # will fire when the defense lands and force this marker removed.
        with self.step(
            "F1: forged-origin POST returns 403",
            xfail="F1 — CSRFProtect not yet registered (SecurityHardening.md Track 4a partner)",
        ):
            resp = self.session.post(
                f"{self.cfg.base_url}{_TARGET_PATH}",
                json=body_template(),
                headers={"Origin": _FORGED_ORIGIN},
                timeout=self.cfg.timeout,
            )
            # Track success so we can clean up if the request actually
            # created a row (which it will until F1 is fixed).
            if resp.status_code == 201:
                created_ids.append(resp.json()["id"])
            assert resp.status_code == 403, (
                f"expected 403 from forged-origin POST, got {resp.status_code}"
            )

        # Cleanup — delete every timeframe this flow created so the
        # database doesn't accrete livetest-csrf-* rows on every CI run.
        with self.step(f"cleanup: delete {len(created_ids)} timeframe(s)"):
            for tid in created_ids:
                self.session.delete(
                    f"{self.cfg.base_url}{_TARGET_PATH}/{tid}",
                    timeout=self.cfg.timeout,
                )

        return self.result()


if __name__ == "__main__":
    cfg = load_config(sys.argv[1:])
    session = login(cfg)
    resp = session.get(f"{cfg.base_url}/api/v1/session", timeout=cfg.timeout)
    resp.raise_for_status()
    user_id = resp.json()["user_id"]
    conn = open_rls_connection(cfg, user_id)
    try:
        result = SecurityCsrfFlow(cfg, session, conn, user_id).run()
        print_live(result)
        write_markdown([result], cfg)
        sys.exit(0 if result.status == "pass" else 1)
    finally:
        conn.close()

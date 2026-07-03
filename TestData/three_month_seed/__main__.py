"""Entrypoint: python -m TestData.three_month_seed [flags]

Single-household temporal seeder. Generates a configurable volume of health
activity for the six Borgia household users over a rolling window, posting
through the UserApp REST API (app-level user_id scoping; no RLS). Stages:

  Stage 0  accounts          seed_users.py (run separately, before this)
  Stage 1  scaffolding       clinical baseline from records/*.json (API)
  Stage 2  daily activity    per-day BP/weight/stack/meal/observation (API)
  Stage 5  embeddings        pgvector fill (direct DB)
  Stage 6  verify            read-only assertions (direct DB)

Volume is driven by SCALE (per-day event multiplier) and WINDOW_DAYS.
"""
from __future__ import annotations
import argparse
import logging
import os
import sys
import time
from pathlib import Path

import psycopg

from .config import SeedConfig, ConfigError
from .cohort_gate import check_cohort, CohortGateError
from .personas.registry import REGISTRY
from .timeline import iter_days, dispatch_persona_day
from .seed_rng import rng_for
from .sources.manual import ManualClient
from .embeddings import fill_embeddings
from .stage1 import run_stage1, Stage1Result
from .verify import run_verification

DEFAULT_PASSWORD = "Password2026"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m TestData.three_month_seed")
    p.add_argument("--reset", action="store_true",
                   help="DELETE seeder-owned tenant=1 rows, then re-seed")
    p.add_argument("--verify-only", action="store_true",
                   help="Skip stages 1-2/5; run stage 6 read-only assertions")
    p.add_argument("--no-embeddings", action="store_true",
                   help="Skip stage 5 (Ollama). Ad-hoc only.")
    p.add_argument("--no-stage1", action="store_true",
                   help="Skip Stage 1 scaffolding from records/*.json")
    p.add_argument("--persona", default=None,
                   help="Run only one persona (debug aid)")
    return p


def _connect(cfg: SeedConfig):
    """Open the admin connection and register the pgvector adapter so
    list[float] columns adapt to vector(768) on UPDATE."""
    conn = psycopg.connect(
        host=cfg.db_host, port=cfg.db_port, dbname=cfg.db_name,
        user=cfg.db_user, password=cfg.db_password,
    )
    # Register pgvector adapter — required by fill_embeddings (Stage 5).
    # Imported lazily so this module can be imported without pgvector installed
    # (e.g., in test environments where the adapter isn't needed).
    try:
        from pgvector.psycopg import register_vector
        register_vector(conn)
    except ImportError:
        # pgvector not available; Stage 5 will fail at UPDATE time if it runs.
        # Tests using MagicMock connections don't need the adapter.
        pass
    return conn


def _reset_tenant_one(conn) -> None:
    """DELETE seeder-owned rows for tenant=1. Cohort gate must already hold.

    Order matters — child tables before parent tables to respect FK
    constraints.
    """
    tables = [
        "health_observations",
        "health_food_logv2",
        "health_input_log",
        "health_blood_pressure_readings",
        "health_metrics",
        "health_vaccinations",
        "health_surgical_history",
        "health_social_history",
        "health_family_history",
        "health_allergies",
        "health_conditions",
        "stack_inputs", "stacks", "timeframes", "health_inputs",
    ]
    with conn.cursor() as cur:
        for t in tables:
            cur.execute(f"DELETE FROM {t} WHERE tenant_id=1")
    conn.commit()


def _run_seed(cfg: SeedConfig, args) -> int:
    log = logging.getLogger("seed")
    log.info("Pre-flight: db=%s api=%s scale=%s window_days=%s",
             cfg.db_host, cfg.api_base_url, cfg.scale, cfg.window_days)
    conn = _connect(cfg)
    try:
        try:
            check_cohort(conn)
        except CohortGateError as e:
            log.error("Cohort gate failed: %s", e)
            return 2

        if args.reset:
            log.info("--reset: deleting tenant=1 seeder rows")
            _reset_tenant_one(conn)

        if args.verify_only:
            report = run_verification(conn)
            print(report.render())
            return 0 if report.all_pass() else 1

        # Stages 1-2: API-driven scaffolding + activity loop.
        post_throttle = float(os.environ.get("SEED_POST_THROTTLE_SECS", "0.55"))
        client = ManualClient(
            base_url=cfg.api_base_url, post_throttle=post_throttle
        )
        try:
            if args.persona is None:
                personas = REGISTRY
            else:
                personas = tuple(
                    p for p in REGISTRY if args.persona in p.email
                )
                if not personas:
                    log.error("--persona=%s matched no persona", args.persona)
                    return 2

            # /login is rate-limited. Throttle sequential logins so the six
            # personas stay under the limit. Configurable via
            # SEED_LOGIN_THROTTLE_SECS (default 13s; 0 to disable for tests /
            # rate-limit-bypassed environments).
            throttle = float(os.environ.get("SEED_LOGIN_THROTTLE_SECS", "13"))
            for i, persona in enumerate(personas):
                if i > 0 and throttle > 0:
                    time.sleep(throttle)
                client.token_for(persona.email, DEFAULT_PASSWORD)

            # Stage 1 — scaffolding from records/*.json (conditions, allergies,
            # medications/supplements, schedules, stacks, family/social history,
            # vaccinations). Captures server-side stack UUIDs so the Stage 2
            # log_stack events can reference real rows.
            if args.no_stage1:
                log.info("--no-stage1: skipping scaffolding loader")
                stage1_result = Stage1Result()
            else:
                log.info("Stage 1: loading scaffolding from records/*.json ...")
                stage1_result = run_stage1(
                    client, [p.email for p in personas]
                )
                log.info(
                    "Stage 1 complete: %d personas seeded",
                    len(stage1_result.by_email),
                )

            # Stage 2 activity loop.
            for day in iter_days(cfg.window_end, cfg.window_days):
                for persona in personas:
                    events = dispatch_persona_day(
                        persona, day, cfg.seed, scale=cfg.scale,
                    )
                    # Per-day RNG for any dispatch-side randomness (e.g.
                    # picking among the persona's stacks for log_stack).
                    dispatch_rng = rng_for(
                        seed=cfg.seed, persona_id=persona.user_id,
                        source_kind="dispatch", day=day,
                    )
                    for kind, body in events:
                        _dispatch(
                            client, persona.email, kind, body,
                            stage1=stage1_result, rng=dispatch_rng,
                        )
        finally:
            client.close()

        # Report Stage 1-dependent skips (log_stack, log_meal).
        if any(_SKIPPED_STAGE1_DEPENDENT.values()):
            log.warning(
                "Stage 1 scaffolding deferred: skipped %d log_stack + %d "
                "log_meal events. Wire Stage 1 to enable these.",
                _SKIPPED_STAGE1_DEPENDENT["log_stack"],
                _SKIPPED_STAGE1_DEPENDENT["log_meal"],
            )

        # Stage 5: embeddings.
        if not args.no_embeddings:
            counts = fill_embeddings(conn)
            log.info("Embeddings filled: %s", counts)
            conn.commit()

        # Stage 6: verify.
        report = run_verification(conn)
        print(report.render())
        return 0 if report.all_pass() else 1
    finally:
        conn.close()


# Narrative-beat kinds that the seed knows how to POST as observations.
_NARRATIVE_KINDS = frozenset({
    "migraine_episode", "injury_day", "pain_observation",
    "pt_exercise", "missed_dose", "note_to_self",
    "meal_photo_obs", "bp_pact_start",
})


# Counter for log_stack/log_meal events skipped when Stage 1 didn't capture
# stacks/meals for the persona (e.g. records file missing, or stage1 disabled
# via --no-stage1). Both are wired and should be 0 on a normal run.
_SKIPPED_STAGE1_DEPENDENT: dict[str, int] = {"log_stack": 0, "log_meal": 0}


def _dispatch(
    client: ManualClient,
    email: str,
    kind: str,
    body: dict,
    *,
    stage1: Stage1Result | None = None,
    rng=None,
) -> None:
    """Route an event_kind from timeline.py to the matching POST helper.

    `stage1` provides captured stack + meal IDs so log_stack/log_meal events
    can reference real rows. `rng` is a numpy Generator used for any
    dispatch-side randomness (e.g. picking which stack/meal to log on a
    given day).
    """
    if kind == "bp":
        client.post_blood_pressure(email, body)
    elif kind == "weight":
        client.post_weight(email, body)
    elif kind == "log_stack":
        stack_ids = stage1.stack_ids_for(email) if stage1 else []
        if not stack_ids:
            # No Stage 1 stacks for this persona — count and skip.
            _SKIPPED_STAGE1_DEPENDENT["log_stack"] += 1
        else:
            chosen = stack_ids[int(rng.integers(0, len(stack_ids)))] if rng \
                     else stack_ids[0]
            client.post_log_stack(email, {
                "stack_id": chosen,
                "timestamp": body["logged_at"],
            })
    elif kind == "log_meal":
        meal_ids = stage1.meal_ids_for(email) if stage1 else []
        if not meal_ids:
            # No Stage 1 meals for this persona — count and skip.
            _SKIPPED_STAGE1_DEPENDENT["log_meal"] += 1
        else:
            chosen = meal_ids[int(rng.integers(0, len(meal_ids)))] if rng \
                     else meal_ids[0]
            client.post_log_meal(email, {
                "meal_id": chosen,
                "timestamp": body["logged_at"],
            })
    elif kind == "observation":
        client.post_observation(email, body)
    elif kind in _NARRATIVE_KINDS:
        # Narrative beats map to observations with kind-specific text.
        client.post_observation(email, {
            "kind": kind,
            "text": str(body.get("payload", "")),
            "observed_at": body.get("day", "") + "T20:00:00Z",
        })
    else:
        raise ValueError(f"unknown event kind: {kind}")


def main() -> int:
    args = build_parser().parse_args()
    try:
        cfg = SeedConfig.from_env()
    except ConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    logging.basicConfig(level=cfg.log_level)
    return _run_seed(cfg, args)


if __name__ == "__main__":
    sys.exit(main())

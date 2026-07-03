# TestData/three_month_seed/config.py
"""Immutable run-time config for the three-month seeder.

Loads env vars at startup. Fails loud on missing required vars.
Cohort gate is invoked separately (Task 2) — this module just packages config.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date


class ConfigError(RuntimeError):
    """Raised on missing/invalid env-var configuration."""


def _parse_int(name: str, raw: str) -> int:
    try:
        return int(raw)
    except ValueError as e:
        raise ConfigError(f"Invalid {name} '{raw}': {e}") from e


def _parse_float(name: str, raw: str) -> float:
    try:
        return float(raw)
    except ValueError as e:
        raise ConfigError(f"Invalid {name} '{raw}': {e}") from e


@dataclass(frozen=True)
class SeedConfig:
    # Window
    window_end: date
    window_days: int

    # RNG + scale
    seed: int
    scale: float

    # Embeddings
    ollama_url: str
    embedding_model: str
    ollama_timeout: int

    # DB admin connection
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str

    # API
    api_base_url: str

    # Misc
    log_level: str
    output_dir: str

    @classmethod
    def from_env(cls) -> "SeedConfig":
        if os.environ.get("SEED_TEST_DATA") != "true":
            raise ConfigError(
                "SEED_TEST_DATA must be set to 'true' to run the seeder."
            )
        try:
            ollama_url = os.environ["OLLAMA_URL"]
            db_user = os.environ["SEED_DB_USER"]
            db_password = os.environ["SEED_DB_PASSWORD"]
        except KeyError as e:
            raise ConfigError(f"Missing required env var: {e.args[0]}") from e

        window_end_str = os.environ.get("WINDOW_END", "2026-05-08")
        try:
            window_end = date.fromisoformat(window_end_str)
        except ValueError as e:
            raise ConfigError(f"Invalid WINDOW_END '{window_end_str}': {e}") from e

        return cls(
            window_end=window_end,
            window_days=_parse_int("WINDOW_DAYS", os.environ.get("WINDOW_DAYS", "90")),
            seed=_parse_int("SEED", os.environ.get("SEED", "42")),
            scale=_parse_float("SCALE", os.environ.get("SCALE", "1.0")),
            ollama_url=ollama_url,
            embedding_model=os.environ.get(
                "EMBEDDING_MODEL", "nomic-embed-text-v2-moe:latest"
            ),
            ollama_timeout=_parse_int("OLLAMA_TIMEOUT", os.environ.get("OLLAMA_TIMEOUT", "30")),
            db_host=os.environ.get("SEED_DB_HOST", "localhost"),
            db_port=_parse_int("SEED_DB_PORT", os.environ.get("SEED_DB_PORT", "5432")),
            db_name=os.environ.get("SEED_DB_NAME", "healthv10"),
            db_user=db_user,
            db_password=db_password,
            api_base_url=os.environ.get("SEED_API_BASE_URL", "http://localhost"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            output_dir=os.environ.get(
                "OUTPUT_DIR", "TestData/three_month_seed/output"
            ),
        )

"""Live test harness configuration: env + CLI → LiveTestConfig."""
from __future__ import annotations

import argparse
import uuid
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = REPO_ROOT / "UserApp" / ".env.livetest"
DEFAULT_REPORT_DIR = REPO_ROOT / "UserApp" / "livetest" / "reports"

REQUIRED_FIELDS = ("BASE_URL", "APP_DB_PASSWORD")

DEFAULTS = {
    "DB_HOST": "localhost",
    "DB_PORT": "5432",
    "DB_NAME": "healthv10",
    "APP_DB_USER": "healthv10_app",
    "TENANT_ID": "1",
    "TEST_EMAIL": "test@example.com",
    "TEST_PASSWORD": "Password2026",
    "TIMEOUT": "30",
    "REPORT_DIR": str(DEFAULT_REPORT_DIR),
}


@dataclass(frozen=True)
class LiveTestConfig:
    base_url: str
    db_host: str
    db_port: int
    db_name: str
    app_db_user: str
    app_db_password: str
    tenant_id: int
    test_email: str
    test_password: str
    run_id: str
    report_dir: Path
    timeout: int


def _int_field(name: str, value: str) -> int:
    """Parse an integer config field, raising a field-aware error on failure.

    Replaces bare ``int(...)`` so an operator who types ``TIMEOUT=30s`` gets
    ``ValueError: TIMEOUT must be an integer, got '30s'`` instead of a
    confusing ``invalid literal for int()`` traceback.
    """
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(
            f"{name} must be an integer, got {value!r}"
        ) from exc


def _load_env_file(path: Path) -> dict[str, str]:
    """Minimal .env parser: KEY=VALUE per line, # for comments.

    Raises ValueError on malformed lines so a typo like ``BASE_URL https://x``
    fails loudly instead of being silently dropped (which previously surfaced
    later as a confusing "BASE_URL is required" error pointing at a file the
    operator can clearly see contains BASE_URL).
    """
    if not path.exists():
        return {}
    env: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Malformed line in {path}: {raw!r}")
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Live test harness")
    p.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE,
                   help="Path to .env.livetest file (default: UserApp/.env.livetest)")
    p.add_argument("--base-url", help="Override BASE_URL from env file")
    p.add_argument("--db-host", help="Override DB_HOST")
    p.add_argument("--db-port", type=int, help="Override DB_PORT")
    p.add_argument("--report-dir", type=Path,
                   help="Override REPORT_DIR (where livetest-report-*.md files land)")
    p.add_argument("--timeout", type=int, help="HTTP timeout in seconds")
    return p


def load_config(argv: list[str]) -> LiveTestConfig:
    """Load config from env file, overlay CLI args, validate required fields."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    env = _load_env_file(args.env_file)

    for field in REQUIRED_FIELDS:
        if field not in env or not env[field]:
            raise ValueError(
                f"{field} is required in {args.env_file} "
                f"(see .env.livetest.example for the template)"
            )

    def pick(key: str, cli_value: object | None = None) -> str:
        if cli_value is not None:
            return str(cli_value)
        return env.get(key, DEFAULTS.get(key, ""))

    return LiveTestConfig(
        base_url=pick("BASE_URL", args.base_url),
        db_host=pick("DB_HOST", args.db_host),
        db_port=_int_field("DB_PORT", pick("DB_PORT", args.db_port)),
        db_name=pick("DB_NAME"),
        app_db_user=pick("APP_DB_USER"),
        app_db_password=pick("APP_DB_PASSWORD"),
        tenant_id=_int_field("TENANT_ID", pick("TENANT_ID")),
        test_email=pick("TEST_EMAIL"),
        test_password=pick("TEST_PASSWORD"),
        run_id=uuid.uuid4().hex[:8],
        report_dir=Path(pick("REPORT_DIR", args.report_dir)),
        timeout=_int_field("TIMEOUT", pick("TIMEOUT", args.timeout)),
    )

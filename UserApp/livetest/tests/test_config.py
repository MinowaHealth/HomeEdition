"""Tests for livetest.config.load_config."""
from __future__ import annotations

from pathlib import Path

import pytest

from livetest.config import LiveTestConfig, load_config


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    f = tmp_path / ".env.livetest"
    f.write_text(
        "BASE_URL=https://env.example.com\n"
        "DB_HOST=envhost\n"
        "DB_PORT=5433\n"
        "DB_NAME=envdb\n"
        "APP_DB_USER=envuser\n"
        "APP_DB_PASSWORD=envpass\n"
        "TENANT_ID=2\n"
        "TEST_EMAIL=env@example.com\n"
        "TEST_PASSWORD=envpw\n"
    )
    return f


def test_loads_from_env_file(env_file: Path):
    cfg = load_config(["--env-file", str(env_file)])
    assert isinstance(cfg, LiveTestConfig)
    assert cfg.base_url == "https://env.example.com"
    assert cfg.db_host == "envhost"
    assert cfg.db_port == 5433
    assert cfg.db_name == "envdb"
    assert cfg.app_db_user == "envuser"
    assert cfg.app_db_password == "envpass"
    assert cfg.tenant_id == 2
    assert cfg.test_email == "env@example.com"
    assert cfg.test_password == "envpw"


def test_cli_overrides_env(env_file: Path):
    cfg = load_config([
        "--env-file", str(env_file),
        "--base-url", "https://cli.example.com",
    ])
    assert cfg.base_url == "https://cli.example.com"
    assert cfg.db_host == "envhost"  # not overridden, still from env


def test_missing_required_field_raises(tmp_path: Path):
    f = tmp_path / ".env.livetest"
    f.write_text("BASE_URL=https://x\n")  # missing APP_DB_PASSWORD etc.
    with pytest.raises(ValueError, match="APP_DB_PASSWORD"):
        load_config(["--env-file", str(f)])


def test_run_id_is_unique_per_call(env_file: Path):
    a = load_config(["--env-file", str(env_file)])
    b = load_config(["--env-file", str(env_file)])
    assert a.run_id != b.run_id
    assert len(a.run_id) == 8  # uuid4 hex first 8 chars


def test_defaults_applied_when_env_missing_optional(tmp_path: Path):
    f = tmp_path / ".env.livetest"
    f.write_text(
        "BASE_URL=https://x\n"
        "APP_DB_PASSWORD=pw\n"
    )
    cfg = load_config(["--env-file", str(f)])
    assert cfg.db_host == "localhost"
    assert cfg.db_port == 5432
    assert cfg.db_name == "healthv10"
    assert cfg.app_db_user == "healthv10_app"
    assert cfg.tenant_id == 1
    assert cfg.test_email == "test@example.com"
    assert cfg.test_password == "Password2026"
    assert cfg.timeout == 30


def test_report_dir_resolved_from_package_root(env_file: Path):
    cfg = load_config(["--env-file", str(env_file)])
    assert cfg.report_dir.name == "reports"
    assert cfg.report_dir.parent.name == "livetest"


def test_report_dir_cli_override(env_file: Path, tmp_path: Path):
    custom = tmp_path / "custom-reports"
    cfg = load_config([
        "--env-file", str(env_file),
        "--report-dir", str(custom),
    ])
    assert cfg.report_dir == custom


def test_report_dir_env_override(tmp_path: Path):
    custom = tmp_path / "env-reports"
    f = tmp_path / ".env.livetest"
    f.write_text(
        "BASE_URL=https://x\n"
        "APP_DB_PASSWORD=pw\n"
        f"REPORT_DIR={custom}\n"
    )
    cfg = load_config(["--env-file", str(f)])
    assert cfg.report_dir == custom


def test_int_field_error_includes_field_name(tmp_path: Path):
    f = tmp_path / ".env.livetest"
    f.write_text(
        "BASE_URL=https://x\n"
        "APP_DB_PASSWORD=pw\n"
        "TIMEOUT=30s\n"  # invalid integer
    )
    with pytest.raises(ValueError, match="TIMEOUT must be an integer"):
        load_config(["--env-file", str(f)])


def test_malformed_env_line_raises(tmp_path: Path):
    f = tmp_path / ".env.livetest"
    f.write_text(
        "BASE_URL=https://x\n"
        "APP_DB_PASSWORD pw\n"  # missing '=', typo
    )
    with pytest.raises(ValueError, match="Malformed line"):
        load_config(["--env-file", str(f)])


def test_missing_env_file_raises_on_required_check(tmp_path: Path):
    missing = tmp_path / "does-not-exist"
    with pytest.raises(ValueError, match="BASE_URL"):
        load_config(["--env-file", str(missing)])

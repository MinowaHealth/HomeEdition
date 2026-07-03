import os
import pytest
from TestData.three_month_seed.config import SeedConfig, ConfigError

def test_config_requires_seed_test_data_true(monkeypatch):
    monkeypatch.delenv("SEED_TEST_DATA", raising=False)
    with pytest.raises(ConfigError, match="SEED_TEST_DATA"):
        SeedConfig.from_env()

def test_config_requires_ollama_url(monkeypatch):
    monkeypatch.setenv("SEED_TEST_DATA", "true")
    monkeypatch.setenv("SEED_DB_USER", "admin")
    monkeypatch.setenv("SEED_DB_PASSWORD", "x")
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    with pytest.raises(ConfigError, match="OLLAMA_URL"):
        SeedConfig.from_env()

def test_config_loads_defaults(monkeypatch):
    monkeypatch.setenv("SEED_TEST_DATA", "true")
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    monkeypatch.setenv("SEED_DB_USER", "admin")
    monkeypatch.setenv("SEED_DB_PASSWORD", "x")
    cfg = SeedConfig.from_env()
    assert cfg.window_days == 90
    assert cfg.seed == 42
    assert cfg.scale == 1.0
    assert cfg.embedding_model == "nomic-embed-text-v2-moe:latest"
    assert cfg.api_base_url == "http://localhost"


def test_config_requires_seed_db_user(monkeypatch):
    monkeypatch.setenv("SEED_TEST_DATA", "true")
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    monkeypatch.delenv("SEED_DB_USER", raising=False)
    monkeypatch.setenv("SEED_DB_PASSWORD", "x")
    with pytest.raises(ConfigError, match="SEED_DB_USER"):
        SeedConfig.from_env()


def test_config_requires_seed_db_password(monkeypatch):
    monkeypatch.setenv("SEED_TEST_DATA", "true")
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    monkeypatch.setenv("SEED_DB_USER", "admin")
    monkeypatch.delenv("SEED_DB_PASSWORD", raising=False)
    with pytest.raises(ConfigError, match="SEED_DB_PASSWORD"):
        SeedConfig.from_env()


def test_config_rejects_malformed_window_end(monkeypatch):
    monkeypatch.setenv("SEED_TEST_DATA", "true")
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    monkeypatch.setenv("SEED_DB_USER", "admin")
    monkeypatch.setenv("SEED_DB_PASSWORD", "x")
    monkeypatch.setenv("WINDOW_END", "not-a-date")
    with pytest.raises(ConfigError, match="WINDOW_END"):
        SeedConfig.from_env()


def test_config_rejects_malformed_window_days(monkeypatch):
    monkeypatch.setenv("SEED_TEST_DATA", "true")
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    monkeypatch.setenv("SEED_DB_USER", "admin")
    monkeypatch.setenv("SEED_DB_PASSWORD", "x")
    monkeypatch.setenv("WINDOW_DAYS", "ninety")
    with pytest.raises(ConfigError, match="WINDOW_DAYS"):
        SeedConfig.from_env()


def test_config_rejects_malformed_scale(monkeypatch):
    monkeypatch.setenv("SEED_TEST_DATA", "true")
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    monkeypatch.setenv("SEED_DB_USER", "admin")
    monkeypatch.setenv("SEED_DB_PASSWORD", "x")
    monkeypatch.setenv("SCALE", "big")
    with pytest.raises(ConfigError, match="SCALE"):
        SeedConfig.from_env()

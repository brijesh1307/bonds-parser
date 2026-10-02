from __future__ import annotations

from pathlib import Path

import pytest

from app.config import EngineSettings, Settings
from app.errors import ConfigError


def test_defaults_match_baseline() -> None:
    s = Settings.from_env({})
    assert s.data_dir == Path("./data")
    assert s.templates_dir == Path("./templates")
    assert s.clients_path == Path("./data/clients.json")
    assert s.audit_path == Path("./data/audit/audit.jsonl")
    assert (s.max_upload_mb, s.max_pages) == (10, 20)
    assert (s.confidence_threshold, s.template_match_threshold) == (0.90, 0.80)
    assert s.cors_origins == ("http://localhost:3000", "http://localhost:5173")
    assert s.docs_enabled is True
    assert (s.auth_max_failures, s.auth_window_seconds, s.auth_block_seconds) == (10, 300, 900)
    assert (s.log_level, s.audit_retention_years) == ("INFO", 8)


def test_env_example_lists_every_setting() -> None:
    env_example = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    for name in Settings.__dataclass_fields__:
        assert f"BONDS_{name.upper()}=" in env_example, f"BONDS_{name.upper()} missing from .env.example"


def test_env_overrides() -> None:
    s = Settings.from_env(
        {
            "BONDS_DATA_DIR": "/srv/bonds",
            "BONDS_MAX_UPLOAD_MB": "5",
            "BONDS_CONFIDENCE_THRESHOLD": "0.95",
            "BONDS_CORS_ORIGINS": " https://a.example , https://b.example ,",
            "BONDS_DOCS_ENABLED": "No",
            "BONDS_LOG_LEVEL": "debug",
        }
    )
    assert s.data_dir == Path("/srv/bonds")
    assert s.max_upload_bytes == 5 * 1024 * 1024
    assert s.confidence_threshold == 0.95
    assert s.cors_origins == ("https://a.example", "https://b.example")
    assert s.docs_enabled is False
    assert s.log_level == "DEBUG"
    assert s.clients_path == Path("/srv/bonds/clients.json")
    assert s.audit_path == Path("/srv/bonds/audit/audit.jsonl")


def test_blank_value_keeps_default() -> None:
    assert Settings.from_env({"BONDS_MAX_PAGES": "  "}).max_pages == 20


def test_engine_slice() -> None:
    assert Settings.from_env({}).engine == EngineSettings(0.90, 0.80, 20)


@pytest.mark.parametrize(
    "env",
    [
        {"BONDS_MAX_PAGES": "twenty"},
        {"BONDS_MAX_PAGES": "0"},
        {"BONDS_CONFIDENCE_THRESHOLD": "1.5"},
        {"BONDS_TEMPLATE_MATCH_THRESHOLD": "-0.1"},
        {"BONDS_DOCS_ENABLED": "maybe"},
        {"BONDS_LOG_LEVEL": "LOUD"},
    ],
)
def test_invalid_values_refuse_to_start(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        Settings.from_env(env)

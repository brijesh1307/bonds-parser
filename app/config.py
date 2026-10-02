"""Settings read from BONDS_* environment variables (docs/00_design_baseline.md §9, docs/03_lld.md §3.2).

Invalid values raise ConfigError at startup so the process refuses to start with a bad config.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, fields
from functools import lru_cache
from pathlib import Path

from app.errors import ConfigError

_TRUE = {"true", "1", "yes"}
_FALSE = {"false", "0", "no"}
_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path = Path("./data")  # BONDS_DATA_DIR
    templates_dir: Path = Path("./templates")  # BONDS_TEMPLATES_DIR
    clients_file: Path | None = None  # BONDS_CLIENTS_FILE (default <data_dir>/clients.json)
    audit_file: Path | None = None  # BONDS_AUDIT_FILE (default <data_dir>/audit/audit.jsonl)
    max_upload_mb: int = 10  # BONDS_MAX_UPLOAD_MB
    max_pages: int = 20  # BONDS_MAX_PAGES
    confidence_threshold: float = 0.90  # BONDS_CONFIDENCE_THRESHOLD
    template_match_threshold: float = 0.80  # BONDS_TEMPLATE_MATCH_THRESHOLD
    cors_origins: tuple[str, ...] = ("http://localhost:3000", "http://localhost:5173")  # BONDS_CORS_ORIGINS
    docs_enabled: bool = True  # BONDS_DOCS_ENABLED
    auth_max_failures: int = 10  # BONDS_AUTH_MAX_FAILURES
    auth_window_seconds: int = 300  # BONDS_AUTH_WINDOW_SECONDS
    auth_block_seconds: int = 900  # BONDS_AUTH_BLOCK_SECONDS
    log_level: str = "INFO"  # BONDS_LOG_LEVEL
    audit_retention_years: int = 8  # BONDS_AUDIT_RETENTION_YEARS

    @property
    def clients_path(self) -> Path:
        return self.clients_file or self.data_dir / "clients.json"

    @property
    def audit_path(self) -> Path:
        return self.audit_file or self.data_dir / "audit" / "audit.jsonl"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def engine(self) -> EngineSettings:
        return EngineSettings(
            confidence_threshold=self.confidence_threshold,
            template_match_threshold=self.template_match_threshold,
            max_pages=self.max_pages,
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Settings:
        values: dict[str, object] = {}
        for f in fields(cls):
            name = f"BONDS_{f.name.upper()}"
            raw = env.get(name)
            if raw is None or raw.strip() == "":
                continue
            values[f.name] = _parse(name, f.name, raw.strip())
        settings = cls(**values)  # type: ignore[arg-type]
        settings._check()
        return settings

    def _check(self) -> None:
        for name in ("confidence_threshold", "template_match_threshold"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ConfigError(f"BONDS_{name.upper()} must be between 0 and 1, got {value}")
        for name in (
            "max_upload_mb",
            "max_pages",
            "auth_max_failures",
            "auth_window_seconds",
            "auth_block_seconds",
            "audit_retention_years",
        ):
            if getattr(self, name) <= 0:
                raise ConfigError(f"BONDS_{name.upper()} must be a positive integer")
        if self.log_level not in _LOG_LEVELS:
            raise ConfigError(f"BONDS_LOG_LEVEL must be one of {sorted(_LOG_LEVELS)}, got {self.log_level!r}")


@dataclass(frozen=True, slots=True)
class EngineSettings:
    """The slice of settings the engine needs, so the engine stays config-agnostic."""

    confidence_threshold: float
    template_match_threshold: float
    max_pages: int


def _parse(env_name: str, field: str, raw: str) -> object:
    try:
        if field in ("data_dir", "templates_dir", "clients_file", "audit_file"):
            return Path(raw)
        if field == "cors_origins":
            return tuple(o.strip() for o in raw.split(",") if o.strip())
        if field == "docs_enabled":
            low = raw.lower()
            if low in _TRUE:
                return True
            if low in _FALSE:
                return False
            raise ValueError("expected true/false/1/0/yes/no")
        if field in ("confidence_threshold", "template_match_threshold"):
            return float(raw)
        if field == "log_level":
            return raw.upper()
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"invalid {env_name}={raw!r}: {exc}") from exc


@lru_cache
def get_settings() -> Settings:
    settings = Settings.from_env()
    logging.getLogger("app").setLevel(settings.log_level)
    return settings

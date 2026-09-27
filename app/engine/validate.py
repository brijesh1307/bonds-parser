"""Required-field and confidence checks (docs/03_lld.md §3.9, §4.13-4.14).

PH1: missing required fields and low-confidence required fields. The arithmetic and date
checks (principal, consideration, repo legs, ...) are added in PH2 (T2.6).
"""

from __future__ import annotations

from typing import Any

from app.engine.fields import required_fields
from app.engine.models import FieldValue
from app.engine.normalize import isin_check_digit_ok

__all__ = ["isin_check_digit_ok", "low_confidence", "missing_required"]


def _value(deal: dict[str, Any], path: str) -> Any:
    if path.startswith("repo."):
        repo = deal.get("repo") or {}
        return repo.get(path.removeprefix("repo."))
    return deal.get(path)


def missing_required(deal: dict[str, Any]) -> list[str]:
    return [p for p in required_fields(deal.get("deal_type")) if _value(deal, p) is None]


def low_confidence(fields: dict[str, FieldValue], deal_type: str | None, threshold: float) -> list[str]:
    return [p for p in required_fields(deal_type) if p in fields and fields[p].confidence < threshold]

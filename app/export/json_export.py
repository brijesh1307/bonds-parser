"""JSON output (docs/05_data_dictionary_and_outputs.md §5.11).

Decimals are plain-notation strings (D10), dates ISO (D11), nulls always emitted.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


def plain(obj: Any) -> Any:
    """Recursively convert engine values to JSON-ready values."""
    if isinstance(obj, Decimal):
        return format(obj, "f")
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [plain(v) for v in obj]
    return obj


def to_json(result: BaseModel) -> bytes:
    """Pretty-printed export file body (2-space indent, UTF-8)."""
    return (result.model_dump_json(by_alias=True, indent=2) + "\n").encode("utf-8")


def dumps(obj: Any) -> str:
    return json.dumps(plain(obj), indent=2, ensure_ascii=False)

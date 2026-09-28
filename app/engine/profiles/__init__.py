"""Market profiles loaded from <CODE>.json files (docs/09_market_profiles.md §4).

A profile is configuration, not code: adding a market means adding a JSON file with
"status": "active". Only active profiles are used for parsing.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any, Literal, cast

from app.engine.models import MarketProfile

PROFILE_DIR = Path(__file__).resolve().parent
_WEEKDAYS = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}


def _load(data: dict[str, Any]) -> MarketProfile:
    instruments = tuple(
        (pattern, code)
        for code, spec in data.get("instrument_types", {}).items()
        for pattern in spec["patterns"]
    )
    return MarketProfile(
        code=data["code"],
        locale=data["locale"],
        currency=data["currency"],
        date_order=cast(Literal["DMY", "MDY", "YMD"], data["date"]["order"]),
        amount_units={k.lower(): int(v) for k, v in data["numbers"]["units"].items()},
        weekend=frozenset(_WEEKDAYS[d] for d in data["calendar"]["weekend"]),
        default_day_count=dict(data.get("day_count", {})),
        default_coupon_frequency=dict(data.get("coupon_frequency", {})),
        platforms=dict(data.get("platform_names", {})),
        settlement_systems=tuple(data.get("signals", {}).get("settlement_systems", ())),
        instrument_patterns=instruments,
    )


@cache
def _all() -> dict[str, tuple[str, MarketProfile]]:
    out: dict[str, tuple[str, MarketProfile]] = {}
    for path in sorted(PROFILE_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        out[data["code"]] = (data.get("status", "planned"), _load(data))
    return out


def get_profile(market: str) -> MarketProfile | None:
    """The active profile for a market code, or None (UNKNOWN, or no active profile yet)."""
    entry = _all().get(market)
    return entry[1] if entry and entry[0] == "active" else None


def available_profiles() -> list[MarketProfile]:
    return [p for status, p in _all().values() if status == "active"]

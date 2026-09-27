"""Golden-file tests: samples/mock/*.pdf vs samples/mock/expected/*.json (docs/10_testing_strategy.md §5).

Comparison rules (§5.1): only keys in the expected JSON are checked; decimals compare by value
but must be strings; ints as ints; dates exact ISO; text exact after whitespace collapse.
All mismatches of a slip are reported in one assertion.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.engine.pipeline import run
from app.export.json_export import plain
from tests.conftest import EXPECTED_DIR, MOCK_DIR

# docs/10_testing_strategy.md §5.2 - the only allowed exception.
# The 04 repo slip prints only "6.99% GS 2031"; no maturity date appears anywhere on it.
# The generator knows it (2031-06-17), but the parser must not invent it.
KNOWN_EXCEPTIONS: dict[tuple[str, str], Any] = {
    ("04_market_repo_reverse_repo", "maturity_date"): None,
}

# Fields the parser cannot produce yet, by the phase that adds them (docs/14_task_breakdown.md).
# Strict: a pending field that starts matching fails the test until it is removed from this list.
_PH2_ENUMS_AND_DERIVATION = {  # T2.3 market, T2.4 enum normalisers, T2.5 derivation rules
    "deal_type",
    "instrument_type",
    "buy_sell",
    "platform",
    "coupon_frequency",
    "day_count",
    "currency",
    "settlement_mode",
}
PENDING: dict[str, set[str] | str] = {
    "01_gsec_outright_purchase": _PH2_ENUMS_AND_DERIVATION,
    "02_corporate_ncd_outright_sale": "PH2",  # horizontal grids (T2.1)
    "03_tbill_primary_auction_allotment": "PH2",  # letter / prose layout (T2.1, T2.5)
    "04_market_repo_reverse_repo": "PH2",  # two-leg grid (T2.1)
}

INT_FIELDS = {
    "accrued_days",
    "quantity",
    "tenor_days",
    "coupon_frequency",
    "repo_days",
    "leg1_accrued_days",
    "leg2_accrued_days",
}


def _same(expected: Any, actual: Any, name: str) -> bool:
    if expected is None or actual is None:
        return expected is actual
    if name in INT_FIELDS:
        return isinstance(actual, int) and actual == expected
    if isinstance(expected, str) and isinstance(actual, str):
        try:
            return Decimal(expected) == Decimal(actual)
        except InvalidOperation:
            return " ".join(expected.split()) == " ".join(actual.split())
    return bool(expected == actual)


def _diffs(expected: dict[str, Any], actual: dict[str, Any], prefix: str = "") -> dict[str, tuple[Any, Any]]:
    out: dict[str, tuple[Any, Any]] = {}
    for key, exp in expected.items():
        act = actual.get(key) if isinstance(actual, dict) else None
        if isinstance(exp, dict):
            out.update(_diffs(exp, act or {}, f"{prefix}{key}."))
        elif not _same(exp, act, key):
            out[prefix + key] = (exp, act)
    return out


@pytest.mark.parametrize("stem", sorted(p.stem for p in MOCK_DIR.glob("*.pdf")))
def test_golden(stem: str) -> None:
    pending = PENDING.get(stem, set())
    if isinstance(pending, str):
        pytest.skip(f"layout supported from {pending} (docs/14_task_breakdown.md)")

    pdf: Path = MOCK_DIR / f"{stem}.pdf"
    data = pdf.read_bytes()
    expected = json.loads((EXPECTED_DIR / f"{stem}.json").read_text(encoding="utf-8"))
    for (s, field), value in KNOWN_EXCEPTIONS.items():
        if s == stem:
            expected[field] = value

    result = run(data, file_name=pdf.name, sha256=hashlib.sha256(data).hexdigest(), es=Settings().engine)
    diffs = _diffs(expected, plain(result.deal))

    failures = [f"{k}: expected {e!r}, got {a!r}" for k, (e, a) in sorted(diffs.items()) if k not in pending]
    now_passing = sorted(pending - diffs.keys())
    assert not failures, f"{stem} mismatches:\n" + "\n".join(failures)
    assert not now_passing, f"{stem}: now correct, remove from PENDING: {now_passing}"

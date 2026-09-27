from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.engine.models import MarketProfile
from app.engine.normalize import (
    DocHints,
    NormalizationError,
    isin_check_digit_ok,
    normalize_isin,
    normalize_value,
    parse_amount,
    parse_date,
    parse_int,
    parse_rate,
    scale,
)

# Minimal India profile for unit tests; the real one is loaded from profiles/IN.json in PH2.
IN = MarketProfile(
    code="IN",
    locale="en-IN",
    currency="INR",
    date_order="DMY",
    amount_units={
        "cr": 10**7,
        "crore": 10**7,
        "crores": 10**7,
        "lakh": 10**5,
        "lakhs": 10**5,
        "lac": 10**5,
        "lacs": 10**5,
        "mn": 10**6,
        "million": 10**6,
    },
)


# --------------------------------------------------------------------------- dates (docs/05 §8.4)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("24-Sep-2026", date(2026, 9, 24)),
        ("24 September 2026", date(2026, 9, 24)),
        ("Sep 24, 2026", date(2026, 9, 24)),
        ("2026-09-24", date(2026, 9, 24)),
        ("24/09/2026", date(2026, 9, 24)),
        ("24.09.2026", date(2026, 9, 24)),
        ("23-Sep-2026  15:07", date(2026, 9, 23)),
        ("25-Sep-2026 (T+1)", date(2026, 9, 25)),
        ("24th Sep 2026", date(2026, 9, 24)),
        ("24-Sep-26", date(2026, 9, 24)),
        ("25032027", date(2027, 3, 25)),
        ("20270325", date(2027, 3, 25)),
        ("09/09/2026", date(2026, 9, 9)),
    ],
)
def test_parse_date(raw: str, expected: date) -> None:
    assert parse_date(raw) == expected


def test_ambiguous_date_uses_hint_then_profile() -> None:
    assert parse_date("05/09/2026", IN) == date(2026, 9, 5)  # India: day first
    assert parse_date("05/09/2026", IN, DocHints(date_order="MDY")) == date(2026, 5, 9)
    with pytest.raises(NormalizationError) as e:
        parse_date("05/09/2026")
    assert e.value.code == "DATE_AMBIGUOUS"


@pytest.mark.parametrize("raw", ["31-Feb-2026", "not a date", "32/13/2026"])
def test_invalid_dates(raw: str) -> None:
    with pytest.raises(NormalizationError):
        parse_date(raw)


@given(st.dates(min_value=date(1971, 1, 1), max_value=date(2099, 12, 31)))
def test_date_round_trip(d: date) -> None:
    for text in (d.strftime("%d-%b-%Y"), d.isoformat(), d.strftime("%d %B %Y"), d.strftime("%d/%m/%Y")):
        assert parse_date(text, IN) == d


# --------------------------------------------------------------------------- amounts (docs/05 §8.3)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("5,00,00,000.00", "50000000.00"),
        ("50,000,000.00", "50000000.00"),
        ("5.00 Cr", "50000000.00"),
        ("5 crore", "50000000"),
        ("50 Lakh", "5000000"),
        ("50 lacs", "5000000"),
        ("INR 30,00,00,000.00", "300000000.00"),
        ("Rs. 1,000/-", "1000"),
        ("₹ 1,000", "1000"),
        ("(1,234.50)", "-1234.50"),
        ("-1,234.50", "-1234.50"),
        ("16,46,805.56", "1646805.56"),
    ],
)
def test_parse_amount(raw: str, expected: str) -> None:
    assert parse_amount(raw, IN) == Decimal(expected)


def test_unit_in_label_applies() -> None:
    assert parse_amount("5.00", IN, label="Nominal (Cr)") == Decimal("50000000.00")
    assert parse_amount("5.00", IN, label="Face Value (INR)") == Decimal("5.00")


@pytest.mark.parametrize("raw", ["1,23,4567", "abc", "5 bananas"])
def test_bad_amounts(raw: str) -> None:
    with pytest.raises(NormalizationError) as e:
        parse_amount(raw, IN)
    assert e.value.code == "AMOUNT_INVALID"


def test_units_need_a_profile() -> None:
    with pytest.raises(NormalizationError):
        parse_amount("5 Cr")


def _indian(n: int) -> str:
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return ",".join([head, *groups, tail]) if head else ",".join([*groups, tail])


@given(st.integers(min_value=0, max_value=10**13), st.integers(min_value=0, max_value=99))
def test_amount_round_trip_indian_and_western(rupees: int, paise: int) -> None:
    expected = Decimal(f"{rupees}.{paise:02d}")
    assert parse_amount(f"{_indian(rupees)}.{paise:02d}", IN) == expected
    assert parse_amount(f"{rupees:,}.{paise:02d}", IN) == expected


# --------------------------------------------------------------------------- rates, ints, ISIN


def test_parse_rate_keeps_printed_precision() -> None:
    assert str(parse_rate("7.10% p.a. (Semi-Annual)")) == "7.10"
    assert str(parse_rate("8.25% Annual")) == "8.25"
    with pytest.raises(NormalizationError):
        parse_rate("n/a")


def test_parse_int() -> None:
    assert parse_int("167 (30/360)") == 167
    assert parse_int("1,000") == 1000
    with pytest.raises(NormalizationError):
        parse_int("none")


@pytest.mark.parametrize(
    "isin", ["IN0020240A75", "INE917S07032", "IN002026Y188", "IN0020210C53", "US0378331005"]
)
def test_valid_isins(isin: str) -> None:
    assert isin_check_digit_ok(isin)


def test_isin_check_digit_rejects_typo() -> None:
    assert not isin_check_digit_ok("IN0020240A76")
    assert not isin_check_digit_ok("IN002024")


def test_normalize_isin() -> None:
    assert normalize_isin(" in0020240a75 ") == "IN0020240A75"
    assert normalize_isin("(ISIN IN002026Y188)") == "IN002026Y188"
    assert normalize_isin("IN0020240A76") == "IN0020240A76"  # kept; validation flags it (PH2)
    with pytest.raises(NormalizationError):
        normalize_isin("none")


# --------------------------------------------------------------------------- scale (docs/05 §8.2)


@pytest.mark.parametrize(
    ("value", "kind", "expected"),
    [
        ("50000000.00", "nominal", "50000000"),
        ("100000.50", "nominal", "100000.5"),
        ("1646805.56", "cash", "1646805.56"),
        ("45161.5", "cash", "45161.50"),
        ("101.235", "price", "101.2350"),
        ("97.25201", "price", "97.25201"),
        ("6.8865", "yield", "6.8865"),
        ("7.1", "rate", "7.10"),
        ("83.2150", "fx", "83.2150"),
    ],
)
def test_scale(value: str, kind: str, expected: str) -> None:
    assert format(scale(Decimal(value), kind), "f") == expected  # type: ignore[arg-type]


def test_normalize_value_dispatch() -> None:
    assert normalize_value("face_value", "5,00,00,000.00", IN) == Decimal("50000000")
    assert str(normalize_value("price", "101.2350")) == "101.2350"
    assert normalize_value("trade_date", "24-Sep-2026") == date(2026, 9, 24)
    assert normalize_value("accrued_days", "167 (30/360)") == 167
    assert normalize_value("counterparty", "  Anonymous  (NDS-OM) ") == "Anonymous (NDS-OM)"
    assert normalize_value("isin", "IN0020240A75") == "IN0020240A75"
    assert normalize_value("deal_type", "OUTRIGHT PURCHASE") is None  # enum normalisers arrive in PH2

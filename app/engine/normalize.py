"""Turn raw slip text into typed values (docs/03_lld.md §3.9, §4.10, §4.11; docs/05 §8).

PH1 covers strings, dates, amounts, rates, integers and ISINs. Enum normalisation
(deal_type, buy_sell, day_count, ...) is added in PH2 (docs/14_task_breakdown.md T2.4);
until then enum fields normalise to None without an issue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from app.engine.fields import FIELD_SPECS, DecimalKind
from app.engine.models import MarketProfile

MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}


class NormalizationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class DocHints:
    """Document-level hints; e.g. the date order seen in unambiguous dates on the same slip."""

    date_order: Literal["DMY", "MDY"] | None = None


# --------------------------------------------------------------------------- dates

_TIME = re.compile(r"\b\d{1,2}:\d{2}(:\d{2})?\b")
_PAREN = re.compile(r"\([^)]*\)")
_ORDINAL = re.compile(r"\b(\d{1,2})(st|nd|rd|th)\b", re.I)
_ISO = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$")
_D_MON_Y = re.compile(r"^(\d{1,2})[-/ .]+([A-Za-z]{3,9})\.?[-/ .]+(\d{4}|\d{2})$")
_MON_D_Y = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2})\s+(\d{4})$")
_NUMERIC = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})$")
_COMPACT = re.compile(r"^\d{8}$")


def _year(y: str) -> int:
    if len(y) == 4:
        return int(y)
    n = int(y)
    return 2000 + n if n < 70 else 1900 + n


def _make(y: int, m: int, d: int) -> date:
    try:
        return date(y, m, d)
    except ValueError as exc:
        raise NormalizationError("DATE_INVALID", f"not a valid date: {y}-{m}-{d}") from exc


def parse_date(raw: str, profile: MarketProfile | None = None, hints: DocHints | None = None) -> date:
    s = _PAREN.sub(" ", _TIME.sub(" ", raw))
    s = _ORDINAL.sub(r"\1", s).replace(",", " ")
    s = " ".join(s.split())

    if m := _ISO.match(s):
        return _make(int(m[1]), int(m[2]), int(m[3]))
    if (m := _D_MON_Y.match(s)) and m[2].lower() in MONTHS:
        return _make(_year(m[3]), MONTHS[m[2].lower()], int(m[1]))
    if (m := _MON_D_Y.match(s)) and m[1].lower() in MONTHS:
        return _make(int(m[3]), MONTHS[m[1].lower()], int(m[2]))
    if m := _NUMERIC.match(s):
        a, b, y = int(m[1]), int(m[2]), _year(m[3])
        if a > 12 >= b:
            return _make(y, b, a)
        if b > 12 >= a:
            return _make(y, a, b)
        if a == b:
            return _make(y, a, b)
        order = (hints.date_order if hints else None) or (profile.date_order if profile else None)
        if order == "DMY":
            return _make(y, b, a)
        if order == "MDY":
            return _make(y, a, b)
        raise NormalizationError("DATE_AMBIGUOUS", f"{raw!r} could be day-first or month-first")
    if _COMPACT.match(s):
        if s[:2] in ("19", "20"):
            try:
                return date(int(s[:4]), int(s[4:6]), int(s[6:]))
            except ValueError:
                pass
        return _make(int(s[4:]), int(s[2:4]), int(s[:2]))  # ddmmyyyy
    raise NormalizationError("DATE_INVALID", f"no date found in {raw!r}")


# --------------------------------------------------------------------------- amounts

_INDIAN = re.compile(r"^\d{1,2}(,\d{2})*,\d{3}(\.\d+)?$")
_WESTERN = re.compile(r"^\d{1,3}(,\d{3})+(\.\d+)?$")
_CURRENCY = re.compile(r"\b(INR|USD|GBP|EUR|JPY|Rs)\b\.?|₹|\$|£|€|¥", re.I)
_AMOUNT = re.compile(r"(?P<num>[\d,]*\.?\d+)\s*(?P<unit>[A-Za-z]+\.?)?")
_LABEL_UNIT = re.compile(r"\((?:[^)]*\b)?(cr|crs|crore|crores|lakh|lakhs|lac|lacs|mn|million)\b[^)]*\)", re.I)


def parse_amount(raw: str, profile: MarketProfile | None = None, *, label: str | None = None) -> Decimal:
    """'5,00,00,000.00' / '50,000,000.00' / '5.00 Cr' / 'INR 50 Lakh' -> Decimal.

    A unit printed only in the label ('Nominal (Cr)') applies too (baseline §4).
    """
    s = raw.strip()
    neg = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    s = _CURRENCY.sub(" ", s.strip("()").lstrip("-"))
    s = _PAREN.sub(" ", s).replace("/-", " ")
    s = " ".join(s.split())
    m = _AMOUNT.fullmatch(s)
    if not m:
        raise NormalizationError("AMOUNT_INVALID", f"not an amount: {raw!r}")
    num = m["num"]
    if "," in num and not (_INDIAN.fullmatch(num) or _WESTERN.fullmatch(num)):
        raise NormalizationError("AMOUNT_INVALID", f"bad digit grouping: {raw!r}")

    unit = m["unit"]
    if unit is None and label and (lm := _LABEL_UNIT.search(label)):
        unit = lm[1]
    mult = 1
    if unit:
        units = profile.amount_units if profile else {}
        found = units.get(unit.lower().rstrip("."))
        if found is None:
            raise NormalizationError("AMOUNT_INVALID", f"unknown unit {unit!r} in {raw!r}")
        mult = found
    try:
        value = Decimal(num.replace(",", "")) * mult
    except InvalidOperation as exc:
        raise NormalizationError("AMOUNT_INVALID", f"not an amount: {raw!r}") from exc
    return -value if neg else value


def parse_rate(raw: str) -> Decimal:
    """First number, precision as printed: '7.10% p.a. (Semi-Annual)' -> Decimal('7.10')."""
    m = re.search(r"-?\d+(?:\.\d+)?", raw.replace(",", ""))
    if not m:
        raise NormalizationError("VALUE_UNPARSEABLE", f"no number in {raw!r}")
    return Decimal(m[0])


def parse_int(raw: str) -> int:
    """Leading integer: '167 (30/360)' -> 167."""
    m = re.match(r"\s*(\d[\d,]*)", raw)
    if not m:
        raise NormalizationError("VALUE_UNPARSEABLE", f"no integer in {raw!r}")
    return int(m[1].replace(",", ""))


# --------------------------------------------------------------------------- ISIN

ISIN_RE = re.compile(r"[A-Z]{2}[A-Z0-9]{9}\d")


def isin_check_digit_ok(isin: str) -> bool:
    """ISO 6166: Luhn over the letter-expanded digits (same as tools/generate_mock_slips.py)."""
    if not ISIN_RE.fullmatch(isin):
        return False
    digits = "".join(str(int(c, 36)) for c in isin[:11])
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d)
        if i % 2 == 0:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return (10 - total % 10) % 10 == int(isin[11])


def normalize_isin(raw: str) -> str:
    """Upper-case ISIN from the text, preferring a check-digit-valid one.

    Whole-word ISINs are tried first ('ISIN IN002026Y188'); then the text with spaces/hyphens
    removed, for ISINs printed in groups ('IN00 2024 0A75').
    """
    upper = raw.upper()
    tokens: list[str] = re.findall(rf"\b{ISIN_RE.pattern}\b", upper)
    s = re.sub(r"[\s-]", "", upper)
    tokens += [s[i : i + 12] for i in range(max(0, len(s) - 11)) if ISIN_RE.fullmatch(s[i : i + 12])]
    for t in tokens:
        if isin_check_digit_ok(t):
            return t
    if tokens:
        return tokens[0]  # validation reports the bad check digit (PH2)
    raise NormalizationError("VALUE_UNPARSEABLE", f"no ISIN in {raw!r}")


# --------------------------------------------------------------------------- scale (docs/05 §8.2)

_MIN_SCALE: dict[DecimalKind, int] = {"cash": 2, "price": 4, "yield": 4, "rate": 2}


def scale(d: Decimal, kind: DecimalKind) -> Decimal:
    """Output scale by kind; never rounds a value down below what the slip printed."""
    if kind == "nominal":
        return d.quantize(Decimal(1)) if d == d.to_integral_value() else d.normalize()
    if kind == "fx":
        return d
    exponent = d.as_tuple().exponent
    printed = -exponent if isinstance(exponent, int) and exponent < 0 else 0
    return d.quantize(Decimal(1).scaleb(-max(printed, _MIN_SCALE[kind])))


# --------------------------------------------------------------------------- dispatch


def normalize_value(
    path: str,
    raw: str,
    profile: MarketProfile | None = None,
    hints: DocHints | None = None,
    *,
    label: str | None = None,
) -> Any:
    """Normalise raw text for a canonical field path. Raises NormalizationError."""
    spec = FIELD_SPECS[path]
    text = " ".join(raw.split())
    if path == "isin":
        return normalize_isin(text)
    if spec.type == "str":
        return text or None
    if spec.type == "date":
        return parse_date(text, profile, hints)
    if spec.type == "int":
        return parse_int(text)
    if spec.type == "decimal":
        if spec.kind is None:  # every decimal FieldSpec declares a kind
            raise ValueError(f"decimal field {path} has no kind")
        value = (
            parse_amount(text, profile, label=label) if spec.kind in ("nominal", "cash") else parse_rate(text)
        )
        return scale(value, spec.kind)
    return None  # enum (PH2) and float fields are not read from slip labels in PH1

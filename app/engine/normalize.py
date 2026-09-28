"""Turn raw slip text into typed values (docs/03_lld.md §3.9, §4.10, §4.11; docs/05 §8).

Covers strings, dates, amounts, rates, integers, ISINs, PANs and the enum / canonical-name
fields (deal_type, buy_sell, instrument_type, day_count, bid_type, platform, settlement_mode,
broker). Enum values that cannot be recognised raise ENUM_UNKNOWN.
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
_WEEKDAY = re.compile(r"\b(mon|tues?|wed(nes)?|thu(rs)?|fri|sat(ur)?|sun)(day)?\b\.?,?", re.I)


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
    s = _WEEKDAY.sub(" ", s)  # "Friday, December 24, 2021"
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
    if path == "security_name":
        return clean_security_name(text) or None
    if path in ("counterparty_pan", "_seller_pan", "_buyer_pan"):
        return normalize_pan(text)
    if path == "platform":
        return canonical_platform(text, profile) or text
    if path == "settlement_mode":
        return settlement_mode(text) or text
    if path == "broker":
        return None if _NO_BROKER.search(text) else text
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
    if spec.type == "enum":
        enum_value = normalize_enum(path, text, profile)
        if enum_value is None:
            raise NormalizationError("ENUM_UNKNOWN", f"{text!r} is not a known {path} value")
        return enum_value
    return None  # float / bool fields are derived, never read from a slip label


# --------------------------------------------------------------------------- names, PAN, enums

_NO_BROKER = re.compile(r"^\s*(direct|no broker|nil|none|n\.?a\.?|-+)\b|\(no broker\)", re.I)
_ISIN_IN_NAME = re.compile(
    r"\(\s*ISIN\s*[:\-–]?\s*[A-Z]{2}[A-Z0-9]{9}\d\s*\)"  # "(ISIN - INE999M07033)"
    r"|[/|,]?\s*(ISIN\s*[:\-–]?\s*)?\b[A-Z]{2}[A-Z0-9]{9}\d\b"  # "/ INE999A07087", "ISIN: X"
)
PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")


def clean_security_name(text: str) -> str:
    """Drop an ISIN printed inside the name cell ('SECURITY NAME/ISIN NUMBER')."""
    name = _ISIN_IN_NAME.sub(" ", text)
    return " ".join(name.split()).strip(" -/|,")


def normalize_pan(raw: str) -> str:
    m = PAN_RE.search(raw.upper())
    if not m:
        raise NormalizationError("VALUE_UNPARSEABLE", f"no PAN in {raw!r}")
    return m.group(0)


def canonical_platform(text: str, profile: MarketProfile | None) -> str | None:
    for pattern, name in (profile.platforms if profile else {}).items():
        if re.search(pattern, text, re.I):
            return name
    return None


def settlement_mode(text: str) -> str | None:
    m = re.search(r"\bDVP[\s-]*(III|II|I|3|2|1)\b", text, re.I)
    if not m:
        return None
    return "DVP-" + {"1": "I", "2": "II", "3": "III"}.get(m.group(1), m.group(1).upper())


_DEAL_TYPES: list[tuple[str, str]] = [
    (r"\breverse\s+repo\b", "REVERSE_REPO"),
    (r"\btreps\b.*\bborrow|\bborrow.*\btreps\b", "TREPS_BORROW"),
    (r"\btreps\b.*\blend|\blend.*\btreps\b", "TREPS_LEND"),
    (r"\brepo\b", "REPO"),
    (r"\b(laf|sdf|msf|vrrr?)\b", "LAF"),
    (r"\b(auction|allotment)\b", "PRIMARY_AUCTION"),
    (r"private\s+placement|\bebp\b", "PRIMARY_PLACEMENT"),
    (r"\b(outright|purchase|purchased|sale|sold|buy|bought|sell)\b", "OUTRIGHT"),
]
_DAY_COUNTS: list[tuple[str, str]] = [
    (r"\b30e\s*/\s*360\b", "30E/360"),
    (r"\b30\s*/\s*360\b", "30/360"),
    (r"\bact(ual)?\s*/\s*act(ual)?\b", "ACT/ACT"),
    (r"\bact(ual)?\s*/\s*365\b", "ACT/365"),
    (r"\bact(ual)?\s*/\s*364\b", "ACT/364"),
    (r"\bact(ual)?\s*/\s*360\b", "ACT/360"),
]


def buy_sell(text: str) -> str | None:
    if re.search(r"\b(buy|bought|purchase|purchased)\b", text, re.I):
        return "BUY"
    if re.search(r"\b(sell|sold|sale)\b", text, re.I):
        return "SELL"
    return {"B": "BUY", "S": "SELL"}.get(text.strip().upper())


def instrument_type(text: str, profile: MarketProfile | None) -> str | None:
    for pattern, code in profile.instrument_patterns if profile else ():
        if re.search(pattern, text, re.I):
            return code
    return None


def normalize_enum(path: str, text: str, profile: MarketProfile | None = None) -> str | None:
    """Raw slip text -> enum value (docs/03_lld.md §3.9 enum table); None when not recognised."""
    field = path.removeprefix("repo.")
    if field == "buy_sell":
        return buy_sell(text)
    if field == "deal_type":
        return next((v for rx, v in _DEAL_TYPES if re.search(rx, text, re.I)), None)
    if field == "day_count":
        return next((v for rx, v in _DAY_COUNTS if re.search(rx, text, re.I)), None)
    if field == "bid_type":
        if re.search(r"non[\s-]*competitive|\bncb\b", text, re.I):
            return "NON_COMPETITIVE"
        return "COMPETITIVE" if re.search(r"competitive", text, re.I) else None
    if field == "instrument_type":
        return instrument_type(text, profile)
    return None

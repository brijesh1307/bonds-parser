"""Canonical field registry, enums, label synonyms and required-field rules.

Sources: docs/00_design_baseline.md §5, docs/03_lld.md §2.1 and §3.9,
docs/05_data_dictionary_and_outputs.md §1–§3, docs/deal_slip_standard_formats.md §4 (label synonyms).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

# --------------------------------------------------------------------------- enums


class SlipStatus(StrEnum):
    PARSED = "PARSED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NEW_TEMPLATE = "NEW_TEMPLATE"
    APPROVED = "APPROVED"
    UNREADABLE = "UNREADABLE"
    FAILED = "FAILED"


class DealType(StrEnum):
    OUTRIGHT = "OUTRIGHT"
    PRIMARY_AUCTION = "PRIMARY_AUCTION"
    PRIMARY_PLACEMENT = "PRIMARY_PLACEMENT"
    REPO = "REPO"
    REVERSE_REPO = "REVERSE_REPO"
    TREPS_BORROW = "TREPS_BORROW"
    TREPS_LEND = "TREPS_LEND"
    LAF = "LAF"


class InstrumentType(StrEnum):
    GSEC = "GSEC"
    SDL = "SDL"
    TBILL = "TBILL"
    CMB = "CMB"
    CORPORATE_BOND = "CORPORATE_BOND"
    PSU_BOND = "PSU_BOND"
    CP = "CP"
    CD = "CD"
    UST = "UST"
    GILT = "GILT"
    BUND = "BUND"
    JGB = "JGB"
    EUROBOND = "EUROBOND"


class BuySell(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class DayCount(StrEnum):
    D30_360 = "30/360"
    D30E_360 = "30E/360"
    ACT_ACT = "ACT/ACT"
    ACT_365 = "ACT/365"
    ACT_364 = "ACT/364"
    ACT_360 = "ACT/360"


class BidType(StrEnum):
    COMPETITIVE = "COMPETITIVE"
    NON_COMPETITIVE = "NON_COMPETITIVE"


class Market(StrEnum):
    IN = "IN"
    US = "US"
    GB = "GB"
    DE = "DE"
    JP = "JP"
    INTL = "INTL"
    UNKNOWN = "UNKNOWN"


class Method(StrEnum):
    template = "template"
    region = "region"
    constant = "constant"
    regex = "regex"
    synonym = "synonym"
    abbreviation = "abbreviation"
    derived = "derived"
    fuzzy = "fuzzy"
    text = "text"


class Source(StrEnum):
    table = "table"
    grid = "grid"
    text = "text"
    prose = "prose"


class Severity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


# Confidence per method (baseline §4). Fuzzy = ratio × 0.85, so it never reaches the 0.90 threshold.
METHOD_SCORE: dict[Method, float] = {
    Method.template: 1.00,
    Method.region: 1.00,
    Method.constant: 1.00,
    Method.regex: 0.95,
    Method.synonym: 0.95,
    Method.abbreviation: 0.90,
    Method.derived: 0.90,
    Method.text: 0.80,
}
FUZZY_FACTOR = 0.85
ACCEPTED_DERIVED_SCORE = 0.95
IGNORE = "_ignore"

REPO_DEAL_TYPES = frozenset({DealType.REPO, DealType.REVERSE_REPO})
DISCOUNT_INSTRUMENTS = frozenset(
    {InstrumentType.TBILL, InstrumentType.CMB, InstrumentType.CP, InstrumentType.CD}
)

# --------------------------------------------------------------------------- field registry

FieldType = Literal["str", "enum", "date", "decimal", "int", "float", "bool"]
DecimalKind = Literal["nominal", "cash", "price", "yield", "rate", "fx"]


@dataclass(frozen=True, slots=True)
class FieldSpec:
    path: str
    type: FieldType
    kind: DecimalKind | None = None
    enum: tuple[str, ...] | None = None
    description: str = ""
    internal: bool = False  # working field: mappable, used by derivation, never in the deal output


def _enum(e: type[StrEnum]) -> tuple[str, ...]:
    return tuple(m.value for m in e)


_TOP: list[FieldSpec] = [
    FieldSpec("deal_id", "str", description="Deal / ticket / reference number"),
    FieldSpec("deal_type", "enum", enum=_enum(DealType), description="Kind of transaction"),
    FieldSpec("instrument_type", "enum", enum=_enum(InstrumentType), description="Instrument class"),
    FieldSpec("buy_sell", "enum", enum=_enum(BuySell), description="Our direction"),
    FieldSpec("platform", "str", description="Execution / reporting venue"),
    FieldSpec("trade_date", "date", description="Trade / deal / auction date"),
    FieldSpec("settlement_date", "date", description="Settlement / value date (repo: leg 1 date)"),
    FieldSpec("security_name", "str", description="Security description"),
    FieldSpec("issuer", "str", description="Issuer"),
    FieldSpec("credit_rating", "str", description="Credit rating with agency"),
    FieldSpec("isin", "str", description="ISIN (ISO 6166)"),
    FieldSpec("coupon_rate", "decimal", kind="rate", description="Coupon, % p.a."),
    FieldSpec("coupon_frequency", "int", description="Coupon payments per year"),
    FieldSpec("maturity_date", "date", description="Maturity / redemption date"),
    FieldSpec("last_coupon_date", "date", description="Last coupon paid before settlement"),
    FieldSpec("day_count", "enum", enum=_enum(DayCount), description="Accrual basis"),
    FieldSpec("tenor_days", "int", description="Tenor in days (discount instruments)"),
    FieldSpec("face_value", "decimal", kind="nominal", description="Total face value"),
    FieldSpec("face_value_per_unit", "decimal", kind="nominal", description="Face value per bond / unit"),
    FieldSpec("quantity", "int", description="Number of bonds / units"),
    FieldSpec("price", "decimal", kind="price", description="Clean price per 100 (repo: leg 1)"),
    FieldSpec("yield", "decimal", kind="yield", description="YTM / cut-off yield, %"),
    FieldSpec("principal_amount", "decimal", kind="cash", description="Face value x price / 100"),
    FieldSpec("accrued_days", "int", description="Accrued interest days"),
    FieldSpec("accrued_interest", "decimal", kind="cash", description="Accrued interest"),
    FieldSpec("discount_amount", "decimal", kind="cash", description="Discount (discount instruments)"),
    FieldSpec("consideration", "decimal", kind="cash", description="Net settlement amount (repo: leg 1)"),
    FieldSpec("stamp_duty", "decimal", kind="cash", description="Stamp duty on the trade"),
    FieldSpec("settlement_reference", "str", description="Clearing settlement number (not unique per deal)"),
    FieldSpec("currency", "str", description="Deal currency (ISO 4217)"),
    FieldSpec("settlement_currency", "str", description="Settlement currency if different"),
    FieldSpec("fx_rate", "decimal", kind="fx", description="FX rate deal -> settlement currency"),
    FieldSpec("counterparty", "str", description="Counterparty"),
    FieldSpec("counterparty_pan", "str", description="Counterparty PAN (personal data; masked in audit)"),
    FieldSpec("is_market_linked", "bool", description="Market Linked Debenture flag"),
    FieldSpec("broker", "str", description="Broker (Direct / No Broker -> null)"),
    FieldSpec("settlement_mode", "str", description="Settlement mode, e.g. DVP-III"),
    FieldSpec("portfolio", "str", description="Portfolio / book / category"),
    FieldSpec("dealer", "str", description="Dealer"),
    FieldSpec("bid_type", "enum", enum=_enum(BidType), description="Auction bid type"),
    FieldSpec("bid_amount", "decimal", kind="nominal", description="Face value bid in the auction"),
    FieldSpec("issuer_country", "str", description="Issuer country from ISIN prefix"),
    FieldSpec("market", "enum", enum=_enum(Market), description="Detected market"),
    FieldSpec("slip_locale", "str", description="Locale of the slip's formatting"),
    FieldSpec("market_confidence", "float", description="Share of the market vote won"),
]

_IDENTIFIERS: list[FieldSpec] = [
    FieldSpec("identifiers.isin", "str", description="ISIN"),
    FieldSpec("identifiers.cusip", "str", description="CUSIP"),
    FieldSpec("identifiers.sedol", "str", description="SEDOL"),
    FieldSpec("identifiers.common_code", "str", description="Euroclear / Clearstream common code"),
]

_REPO: list[FieldSpec] = [
    FieldSpec("repo.repo_rate", "decimal", kind="rate", description="Repo rate, % p.a."),
    FieldSpec("repo.repo_days", "int", description="Repo period in days"),
    FieldSpec("repo.day_count", "enum", enum=_enum(DayCount), description="Basis for repo interest"),
    FieldSpec("repo.haircut", "decimal", kind="rate", description="Haircut / margin, %"),
    FieldSpec("repo.leg1_date", "date", description="First leg date"),
    FieldSpec("repo.leg1_price", "decimal", kind="price", description="First leg clean price"),
    FieldSpec("repo.leg1_accrued_days", "int", description="Accrued days at leg 1"),
    FieldSpec("repo.leg1_accrued_interest", "decimal", kind="cash", description="Accrued interest at leg 1"),
    FieldSpec("repo.leg1_amount", "decimal", kind="cash", description="First leg settlement amount"),
    FieldSpec("repo.leg2_date", "date", description="Second leg date"),
    FieldSpec("repo.leg2_price", "decimal", kind="price", description="Second leg clean price"),
    FieldSpec("repo.leg2_accrued_days", "int", description="Accrued days at leg 2"),
    FieldSpec("repo.leg2_accrued_interest", "decimal", kind="cash", description="Accrued interest at leg 2"),
    FieldSpec("repo.leg2_amount", "decimal", kind="cash", description="Second leg settlement amount"),
    FieldSpec("repo.repo_interest", "decimal", kind="cash", description="Leg 2 amount - leg 1 amount"),
]

# Working fields for slips that print both sides (e.g. client confirmation letters). Derivation
# picks 'our' side from buy_sell: BUY -> consideration = buyer amount, counterparty_pan = seller PAN.
_INTERNAL: list[FieldSpec] = [
    FieldSpec(
        "_seller_amount", "decimal", kind="cash", description="Seller settlement amount", internal=True
    ),
    FieldSpec("_buyer_amount", "decimal", kind="cash", description="Buyer settlement amount", internal=True),
    FieldSpec("_seller_pan", "str", description="Seller PAN", internal=True),
    FieldSpec("_buyer_pan", "str", description="Buyer PAN", internal=True),
    FieldSpec("_seller_name", "str", description="Seller name", internal=True),
    FieldSpec("_buyer_name", "str", description="Buyer name", internal=True),
]

FIELD_SPECS: dict[str, FieldSpec] = {f.path: f for f in (*_TOP, *_IDENTIFIERS, *_REPO, *_INTERNAL)}
TOP_LEVEL_FIELDS: tuple[str, ...] = tuple(f.path for f in _TOP)
REPO_FIELDS: tuple[str, ...] = tuple(f.path.removeprefix("repo.") for f in _REPO)
IDENTIFIER_FIELDS: tuple[str, ...] = tuple(f.path.removeprefix("identifiers.") for f in _IDENTIFIERS)

# Market fields come from market detection, never from a slip label.
MARKET_FIELDS = frozenset({"issuer_country", "market", "slip_locale", "market_confidence"})

BASE_REQUIRED: tuple[str, ...] = (  # deal_id is optional (baseline §5)
    "deal_type",
    "trade_date",
    "settlement_date",
    "security_name",
    "isin",
    "face_value",
    "consideration",
)
_REPO_REQUIRED = ("repo.repo_rate", "repo.leg1_amount", "repo.leg2_date", "repo.leg2_amount")
REQUIRED_BY_TYPE: dict[str, tuple[str, ...]] = {
    DealType.OUTRIGHT: ("buy_sell", "price"),
    DealType.PRIMARY_AUCTION: ("price",),
    DealType.REPO: _REPO_REQUIRED,
    DealType.REVERSE_REPO: _REPO_REQUIRED,
}


def required_fields(deal_type: str | None) -> list[str]:
    return [*BASE_REQUIRED, *REQUIRED_BY_TYPE.get(deal_type or "", ())]


def is_valid_field_path(path: str) -> bool:
    """A path a mapping may target: any canonical field except the market fields."""
    return path in FIELD_SPECS and path not in MARKET_FIELDS and not path.startswith("identifiers.")


def empty_deal() -> dict[str, Any]:
    deal: dict[str, Any] = dict.fromkeys(TOP_LEVEL_FIELDS)
    deal["identifiers"] = dict.fromkeys(IDENTIFIER_FIELDS)
    deal["repo"] = None
    return deal


# --------------------------------------------------------------------------- labels


def normalise_label(label: str) -> tuple[str, str]:
    """(full, short) lookup keys; short drops parenthesised text (docs/03_lld.md §4.4)."""
    s = unicodedata.normalize("NFKC", label).lower().strip()
    full = " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())
    short = " ".join(re.sub(r"[^a-z0-9]+", " ", re.sub(r"\([^)]*\)", " ", s)).split())
    return full, (short or full)


# Label synonyms: docs/deal_slip_standard_formats.md §4, extended with docs/05 §1 "typical labels".
_SYNONYM_LABELS: dict[str, tuple[str, ...]] = {
    "deal_id": (
        "Deal Reference No",
        "Deal Ref No",
        "Deal ID",
        "Deal No",
        "Deal Number",
        "Ticket No",
        "Ticket Number",
        "Deal Ticket No",
        "Ref",
        "Ref No",
        "Reference No",
        "Reference Number",
        "Trade ID",
        "Trade Ref",
        "Trade Reference",
        "Confirmation No",
        "Order No",
        "Transaction ID",
        "Contract No",
    ),
    "deal_type": (
        "Deal Type",
        "Transaction",
        "Transaction Type",
        "Trade Type",
        "Nature of Deal",
        "Type of Deal",
        "Deal Nature",
        "Deal Type & Direction",
    ),
    "buy_sell": ("Buy / Sell", "Buy Sell", "Side", "Purchase / Sale", "B/S", "Direction"),
    "instrument_type": (
        "Security Type",
        "Instrument Type",
        "Product",
        "Product Type",
        "Asset Class",
        "Instrument Category",
        "Type of Instrument",
        "Type of Security",
    ),
    "platform": (
        "Platform",
        "Trading Platform",
        "Exchange",
        "Venue",
        "Execution Venue",
        "Trading System",
        "Market Type",
    ),
    "trade_date": (
        "Trade Date",
        "Deal Date",
        "Deal Date / Time",
        "Trade Date / Time",
        "Auction Date",
        "Transaction Date",
        "Date of Deal",
        "Date of Trade",
        "Dealing Date",
    ),
    "settlement_date": (
        "Settlement Date",
        "Value Date",
        "Date of Issue / Settlement",
        "Issue Date",
        "Pay-in Date",
        "Date of Settlement",
        "Sett Date",
        "Settlement / Value Date",
    ),
    "security_name": (
        "Security Description",
        "Security Name",
        "Security",
        "Collateral Security",
        "Scrip Name",
        "Scrip",
        "Instrument",
        "Instrument Name",
        "Bond Name",
        "Name of Security",
        "Description",
    ),
    "issuer": ("Issuer", "Issuer Name", "Name of Issuer"),
    "credit_rating": ("Rating", "Credit Rating"),
    "isin": ("ISIN", "ISIN Code", "ISIN No", "ISIN Number", "Security ISIN"),
    "coupon_rate": ("Coupon Rate", "Coupon", "Interest Rate", "Rate of Interest", "Interest / Coupon Rate"),
    "coupon_frequency": ("Coupon Frequency", "Interest Frequency", "Frequency", "Payment Frequency"),
    "maturity_date": ("Maturity Date", "Maturity", "Date of Maturity", "Redemption Date", "Final Maturity"),
    "last_coupon_date": (
        "Last Coupon Date",
        "Last Interest Paid On",
        "Previous Coupon Date",
        "Last Interest Date",
        "Last IP Date",
    ),
    "day_count": ("Day Count", "Day Count Convention", "Day Count Basis", "Accrual Basis", "Basis"),
    "tenor_days": ("Tenor", "Tenor Days", "Tenure", "Days to Maturity"),
    "face_value": (
        "Face Value",
        "Total Face Value",
        "Amount Allotted",
        "FV Amount",
        "Nominal",
        "Nominal Amount",
        "Face Value of Collateral",
        "FV",
        "Face Amount",
        "Par Value",
        "Quantum",
        "Total Quantum",
    ),
    "face_value_per_unit": (
        "FV per Bond",
        "Face Value per Bond",
        "Face Value per Unit",
        "FV per Unit",
        "Denomination",
    ),
    "quantity": (
        "No. of Bonds",
        "Quantity",
        "Qty",
        "Units",
        "No. of Units",
        "No. of Securities",
        "Number of Bonds",
        "No. of NCDs",
        "No. of NCD",
        "No. of Debentures",
    ),
    "price": (
        "Clean Price",
        "Price",
        "Deal Price",
        "Trade Price",
        "Cut-off Price",
        "Rate per 100",
        "Rate (per 100)",
    ),
    "yield": ("YTM", "YTM %", "Yield to Maturity", "Yield", "Cut-off Yield", "Deal Yield"),
    "principal_amount": (
        "Principal Amount",
        "Principal",
        "Clean Consideration",
        "Deal Amount",
        "Trade Value",
    ),
    "accrued_days": (
        "Accrued Interest Days",
        "Interest Days",
        "Accr Days",
        "Accrued Days",
        "Broken Period Days",
    ),
    "accrued_interest": ("Accrued Interest", "Accrued Int.", "Broken Period Interest", "Interest Amount"),
    "discount_amount": ("Discount Amount", "Discount"),
    "stamp_duty": ("Stamp Duty", "Stamp Duty Amount", "Stamp Duty to be borne by Buyer"),
    "settlement_reference": ("Settlement No", "Settlement No.", "Settlement Number"),
    "_seller_amount": ("Seller Settlement Amount", "Seller Amount", "Amount Receivable by Seller"),
    "_buyer_amount": ("Buyer Settlement Amount", "Buyer Amount", "Amount Payable by Buyer"),
    "_seller_pan": ("Seller PAN No", "Seller PAN", "PAN of Seller"),
    "_buyer_pan": ("Buyer PAN No", "Buyer PAN", "PAN of Buyer"),
    "_seller_name": ("Seller Name", "Seller"),
    "_buyer_name": ("Buyer Name", "Buyer"),
    "consideration": (
        "Total Consideration",
        "Net Consideration",
        "Consideration",
        "Amount Payable",
        "Amount Receivable",
        "Settlement Amount",
        "Net Amount",
        "Net Settlement Amount",
        "Total Amount",
        "Dirty Consideration",
    ),
    "currency": ("Currency", "CCY"),
    "settlement_currency": ("Settlement Currency",),
    "fx_rate": ("FX Rate", "Exchange Rate"),
    "counterparty": (
        "Counterparty",
        "Counter Party",
        "Counterparty Name",
        "Party Name",
        "Client",
        "Client Name",
        "Cpty",
        "Buyer / Seller",
    ),
    "broker": ("Broker", "Broker Name", "Through Broker"),
    "settlement_mode": (
        "Settlement Mode",
        "Settlement",
        "Mode of Settlement",
        "Settlement Type",
        "Settlement Through",
        "Clearing",
    ),
    "portfolio": (
        "Portfolio",
        "Book",
        "Portfolio / Book",
        "Book Name",
        "Category",
        "Investment Category",
        "Portfolio Category",
    ),
    "dealer": ("Dealer", "Dealer Name", "Trader", "Dealt By"),
    "bid_type": ("Bid Type", "Type of Bid", "Bid Category"),
    "bid_amount": ("Amount Bid", "Bid Amount", "Face Value Bid"),
    "repo.repo_rate": ("Repo Rate", "Repo Interest Rate"),
    "repo.repo_days": ("Repo Period", "Repo Period (Days)", "Repo Days", "Repo Tenor", "No. of Days"),
    "repo.day_count": ("Day Count (Repo Interest)", "Repo Day Count"),
    "repo.haircut": ("Haircut", "Margin"),
    "repo.repo_interest": ("Repo Interest", "Repo Interest Amount", "Interest on Repo"),
}

SYNONYMS: dict[str, str] = {
    normalise_label(label)[0]: path for path, labels in _SYNONYM_LABELS.items() for label in labels
}

# Multi-row grid keys are "<row label> <column header>" (docs/03_lld.md §4.3).
LEG_PREFIXES: dict[str, str] = {
    **dict.fromkeys(("first leg", "1st leg", "leg 1", "leg i", "near leg", "opening leg"), "repo.leg1_"),
    **dict.fromkeys(("second leg", "2nd leg", "leg 2", "leg ii", "far leg", "closing leg"), "repo.leg2_"),
}
LEG_SUFFIXES: dict[str, str] = {
    "date": "date",
    "settlement date": "date",
    "value date": "date",
    "price": "price",
    "clean price": "price",
    "accr days": "accrued_days",
    "accrued days": "accrued_days",
    "interest days": "accrued_days",
    "accrued int": "accrued_interest",
    "accrued interest": "accrued_interest",
    "amount": "amount",
    "settlement amount": "amount",
    "consideration": "amount",
    "settlement consideration": "amount",
}


ABBREVIATIONS: dict[str, str] = {
    "dt": "date",
    "stl": "settlement",
    "sett": "settlement",
    "settl": "settlement",
    "amt": "amount",
    "qty": "quantity",
    "cpty": "counterparty",
    "accr": "accrued",
    "int": "interest",
    "mat": "maturity",
    "cons": "consideration",
    "yld": "yield",
    "px": "price",
    "ref": "reference",
    "desc": "description",
    "sec": "security",
    "nbr": "no",
    "num": "no",
    "number": "no",
}


def expand_abbreviations(normalised: str) -> str:
    return " ".join(ABBREVIATIONS.get(tok, tok) for tok in normalised.split())


def leg_lookup(normalised: str) -> str | None:
    """'first leg settlement amount' -> 'repo.leg1_amount'."""
    for prefix, target in LEG_PREFIXES.items():
        if normalised.startswith(prefix + " "):
            suffix = LEG_SUFFIXES.get(normalised[len(prefix) + 1 :])
            return target + suffix if suffix else None
    return None

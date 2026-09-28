"""Derivation rules: fill fields the slip implies but does not label (docs/03_lld.md §4.12).

Rules only fill empty fields (never overwrite a mapped value). Derived values carry
method `derived` (0.90) or `text` (0.80, when read from the full slip text).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from app.engine.fields import DISCOUNT_INSTRUMENTS, METHOD_SCORE, REPO_DEAL_TYPES, Method
from app.engine.models import Candidate, ExtractedDocument, FieldValue, MarketProfile
from app.engine.normalize import (
    buy_sell,
    canonical_platform,
    instrument_type,
    normalize_enum,
    parse_date,
    scale,
    settlement_mode,
)


@dataclass(slots=True)
class Ctx:
    fields: dict[str, FieldValue]
    doc: ExtractedDocument
    profile: MarketProfile | None

    def value(self, path: str) -> Any:
        fv = self.fields.get(path)
        return fv.value if fv else None

    def raw(self, path: str) -> str:
        fv = self.fields.get(path)
        return fv.raw or "" if fv else ""

    def fill(
        self, path: str, value: Any, rule: str, *, source: str | None = None, text: bool = False
    ) -> None:
        if value is None or self.value(path) is not None:
            return
        src = self.fields.get(source) if source else None
        method = Method.text if text else Method.derived
        self.fields[path] = FieldValue(
            value=value,
            raw=src.raw if src else None,
            method=method,
            confidence=METHOD_SCORE[method],
            label=src.label if src else f"derived:{rule}",
            page=src.page if src else None,
            bbox=src.bbox if src else None,
        )


# --------------------------------------------------------------------------- pre-resolution


def reassign_per_unit_face_value(cands: dict[str, list[Candidate]], normalised: dict[int, Any]) -> None:
    """'FACE VALUE' printed per bond next to a total ('QUANTUM'): quantity x smaller = larger.

    Moves the per-unit candidate to face_value_per_unit so face_value keeps the total.
    """
    fv = [c for c in cands.get("face_value", []) if isinstance(normalised.get(id(c)), Decimal)]
    qty = [normalised.get(id(c)) for c in cands.get("quantity", []) if isinstance(normalised.get(id(c)), int)]
    if len(fv) < 2 or not qty:
        return
    for small in fv:
        for big in fv:
            if small is not big and normalised[id(small)] * qty[0] == normalised[id(big)]:
                cands["face_value"].remove(small)
                if not cands.get("face_value_per_unit"):
                    moved = Candidate(
                        "face_value_per_unit", small.kv, small.raw, small.method, small.confidence
                    )
                    normalised[id(moved)] = normalised[id(small)]
                    cands["face_value_per_unit"] = [moved]
                return


# --------------------------------------------------------------------------- rules

_OUR_BUY = re.compile(r"\bour\s+(?:buy|purchase)\s+from\s*:?-?\s*(?P<name>.+)", re.I)
_OUR_SELL = re.compile(r"\bour\s+(?:sell|sale)\s+to\s*:?-?\s*(?P<name>.+)", re.I)
_DEFAULT_SIDE = {
    "REVERSE_REPO": "BUY",
    "PRIMARY_AUCTION": "BUY",
    "PRIMARY_PLACEMENT": "BUY",
    "TREPS_LEND": "BUY",
    "REPO": "SELL",
    "TREPS_BORROW": "SELL",
}


def derive(fields: dict[str, FieldValue], doc: ExtractedDocument, profile: MarketProfile | None) -> None:
    c = Ctx(fields, doc, profile)
    text = doc.text
    deal_raw = c.raw("deal_type")

    # D1 deal type from the slip text when no label gave one
    c.fill("deal_type", normalize_enum("deal_type", text), "deal_type_text", text=True)
    deal_type = c.value("deal_type")

    # D2 direction: printed words first, then the deal type's usual side
    c.fill("buy_sell", buy_sell(deal_raw) if deal_raw else None, "buy_sell", source="deal_type")
    c.fill("buy_sell", _DEFAULT_SIDE.get(deal_type or ""), "buy_sell_default")
    side = c.value("buy_sell")

    # Counterparty named in the deal type: "Our Buy from:- X" / "Our Sell to:- X"
    if m := (_OUR_BUY.search(deal_raw) or _OUR_SELL.search(deal_raw)):
        c.fill("counterparty", " ".join(m["name"].split()), "counterparty_from_deal_type", source="deal_type")

    # Both sides printed: pick ours (baseline §5 decision for client confirmation letters)
    seller, buyer = c.value("_seller_amount"), c.value("_buyer_amount")
    if side == "BUY":
        c.fill("consideration", buyer, "our_side_amount", source="_buyer_amount")
        c.fill("counterparty_pan", c.value("_seller_pan"), "counterparty_pan", source="_seller_pan")
        c.fill("counterparty", c.value("_seller_name"), "counterparty_name", source="_seller_name")
    elif side == "SELL":
        c.fill("consideration", seller, "our_side_amount", source="_seller_amount")
        c.fill("counterparty_pan", c.value("_buyer_pan"), "counterparty_pan", source="_buyer_pan")
        c.fill("counterparty", c.value("_buyer_name"), "counterparty_name", source="_buyer_name")
    c.fill("principal_amount", seller, "principal_from_seller_amount", source="_seller_amount")
    if seller is not None and buyer is not None and buyer > seller:
        c.fill("stamp_duty", scale(buyer - seller, "cash"), "stamp_duty_from_amounts")

    # D3 instrument type: security type text, then security name, then ISIN prefix
    name = c.value("security_name") or ""
    c.fill(
        "instrument_type",
        instrument_type(name, profile) if name else None,
        "instrument_from_name",
        source="security_name",
    )
    isin = c.value("isin") or ""
    c.fill(
        "instrument_type",
        "CORPORATE_BOND" if isin.startswith("INE") else None,
        "instrument_from_isin",
        source="isin",
    )
    instrument = c.value("instrument_type")
    if re.search(r"market[\s-]+linked|\bMLD\b", f"{c.raw('instrument_type')} {name}", re.I):
        c.fill("is_market_linked", True, "market_linked", source="instrument_type")

    # D4 platform: deal type text, then the whole slip
    c.fill("platform", canonical_platform(deal_raw, profile), "platform_from_deal_type", source="deal_type")
    c.fill("platform", canonical_platform(text, profile), "platform_from_text", text=True)

    # D5 / D6 coupon rate and frequency
    if m := re.match(r"\s*(\d+(?:\.\d+)?)\s*%", name):
        c.fill("coupon_rate", scale(Decimal(m.group(1)), "rate"), "coupon_from_name", source="security_name")
    coupon_raw = c.raw("coupon_rate").upper()
    freq = None
    if re.search(r"SEMI|HALF[\s-]*YEAR", coupon_raw):
        freq = 2
    elif "QUARTER" in coupon_raw:
        freq = 4
    elif "MONTHLY" in coupon_raw:
        freq = 12
    elif re.search(r"\bANNUAL\b|\bYEARLY\b", coupon_raw):
        freq = 1
    c.fill("coupon_frequency", freq, "coupon_frequency", source="coupon_rate")
    if (
        profile
        and instrument
        and instrument not in DISCOUNT_INSTRUMENTS
        and not c.value("is_market_linked")
        and c.value("coupon_rate") is not None
    ):
        c.fill(
            "coupon_frequency", profile.default_coupon_frequency.get(instrument), "coupon_frequency_default"
        )

    # D7 day count: "(30/360)" next to the accrued days, else the market default when accrual is shown
    c.fill(
        "day_count",
        normalize_enum("day_count", c.raw("accrued_days")),
        "day_count_from_accrued_days",
        source="accrued_days",
    )
    if (
        profile
        and instrument
        and deal_type not in REPO_DEAL_TYPES
        and (c.value("accrued_days") is not None or c.value("accrued_interest") is not None)
    ):
        c.fill("day_count", profile.default_day_count.get(instrument), "day_count_default")

    # D8 / D9 tenor and maturity of discount instruments
    if m := re.search(r"\b(\d{2,3})\s*DTB\b", name, re.I):
        c.fill("tenor_days", int(m.group(1)), "tenor_from_name", source="security_name")
    if m := re.search(r"\bDTB\s*(\d{8})\b", name, re.I):
        c.fill("maturity_date", parse_date(m.group(1), profile), "maturity_from_name", source="security_name")
    settle, maturity = c.value("settlement_date"), c.value("maturity_date")
    if instrument in DISCOUNT_INSTRUMENTS and isinstance(settle, date) and isinstance(maturity, date):
        c.fill("tenor_days", (maturity - settle).days, "tenor_from_dates")

    # D10 currency: "(INR)" / "(Rs.)" in labels, else the market's currency
    labels = " ".join(kv.key for kv in doc.pairs)
    if re.search(r"\bINR\b|\bRs\b\.?|₹", labels):
        c.fill("currency", "INR", "currency_from_labels")
    c.fill("currency", profile.currency if profile else None, "currency_from_market")

    # D11 settlement mode anywhere in the slip
    c.fill("settlement_mode", settlement_mode(text), "settlement_mode_from_text", text=True)

    # D12 primary auctions on E-Kuber are with the RBI
    if deal_type == "PRIMARY_AUCTION" and c.value("platform") == "RBI E-Kuber":
        c.fill("counterparty", "Reserve Bank of India", "auction_counterparty")

    # D13-D15 repo: days, interest, leg 1 copied to the top level
    d1, d2 = c.value("repo.leg1_date"), c.value("repo.leg2_date")
    if isinstance(d1, date) and isinstance(d2, date):
        c.fill("repo.repo_days", (d2 - d1).days, "repo_days")
    a1, a2 = c.value("repo.leg1_amount"), c.value("repo.leg2_amount")
    if a1 is not None and a2 is not None:
        c.fill("repo.repo_interest", scale(a2 - a1, "cash"), "repo_interest")
    if deal_type in REPO_DEAL_TYPES:
        for top, leg in (
            ("settlement_date", "repo.leg1_date"),
            ("price", "repo.leg1_price"),
            ("accrued_days", "repo.leg1_accrued_days"),
            ("accrued_interest", "repo.leg1_accrued_interest"),
            ("consideration", "repo.leg1_amount"),
            ("trade_date", "repo.leg1_date"),
        ):
            c.fill(top, c.value(leg), f"repo_{top}", source=leg)

    # D16 / D17 face value <-> quantity x face value per unit
    fv, per_unit, qty = c.value("face_value"), c.value("face_value_per_unit"), c.value("quantity")
    if per_unit is not None and qty is not None:
        c.fill("face_value", scale(per_unit * qty, "nominal"), "face_value_from_quantity")
    if fv is not None and per_unit:
        units = fv / per_unit
        if units == units.to_integral_value():
            c.fill("quantity", int(units), "quantity_from_face_value")

    # D20 T-Bill / CMB price from yield (ACT/364)
    yld, tenor = c.value("yield"), c.value("tenor_days")
    if instrument in ("TBILL", "CMB") and yld is not None and tenor:
        c.fill(
            "price",
            scale(Decimal(100) / (1 + yld / 100 * tenor / 364), "price").quantize(Decimal("0.0001")),
            "tbill_price_from_yield",
        )

    # D21 identifiers
    c.fill("identifiers.isin", c.value("isin"), "identifiers_isin", source="isin")

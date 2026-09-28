"""Market detection by a weighted vote of signals (docs/03_lld.md §4.9, docs/09_market_profiles.md §3).

Runs before normalisation: the market decides how dates and numbers are read.
"""

from __future__ import annotations

import re

from app.engine.fields import Market
from app.engine.models import ExtractedDocument, MarketDetection, MarketSignal
from app.engine.normalize import ISIN_RE, isin_check_digit_ok
from app.engine.profiles import get_profile

MARKET_THRESHOLD = 0.70  # baseline §4

_ISIN_PREFIX = {"IN": "IN", "US": "US", "GB": "GB", "DE": "DE", "JP": "JP", "XS": "INTL", "EU": "INTL"}

# (signal, regex, market, weight); each signal type votes at most once per market.
_SIGNALS: list[tuple[str, str, str, int]] = [
    ("currency", r"\bINR\b|\bRs\.?(?=[\s\d)/])|₹|\bRupees\b", "IN", 2),
    ("currency", r"\bUSD\b|US\$", "US", 2),
    ("currency", r"\bGBP\b|£", "GB", 2),
    ("currency", r"\bJPY\b|¥", "JP", 2),
    ("currency", r"\bEUR\b|€", "DE", 1),
    ("settlement_system", r"\b(CCIL|NSDL|CDSL|NCL|ICCL|CSGL|SGL)\b|NSE Clearing|RBI SGL", "IN", 2),
    ("settlement_system", r"\b(DTC|DTCC|Fedwire)\b", "US", 2),
    ("settlement_system", r"\bCREST\b", "GB", 2),
    ("settlement_system", r"\bEuroclear\b|Clearstream Banking S\.?A", "INTL", 2),
    ("settlement_system", r"\b(JASDEC|BOJ-NET)\b", "JP", 2),
    ("platform", r"NDS-?OM|\bCROMS\b|E-?Kuber|NSE\s*RFQ|BSE\s*RFQ|\bEBP\b|\bTREPS\b|\bICDM\b", "IN", 2),
    ("name_style", r"\d+(\.\d+)?%\s*GS\s*\d{4}|\bSDL\b|\d{2,3}\s*DTB\s*\d{8}|\bNCDs?\b", "IN", 1),
    ("name_style", r"\bUST\b|\bT-Note\b", "US", 1),
    ("name_style", r"\bGilt\b", "GB", 1),
    ("name_style", r"\bBund\b|\bDBR\b", "DE", 1),
    ("name_style", r"\bJGB\b", "JP", 1),
    ("number_format", r"(?<![\d,])\d{1,2}(,\d{2})+,\d{3}(\.\d+)?\b", "IN", 1),
    ("number_format", r"(?<![\d.])\d{1,3}(\.\d{3})+,\d{2}\b", "DE", 1),
]


def isin_country(isin: str) -> str | None:
    prefix = isin[:2]
    return None if prefix in ("XS", "EU") else prefix


def detect_market(doc: ExtractedDocument) -> MarketDetection:
    text = doc.text + "\n" + "\n".join(f"{kv.key} {kv.value}" for kv in doc.pairs)
    signals: list[MarketSignal] = []
    seen: set[tuple[str, str]] = set()

    isins = [m for m in dict.fromkeys(re.findall(ISIN_RE.pattern, text.upper())) if isin_check_digit_ok(m)]
    for isin in isins:
        market = _ISIN_PREFIX.get(isin[:2])
        if market and ("isin_prefix", market) not in seen:
            seen.add(("isin_prefix", market))
            signals.append(MarketSignal("isin_prefix", isin, market, 3))

    for signal, pattern, market, weight in _SIGNALS:
        if (signal, market) in seen:
            continue
        if m := re.search(pattern, text, re.IGNORECASE if signal != "number_format" else 0):
            seen.add((signal, market))
            signals.append(MarketSignal(signal, m.group(0), market, weight))

    totals: dict[str, int] = {}
    for s in signals:
        totals[s.market] = totals.get(s.market, 0) + s.weight
    if not totals:
        return MarketDetection(Market.UNKNOWN, None, None, 0.0, signals, None)

    winner = max(totals, key=lambda k: totals[k])
    confidence = round(totals[winner] / sum(totals.values()), 2)
    market = winner if confidence >= MARKET_THRESHOLD else Market.UNKNOWN
    profile = get_profile(market)
    issuer = next((c for isin in isins if (c := isin_country(isin))), None)
    return MarketDetection(
        market=market,
        issuer_country=issuer,
        slip_locale=profile.locale if profile else None,
        market_confidence=confidence,
        signals=signals,
        profile=profile,
    )

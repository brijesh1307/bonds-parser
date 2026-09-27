"""Orchestrates the parse of one PDF (docs/03_lld.md §3.9 `run()`, §4.15 status decision).

PH1 steps: extract -> map by synonym -> normalise -> resolve -> build deal -> required check
-> status. Market detection and derivation/validation (PH2) and templates (PH3) plug into
the marked steps.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.config import EngineSettings
from app.engine.extract import extract
from app.engine.fields import (
    REPO_DEAL_TYPES,
    REPO_FIELDS,
    TOP_LEVEL_FIELDS,
    Market,
    Severity,
    SlipStatus,
    empty_deal,
)
from app.engine.mapping import collect_candidates, resolve
from app.engine.models import (
    EngineResult,
    ExtractedDocument,
    FieldValue,
    Issue,
    MarketDetection,
    TemplateDefinition,
    TemplateMatchResult,
)
from app.engine.normalize import DocHints, NormalizationError, normalize_value
from app.engine.validate import low_confidence, missing_required


def _undetected_market() -> MarketDetection:
    # Market detection (vote over ISIN prefix, currency, settlement system, ...) arrives in PH2 (T2.3).
    return MarketDetection(Market.UNKNOWN, None, None, 0.0, [], None)


def build_deal(fields: dict[str, FieldValue], market: MarketDetection) -> dict[str, Any]:
    deal = empty_deal()
    repo: dict[str, Any] = {}
    for path, fv in fields.items():
        if path.startswith("repo."):
            repo[path.removeprefix("repo.")] = fv.value
        elif path.startswith("identifiers."):
            deal["identifiers"][path.removeprefix("identifiers.")] = fv.value
        elif path in TOP_LEVEL_FIELDS:
            deal[path] = fv.value
    if deal["deal_type"] in REPO_DEAL_TYPES:
        deal["repo"] = {name: repo.get(name) for name in REPO_FIELDS}
    deal["market"] = market.market
    deal["issuer_country"] = market.issuer_country
    deal["slip_locale"] = market.slip_locale
    deal["market_confidence"] = market.market_confidence
    return deal


def decide_status(
    *,
    doc: ExtractedDocument,
    match: TemplateMatchResult,
    market: MarketDetection,
    missing: list[str],
    issues: list[Issue],
    low_conf: list[str],
) -> SlipStatus:
    if not doc.has_text_layer:
        return SlipStatus.UNREADABLE
    if not match.matched:
        return SlipStatus.NEW_TEMPLATE
    if (
        missing
        or low_conf
        or any(i.severity is Severity.ERROR for i in issues)
        or market.profile is None
        or market.market == Market.UNKNOWN
    ):
        return SlipStatus.NEEDS_REVIEW
    return SlipStatus.PARSED


def run(
    pdf: Path | bytes,
    *,
    file_name: str,
    sha256: str,
    es: EngineSettings,
    templates: Sequence[TemplateDefinition] = (),
) -> EngineResult:
    doc = extract(pdf, max_pages=es.max_pages)
    market = _undetected_market()
    match = TemplateMatchResult(None, 0.0, es.template_match_threshold, matched=False)  # templates: PH3

    if not doc.has_text_layer:
        return EngineResult(
            status=SlipStatus.UNREADABLE,
            file_name=file_name,
            pages=doc.pages,
            page_sizes=doc.page_sizes,
            sha256=sha256,
            market=market,
            template=match,
            deal=build_deal({}, market),
            fields={},
            key_values=[],
            mapped_to={},
            missing_required=[],
            validation=[],
            low_confidence=[],
            unmapped=[],
        )

    cands, kv_map = collect_candidates(doc)
    hints = DocHints()
    issues: list[Issue] = []
    normalised: dict[int, Any] = {}
    errors: dict[int, NormalizationError] = {}
    for path, cs in cands.items():
        for c in cs:
            try:
                normalised[id(c)] = normalize_value(
                    path, c.raw, market.profile, hints, label=c.kv.key if c.kv else None
                )
            except NormalizationError as exc:
                normalised[id(c)] = None
                errors[id(c)] = exc

    resolved = resolve(cands, normalised)
    for path, cs in cands.items():
        if resolved[path].value is None:
            for c in cs:
                if (err := errors.get(id(c))) is not None:
                    issues.append(
                        Issue(err.code, Severity.ERROR, f"{c.kv.key if c.kv else path}: {err}", (path,))
                    )
                    break
    fields = {p: fv for p, fv in resolved.items() if fv.value is not None}

    # PH2: derivation rules and validation checks run here.
    deal = build_deal(fields, market)
    missing = missing_required(deal)
    low = low_confidence(fields, deal["deal_type"], es.confidence_threshold)
    status = decide_status(doc=doc, match=match, market=market, missing=missing, issues=issues, low_conf=low)

    return EngineResult(
        status=status,
        file_name=file_name,
        pages=doc.pages,
        page_sizes=doc.page_sizes,
        sha256=sha256,
        market=market,
        template=match,
        deal=deal,
        fields=fields,
        key_values=doc.pairs,
        mapped_to=kv_map,
        missing_required=missing,
        validation=issues,
        low_confidence=low,
        unmapped=[kv for i, kv in enumerate(doc.pairs) if i not in kv_map],
    )

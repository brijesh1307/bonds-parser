"""Orchestrates the parse of one PDF (docs/03_lld.md §3.9 `run()`, §4.15 status decision).

extract -> detect market -> map labels -> normalise (market profile) -> per-unit face value
-> resolve -> derive -> build deal -> validate -> required / confidence -> status.
Templates (PH3) plug in at the matching and mapping steps.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.config import EngineSettings
from app.engine.derive import derive, reassign_per_unit_face_value
from app.engine.extract import extract
from app.engine.fields import (
    FIELD_SPECS,
    REPO_DEAL_TYPES,
    REPO_FIELDS,
    TOP_LEVEL_FIELDS,
    Market,
    Severity,
    SlipStatus,
    empty_deal,
)
from app.engine.mapping import collect_candidates, resolve
from app.engine.market import detect_market
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
from app.engine.validate import low_confidence, missing_required, validate


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


def _market_issues(market: MarketDetection) -> list[Issue]:
    if market.market == Market.UNKNOWN:
        return [
            Issue(
                "MARKET_UNCERTAIN",
                Severity.WARNING,
                f"market could not be determined (confidence {market.market_confidence})",
            )
        ]
    if market.profile is None:
        return [
            Issue(
                "MARKET_PROFILE_UNAVAILABLE",
                Severity.WARNING,
                f"no active market profile for {market.market}; dates and amounts read with neutral rules",
            )
        ]
    return []


def run(
    pdf: Path | bytes,
    *,
    file_name: str,
    sha256: str,
    es: EngineSettings,
    templates: Sequence[TemplateDefinition] = (),
) -> EngineResult:
    doc = extract(pdf, max_pages=es.max_pages)
    match = TemplateMatchResult(None, 0.0, es.template_match_threshold, matched=False)  # templates: PH3

    if not doc.has_text_layer:
        unknown = MarketDetection(Market.UNKNOWN, None, None, 0.0, [], None)
        return EngineResult(
            SlipStatus.UNREADABLE, file_name, doc.pages, doc.page_sizes, sha256, unknown, match,
            build_deal({}, unknown), {}, [], {}, [], [], [], [],
        )  # fmt: skip

    market = detect_market(doc)
    profile = market.profile
    issues = _market_issues(market)

    cands, kv_map = collect_candidates(doc)
    hints = DocHints()
    normalised: dict[int, Any] = {}
    errors: dict[int, NormalizationError] = {}
    for path, cs in cands.items():
        for c in cs:
            label = c.kv.key if c.kv else None
            try:
                normalised[id(c)] = normalize_value(path, c.raw, profile, hints, label=label)
            except NormalizationError as exc:
                normalised[id(c)] = None
                errors[id(c)] = exc

    reassign_per_unit_face_value(cands, normalised)
    resolved, conflict_issues = resolve(cands, normalised)
    issues += conflict_issues
    for path, cs in cands.items():
        if path in resolved and resolved[path].value is None:  # nothing parsed: report the first error
            for c in cs:
                if (err := errors.get(id(c))) is not None:
                    where = c.kv.key if c.kv else path
                    issues.append(Issue(err.code, Severity.ERROR, f"{where}: {err}", (path,)))
                    break
    fields = {p: fv for p, fv in resolved.items() if fv.value is not None}

    derive(fields, doc, profile)
    deal = build_deal(fields, market)
    issues += validate(deal, profile)
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
        fields={p: fv for p, fv in fields.items() if not FIELD_SPECS[p].internal},
        key_values=doc.pairs,
        mapped_to=kv_map,
        missing_required=missing,
        validation=issues,
        low_confidence=low,
        unmapped=[kv for i, kv in enumerate(doc.pairs) if i not in kv_map],
    )

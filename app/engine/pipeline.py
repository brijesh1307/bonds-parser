"""Orchestrates the parse of one PDF (docs/03_lld.md §3.9 `run()`, §4.15 status decision).

extract -> detect market -> map labels -> normalise (market profile) -> per-unit face value
-> resolve -> derive -> build deal -> validate -> required / confidence -> status.
A matched (or draft) template adds its label map, rules, market / date-order pins and
accepted derived fields.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from app.config import EngineSettings
from app.engine.derive import derive, reassign_per_unit_face_value
from app.engine.detect import fingerprint, jaccard, match_template
from app.engine.extract import extract
from app.engine.fields import (
    ACCEPTED_DERIVED_SCORE,
    FIELD_SPECS,
    REPO_DEAL_TYPES,
    REPO_FIELDS,
    TOP_LEVEL_FIELDS,
    Market,
    Method,
    Severity,
    SlipStatus,
    empty_deal,
)
from app.engine.mapping import apply_rules, collect_candidates, resolve
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
from app.engine.profiles import get_profile
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


DraftBuilder = Callable[[ExtractedDocument, MarketDetection], TemplateDefinition]


def _pin_market(market: MarketDetection, template: TemplateDefinition) -> MarketDetection:
    """A template may pin the market of its layout when the vote is inconclusive."""
    if market.market != Market.UNKNOWN or template.market in ("", Market.UNKNOWN):
        return market
    profile = get_profile(template.market)
    return MarketDetection(
        template.market, market.issuer_country, profile.locale if profile else None, market.market_confidence,
        market.signals, profile,
    )  # fmt: skip


def run(
    pdf: Path | bytes,
    *,
    file_name: str,
    sha256: str,
    es: EngineSettings,
    templates: Sequence[TemplateDefinition] = (),
    draft: DraftBuilder | None = None,
) -> EngineResult:
    """Parse one PDF. `draft` builds a draft template from the extracted slip (preview / approve)."""
    doc = extract(pdf, max_pages=es.max_pages)

    if not doc.has_text_layer:
        unknown = MarketDetection(Market.UNKNOWN, None, None, 0.0, [], None)
        nomatch = TemplateMatchResult(None, 0.0, es.template_match_threshold, matched=False)
        return EngineResult(
            SlipStatus.UNREADABLE, file_name, doc.pages, doc.page_sizes, sha256, unknown, nomatch,
            build_deal({}, unknown), {}, [], {}, [], [], [], [],
        )  # fmt: skip

    market = detect_market(doc)
    if draft is not None:
        tpl: TemplateDefinition | None = draft(doc, market)
        assert tpl is not None  # noqa: S101 - the builder always returns a definition
        match = TemplateMatchResult(
            tpl,
            jaccard(fingerprint(doc), tpl.fingerprint_labels),
            tpl.match_threshold,
            matched=True,
            is_draft=True,
        )
    else:
        match = match_template(doc, templates, es.template_match_threshold)
        tpl = match.template if match.matched else None
    if tpl is not None:
        market = _pin_market(market, tpl)
    profile = market.profile
    issues = _market_issues(market)

    cands, kv_map, ignored = collect_candidates(doc, tpl)
    if tpl is not None:
        issues += apply_rules(doc, tpl, cands)
    hints = DocHints(date_order=tpl.date_order if tpl else None)
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
    if tpl is not None:  # the reviewer accepted these derived / text values for this layout
        for path in tpl.accepted_derived:
            fv = fields.get(path)
            if fv and fv.method in (Method.derived, Method.text, Method.fuzzy):
                fv.confidence = max(fv.confidence, ACCEPTED_DERIVED_SCORE)
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
        unmapped=[kv for i, kv in enumerate(doc.pairs) if i not in kv_map and i not in ignored],
    )

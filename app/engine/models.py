"""Engine data structures passed between pipeline steps (docs/03_lld.md §2.2).

Pure dataclasses: no FastAPI, no database. BBox = (x0, top, x1, bottom) in PDF points,
origin top-left (pdfplumber), rounded to 0.1. Pages are 1-based.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from app.engine.fields import Method, Severity, SlipStatus, Source

BBox = tuple[float, float, float, float]
Word = tuple[str, BBox]  # text, position


@dataclass(frozen=True, slots=True)
class KeyValue:
    """One extracted label/value candidate."""

    key: str
    value: str
    source: Source
    page: int
    key_bbox: BBox | None
    value_bbox: BBox | None


@dataclass(slots=True)
class ExtractedDocument:
    pairs: list[KeyValue]
    text: str  # full text layer, pages joined with "\f"
    pages: int
    page_sizes: list[tuple[float, float]]
    table_bboxes: list[list[BBox]]  # per page
    has_text_layer: bool
    words: list[list[Word]] = field(default_factory=list)  # per page, for template region rules


@dataclass(slots=True)
class FieldValue:
    """One resolved canonical field."""

    value: Any  # str | Decimal | int | date | None
    raw: str | None
    method: Method
    confidence: float
    label: str | None
    page: int | None
    bbox: BBox | None


@dataclass(slots=True)
class Candidate:
    """A possible value for a field, before resolution."""

    field: str
    kv: KeyValue | None
    raw: str
    method: Method
    confidence: float


@dataclass(frozen=True, slots=True)
class RegionRule:
    field: str
    page: int
    bbox: BBox


@dataclass(frozen=True, slots=True)
class RegexRule:
    field: str
    pattern: str
    group: int = 1


@dataclass(frozen=True, slots=True)
class TemplateDefinition:
    """= template_versions.definition_json (docs/00_design_baseline.md §6)."""

    template_id: str
    version: int
    market: str
    fingerprint_labels: frozenset[str]
    keywords: tuple[str, ...]
    match_threshold: float
    label_map: Mapping[str, str]
    region_rules: tuple[RegionRule, ...] = ()
    regex_rules: tuple[RegexRule, ...] = ()
    constants: Mapping[str, str] = field(default_factory=dict)
    ignore: frozenset[str] = frozenset()
    accepted_derived: frozenset[str] = frozenset()
    date_order: Literal["DMY", "MDY"] | None = None
    name: str | None = None

    @classmethod
    def from_json(cls, d: Mapping[str, Any], *, name: str | None = None) -> TemplateDefinition:
        """Baseline §6 JSON shape -> definition."""
        fp = d.get("fingerprint") or {}
        return cls(
            template_id=d["template_id"],
            version=int(d["version"]),
            market=d.get("market") or "UNKNOWN",
            fingerprint_labels=frozenset(fp.get("labels") or ()),
            keywords=tuple(fp.get("keywords") or ()),
            match_threshold=float(d.get("match_threshold") or 0.80),
            label_map=dict(d.get("label_map") or {}),
            region_rules=tuple(
                RegionRule(r["field"], int(r["page"]), tuple(float(x) for x in r["bbox"]))  # type: ignore[arg-type]
                for r in d.get("region_rules") or ()
            ),
            regex_rules=tuple(
                RegexRule(r["field"], r["pattern"], int(r.get("group", 1)))
                for r in d.get("regex_rules") or ()
            ),
            constants=dict(d.get("constants") or {}),
            ignore=frozenset(d.get("ignore") or ()),
            accepted_derived=frozenset(d.get("accepted_derived") or ()),
            date_order=d.get("date_order"),
            name=name,
        )

    def to_json(self) -> dict[str, Any]:
        """Definition -> baseline §6 JSON shape (sorted, so files diff cleanly)."""
        return {
            "template_id": self.template_id,
            "version": self.version,
            "market": self.market,
            "fingerprint": {"labels": sorted(self.fingerprint_labels), "keywords": list(self.keywords)},
            "match_threshold": self.match_threshold,
            "label_map": dict(sorted(self.label_map.items())),
            "region_rules": [
                {"field": r.field, "page": r.page, "bbox": list(r.bbox)} for r in self.region_rules
            ],
            "regex_rules": [
                {"field": r.field, "pattern": r.pattern, "group": r.group} for r in self.regex_rules
            ],
            "constants": dict(sorted(self.constants.items())),
            "ignore": sorted(self.ignore),
            "accepted_derived": sorted(self.accepted_derived),
            "date_order": self.date_order,
        }


@dataclass(frozen=True, slots=True)
class MarketProfile:
    code: str
    locale: str
    currency: str
    date_order: Literal["DMY", "MDY", "YMD"]
    amount_units: Mapping[str, int]
    weekend: frozenset[int] = frozenset({5, 6})
    holidays: frozenset[date] = frozenset()
    default_day_count: Mapping[str, str] = field(default_factory=dict)
    default_coupon_frequency: Mapping[str, int] = field(default_factory=dict)
    platforms: Mapping[str, str] = field(default_factory=dict)
    settlement_systems: tuple[str, ...] = ()
    instrument_patterns: tuple[tuple[str, str], ...] = ()
    tbill_basis: int = 364


@dataclass(frozen=True, slots=True)
class MarketSignal:
    signal: str
    value: str
    market: str
    weight: int


@dataclass(slots=True)
class MarketDetection:
    market: str
    issuer_country: str | None
    slip_locale: str | None
    market_confidence: float
    signals: list[MarketSignal]
    profile: MarketProfile | None


@dataclass(slots=True)
class TemplateMatchResult:
    template: TemplateDefinition | None
    score: float
    threshold: float
    matched: bool
    is_draft: bool = False


@dataclass(slots=True)
class Issue:
    """Engine-side validation issue."""

    code: str
    severity: Severity
    message: str
    fields: tuple[str, ...] = ()
    expected: str | None = None
    actual: str | None = None
    tolerance: str | None = None


@dataclass(slots=True)
class EngineResult:
    status: SlipStatus
    file_name: str
    pages: int
    page_sizes: list[tuple[float, float]]
    sha256: str
    market: MarketDetection
    template: TemplateMatchResult
    deal: dict[str, Any]
    fields: dict[str, FieldValue]
    key_values: list[KeyValue]
    mapped_to: dict[int, str]  # index in key_values -> field path
    missing_required: list[str]
    validation: list[Issue]
    low_confidence: list[str]
    unmapped: list[KeyValue]

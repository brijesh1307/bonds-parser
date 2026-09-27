"""Label -> canonical field lookup and candidate resolution (docs/03_lld.md §3.9, §4.5, §4.8).

PH1: built-in synonyms (incl. repo leg keys). Template maps (PH3), abbreviation expansion,
fuzzy matching and conflict detection (PH2) extend this module later.
"""

from __future__ import annotations

from typing import Any

from app.engine.fields import METHOD_SCORE, SYNONYMS, Method, Source, leg_lookup, normalise_label
from app.engine.models import Candidate, ExtractedDocument, FieldValue

_RANK = {
    Method.region: 1,
    Method.regex: 2,
    Method.template: 3,
    Method.constant: 4,
    Method.synonym: 5,
    Method.abbreviation: 6,
    Method.fuzzy: 7,
    Method.text: 8,
    Method.derived: 9,
}
_SOURCE_ORDER = {Source.table: 0, Source.grid: 1, Source.text: 2, Source.prose: 3}


def lookup_label(label: str) -> tuple[str, Method, float] | None:
    full, short = normalise_label(label)
    for key in (full, short):
        if key in SYNONYMS:
            return SYNONYMS[key], Method.synonym, METHOD_SCORE[Method.synonym]
        if leg := leg_lookup(key):
            return leg, Method.synonym, METHOD_SCORE[Method.synonym]
    return None


def collect_candidates(doc: ExtractedDocument) -> tuple[dict[str, list[Candidate]], dict[int, str]]:
    """Candidates per field path, and key_values index -> field path."""
    cands: dict[str, list[Candidate]] = {}
    kv_map: dict[int, str] = {}
    for i, kv in enumerate(doc.pairs):
        hit = lookup_label(kv.key)
        if hit is None:
            continue
        path, method, confidence = hit
        kv_map[i] = path
        cands.setdefault(path, []).append(Candidate(path, kv, kv.value, method, confidence))
    return cands, kv_map


def _sort_key(c: Candidate) -> tuple[Any, ...]:
    kv = c.kv
    position = (kv.page, kv.value_bbox[1], kv.value_bbox[0]) if kv and kv.value_bbox else (0, 0.0, 0.0)
    return (_RANK[c.method], -c.confidence, _SOURCE_ORDER[kv.source] if kv else 9, *position)


def resolve(cands: dict[str, list[Candidate]], normalised: dict[int, Any]) -> dict[str, FieldValue]:
    """Pick one candidate per field: parseable first, then method rank, confidence, source, position.

    `normalised` maps id(candidate) -> normalised value (None when it did not parse).
    """
    fields: dict[str, FieldValue] = {}
    for path, cs in cands.items():
        parseable = [c for c in cs if normalised.get(id(c)) is not None]
        win = sorted(parseable or cs, key=_sort_key)[0]
        kv = win.kv
        fields[path] = FieldValue(
            value=normalised.get(id(win)),
            raw=win.raw,
            method=win.method,
            confidence=win.confidence,
            label=kv.key if kv else None,
            page=kv.page if kv else None,
            bbox=kv.value_bbox if kv else None,
        )
    return fields

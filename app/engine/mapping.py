"""Label -> canonical field lookup and candidate resolution (docs/03_lld.md §3.9, §4.5, §4.8).

Lookup chain per label (full key first, then the key without parenthesised text):
  synonym / repo-leg key (0.95) -> abbreviation expansion (0.90) -> compound label split on "/"
  ('SECURITY NAME/ISIN NUMBER' -> security_name + isin, 0.95) -> fuzzy match (ratio x 0.85).
Prose candidates always count as method `text` (0.80). Templates (PH3) add rank-3 lookups.
"""

from __future__ import annotations

import difflib
from typing import Any

from app.engine.fields import (
    FUZZY_FACTOR,
    METHOD_SCORE,
    SYNONYMS,
    Method,
    Severity,
    Source,
    expand_abbreviations,
    leg_lookup,
    normalise_label,
)
from app.engine.models import Candidate, ExtractedDocument, FieldValue, Issue

Hit = tuple[str, Method, float]

RANK = {
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
_FUZZY_MIN_LEN = 6


def _exact(key: str) -> str | None:
    return SYNONYMS.get(key) or leg_lookup(key)


def _single(label: str, *, fuzzy: bool) -> Hit | None:
    full, short = normalise_label(label)
    keys = dict.fromkeys((full, short))
    for key in keys:
        if path := _exact(key):
            return path, Method.synonym, METHOD_SCORE[Method.synonym]
    for key in keys:
        expanded = expand_abbreviations(key)
        if expanded != key and (path := _exact(expanded)):
            return path, Method.abbreviation, METHOD_SCORE[Method.abbreviation]
    if fuzzy:
        for key in keys:
            if len(key) < _FUZZY_MIN_LEN:
                continue
            match = difflib.get_close_matches(key, SYNONYMS.keys(), n=1, cutoff=0.85)
            if match:
                ratio = difflib.SequenceMatcher(None, key, match[0]).ratio()
                return SYNONYMS[match[0]], Method.fuzzy, round(ratio * FUZZY_FACTOR, 2)
    return None


def lookup_label(label: str) -> list[Hit]:
    """Field paths a label maps to (usually one; compound labels such as 'DEAL DATE/VALUE DATE' two)."""
    if hit := _single(label, fuzzy=False):
        return [hit]
    if "/" in label:
        parts = [p for p in label.split("/") if p.strip()]
        hits: dict[str, Hit] = {}
        for part in parts:
            if (h := _single(part, fuzzy=False)) and h[0] not in hits:
                hits[h[0]] = h
        if hits:
            return list(hits.values())
    hit = _single(label, fuzzy=True)
    return [hit] if hit else []


def collect_candidates(doc: ExtractedDocument) -> tuple[dict[str, list[Candidate]], dict[int, str]]:
    """Candidates per field path, and key_values index -> field path(s) (comma-joined)."""
    cands: dict[str, list[Candidate]] = {}
    kv_map: dict[int, str] = {}
    for i, kv in enumerate(doc.pairs):
        hits = lookup_label(kv.key)
        if not hits:
            continue
        kv_map[i] = ", ".join(h[0] for h in hits)
        for path, method, confidence in hits:
            if kv.source is Source.prose:
                method, confidence = Method.text, METHOD_SCORE[Method.text]
            cands.setdefault(path, []).append(Candidate(path, kv, kv.value, method, confidence))
    return cands, kv_map


def _sort_key(c: Candidate) -> tuple[Any, ...]:
    kv = c.kv
    position = (kv.page, kv.value_bbox[1], kv.value_bbox[0]) if kv and kv.value_bbox else (9999, 0.0, 0.0)
    return (RANK[c.method], -c.confidence, _SOURCE_ORDER[kv.source] if kv else 9, *position)


def resolve(
    cands: dict[str, list[Candidate]], normalised: dict[int, Any]
) -> tuple[dict[str, FieldValue], list[Issue]]:
    """Pick one candidate per field: parseable first, then rank, confidence, source, position.

    `normalised` maps id(candidate) -> normalised value (None when it did not parse). Two
    same-rank candidates with different values raise a CONFLICTING_VALUES warning and cap the
    winner's confidence at 0.85.
    """
    fields: dict[str, FieldValue] = {}
    issues: list[Issue] = []
    for path, cs in cands.items():
        parseable = [c for c in cs if normalised.get(id(c)) is not None]
        ranked = sorted(parseable or cs, key=_sort_key)
        win = ranked[0]
        value = normalised.get(id(win))
        confidence = win.confidence
        rivals = [
            c
            for c in ranked[1:]
            if RANK[c.method] == RANK[win.method] and normalised.get(id(c)) not in (None, value)
        ]
        if rivals:
            confidence = min(confidence, 0.85)
            others = ", ".join(sorted({f"{c.kv.key if c.kv else '?'}={c.raw}" for c in rivals}))
            issues.append(
                Issue(
                    "CONFLICTING_VALUES",
                    Severity.WARNING,
                    f"{path}: using {win.raw!r} ({win.kv.key if win.kv else '?'}); also found {others}",
                    (path,),
                )
            )
        kv = win.kv
        fields[path] = FieldValue(
            value=value,
            raw=win.raw,
            method=win.method,
            confidence=confidence,
            label=kv.key if kv else None,
            page=kv.page if kv else None,
            bbox=kv.value_bbox if kv else None,
        )
    return fields, issues

"""Template fingerprint and matching (docs/03_lld.md §4.6-4.7).

fingerprint = set of normalised labels on the slip (table / grid / text; prose keys are
synthetic and excluded). A template matches when all its keywords appear in the slip text and
the Jaccard score of the two label sets reaches its threshold; the best score wins.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.engine.fields import Source, normalise_label
from app.engine.models import ExtractedDocument, TemplateDefinition, TemplateMatchResult


def fingerprint(doc: ExtractedDocument) -> frozenset[str]:
    return frozenset(normalise_label(kv.key)[0] for kv in doc.pairs if kv.source is not Source.prose)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    union = a | b
    return round(len(a & b) / len(union), 4) if union else 0.0


def match_template(
    doc: ExtractedDocument, templates: Sequence[TemplateDefinition], default_threshold: float
) -> TemplateMatchResult:
    fp = fingerprint(doc)
    text = doc.text.lower()
    best: tuple[float, TemplateDefinition, float] | None = None
    best_seen = 0.0
    for t in templates:
        if t.keywords and not all(k.lower() in text for k in t.keywords):
            continue
        score = jaccard(fp, t.fingerprint_labels)
        best_seen = max(best_seen, score)
        threshold = t.match_threshold or default_threshold
        if score >= threshold and (best is None or score > best[0]):
            best = (score, t, threshold)
    if best:
        return TemplateMatchResult(best[1], best[0], best[2], matched=True)
    return TemplateMatchResult(None, best_seen, default_threshold, matched=False)

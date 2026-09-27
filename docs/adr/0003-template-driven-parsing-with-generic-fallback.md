# ADR-0003: Template-driven parsing with a generic fallback

**Status:** Accepted

## Context

There is no mandated slip format: every bank, broker and platform prints its own, with different
labels for the same field (`Stl Dt`, `Value Date`, `Settlement Date`). New layouts keep appearing.
Output must be accurate, explainable per field, deterministic and reproducible for audit, and slip
data may not be sent to external services.

## Decision

Parse with **templates first, generic rules as fallback** (baseline D6, D7):

1. **Detect**: fingerprint = set of normalised labels; Jaccard against active templates; best ≥ `match_threshold` (default `BONDS_TEMPLATE_MATCH_THRESHOLD` = 0.80) wins.
2. **Map**: template `label_map` → built-in synonyms → abbreviation expansion → fuzzy.
3. **Rules**: template `region_rules` (bbox), `regex_rules`, `constants`, `ignore`.
4. Every field carries `method` and confidence (`template`/`region`/`constant` 1.00, `regex`/`synonym` 0.95, `abbreviation`/`derived` 0.90, `fuzzy` ≤ 0.85, `text` 0.80; `accepted_derived` → 0.95).
5. No match → `NEW_TEMPLATE` with a best-effort deal and all key/values; the client maps once via `preview` / `approve`, which saves a template version.
6. Templates live in the DB (`templates`, `template_versions`), are versioned and never overwritten; JSON import/export.

## Consequences

**Positive**
- New layouts are data, not code (Open/Closed); onboarding takes minutes.
- Deterministic and explainable: each value has a method, confidence and bbox.
- `PARSED` is reached only through template evidence, so direct answers are trustworthy.
- Template versions give full lineage (`slips.template_version`).

**Negative**
- First slip of each layout needs human mapping.
- Fingerprint collisions or label drift possible; handled by thresholds, keywords and the `NEEDS_REVIEW` → new version flow.
- Synonym and abbreviation lists need curation.

## Alternatives considered

| Option | Why not |
|---|---|
| LLM extraction | External calls with confidential data; non-deterministic; hard to audit. |
| Trained layout ML model (LayoutLM etc.) | Needs labelled data we don't have; opaque; heavy runtime. |
| Hard-coded parser per source | Code change and release for every new layout. |
| Generic rules only | Cannot reach the accuracy needed for a direct `PARSED` answer. |

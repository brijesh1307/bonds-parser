# ADR-0008: Decimal everywhere, decimal strings in outputs

**Status:** Accepted

## Context

Slips carry face values in crores, prices to 4 decimals, yields, accrued interest and
considerations that must reconcile exactly (validation: principal = FV × price / 100 within ±0.01,
consideration = principal + accrued, repo leg 2 = leg 1 + interest). Binary floats cannot represent
most decimal fractions and JSON numbers are commonly parsed into floats by clients, which would
corrupt amounts downstream.

## Decision

Use **`decimal.Decimal` for all money, rates, prices and yields** (baseline D10):

- Normalisers produce `Decimal` from text; never `float()` in the engine.
- Validation arithmetic in `Decimal` with explicit tolerance.
- JSON and XML serialise these fields as **strings** (e.g. `"50000000"`, `"7.1000"`), matching `samples/mock/expected/*.json`.
- Excel: **Assumption:** cells written as numeric with an explicit number format where exact within Excel's precision, otherwise as text; the JSON/XML string is the canonical value.
- Integers (`quantity`, `accrued_days`, `tenor_days`, `repo_days`) remain integers; dates ISO `YYYY-MM-DD` (D11).

## Consequences

**Positive**
- No rounding drift; exact reconciliation and golden-file comparisons.
- Clients cannot accidentally lose precision via JSON number parsing.
- Scale (trailing zeros) as printed on the slip can be preserved.

**Negative**
- Clients must convert strings to their own decimal type before arithmetic.
- JSON Schema types are `string` with a pattern rather than `number`.
- Slightly more code in Pydantic models (custom serialisers).

## Alternatives considered

| Option | Why not |
|---|---|
| `float` | Rounding errors; unacceptable for money. |
| Integer minor units (paise) | Prices/yields need 4+ decimals; different scale per field; less readable. |
| Decimal internally, JSON numbers on output | Most JSON parsers turn them into floats. |

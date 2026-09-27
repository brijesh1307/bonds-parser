# ADR-0007: Market detection before normalisation

**Status:** Accepted

## Context

The same text means different values in different markets: `1,00,000` (Indian grouping) vs
`100,000`; `03/04/2026` is 3 April day-first but 4 March month-first; `5.00 Cr` is a crore amount;
day-count and settlement conventions differ. The MVP targets India, but slips from other markets
(US, GB, DE, JP, international) will arrive and must not be silently mis-parsed with Indian rules.

## Decision

Detect the **market before normalising** values (baseline D12, pipeline step 3):

- Vote of signals: ISIN prefix, CUSIP/SEDOL, currency, settlement system, platform, name style, number format, BIC → `market` (`IN, US, GB, DE, JP, INTL, UNKNOWN`), `issuer_country`, `slip_locale`, `market_confidence`.
- Normalisation (step 7) uses the **market profile** in `app/engine/profiles/`: amount units (lakh/crore), date order, rates, ISIN, enums, holidays.
- MVP: only the `IN` profile parses fully. A slip whose market has no profile, or whose market is uncertain, cannot be `PARSED` → `NEEDS_REVIEW`.
- Templates carry a `market`; available profiles are listed at `GET /api/v1/schema/markets`.

## Consequences

**Positive**
- Prevents a whole class of silent errors (wrong date order, wrong magnitude).
- New market = new profile, no pipeline change (Open/Closed).
- `market` and `issuer_country` stored on `slips` for filtering (`GET /api/v1/slips?market=`).

**Negative**
- Voting heuristics can be wrong on sparse slips; mitigated by `market_confidence` and review.
- Non-IN slips always need review until their profile exists.
- Profiles must be maintained (holiday calendars in particular).

## Alternatives considered

| Option | Why not |
|---|---|
| Assume India everywhere | Silently wrong for foreign slips. |
| Market only from template | Doesn't work for `NEW_TEMPLATE` slips or the generic fallback. |
| Try all locales, pick the "most valid" | Ambiguous dates (both orders valid) make this unreliable. |

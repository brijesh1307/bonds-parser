# 09 · Market Profiles (multi-country design)

How the parser handles slips from more than one country. Baseline decision **D12**: the market is
detected **before** normalisation by a vote of signals. The MVP fully parses India (`IN`). Other
markets are detected and sent to review. Names and values follow `docs/00_design_baseline.md`.

> Items marked **(assumption)**, including the signal weights and cut-offs, are initial proposals
> to be tuned against real slips. They are not fixed by the baseline.

---

## 1. Why the market is detected first

The same characters mean different things in different markets. Normalising before the market is
known silently produces wrong deals.

| Raw text | `IN` reading | `US` reading | `GB` reading |
|---|---|---|---|
| `05/06/2026` | 5 June 2026 | May 6, 2026 | 5 June 2026 |
| `1,00,000` | 100000 (lakh grouping) | invalid grouping | invalid grouping |
| `5.00 Cr` | 50000000 | meaningless | meaningless |
| `99-16+` | invalid | 99.515625 (32nds) | invalid |
| Price `101.2350`, accrued on a G-Sec | 30/360 | ACT/ACT (UST) | ACT/ACT (gilt) |
| Settlement `T+1` holiday check | RBI / FIMMDA calendar | SIFMA calendar | UK bank holidays |

So pipeline step 3 (**Market**) runs after **Extract** and before **Map / Normalise**. It uses only
raw key/values and slip text. The selected **market profile** then drives normalisation, derived
values (day count, settlement cycle) and validation (holidays).

---

## 2. The four "countries" on a slip

| Field | Meaning | Example: US-dollar bond of an Indian issuer, bought in London, slip printed by a UK broker |
|---|---|---|
| `issuer_country` | Country of the issuer (ISIN prefix, except `XS` / `EU`) | `IN` |
| `market` | Market whose conventions govern the trade (settlement, day count, quoting, holidays). **Selects the profile** | `INTL` (Euroclear-settled Eurobond) |
| `currency` / `settlement_currency` | ISO 4217 codes | `USD` |
| `slip_locale` | How the document is written (date order, number format, language), e.g. `en-IN`, `en-US`, `en-GB` | `en-GB` |

These can all differ, so they are stored separately (`slips.market`, `slips.issuer_country`,
deal fields `issuer_country`, `market`, `slip_locale`, `market_confidence`). Only `market` picks
the profile. `slip_locale` drives date and number parsing when it disagrees with the market's
default (**assumption**).

`market` enum: `IN, US, GB, DE, JP, INTL, UNKNOWN`.

---

## 3. Detection signals

Each signal found on the slip casts a weighted vote for one market (or, for ambiguous signals, a
split vote). Weights are an **(assumption)**.

| # | Signal | How it is read | Votes | Weight |
|---|---|---|---|---|
| 1 | **Matched template** | Template's `market` (only if a template matched ≥ threshold) | Template market | 5.0 |
| 2 | **ISIN prefix** | First 2 chars of a check-digit-valid ISIN | `IN`→IN, `US`→US, `GB`→GB, `DE`→DE, `JP`→JP, `XS`/`EU`→INTL. Also sets `issuer_country` | 3.0 |
| 3 | **Settlement system / depository** | Keywords | CCIL, NDS-OM, CROMS, E-Kuber, NSDL, CDSL, NCL, ICCL, SGL→IN; Fedwire, DTC/DTCC, FICC→US; CREST, Euroclear UK→GB; Clearstream Frankfurt→DE; JASDEC, BOJ-NET→JP; Euroclear Bank, Clearstream Banking Luxembourg→INTL | 3.0 |
| 4 | **CUSIP** | 9 chars, mod-10 "double-add-double" check digit valid | US | 2.5 |
| 5 | **SEDOL** | 7 chars, weights 1,3,1,7,3,9 + check digit, no vowels | GB | 2.5 |
| 6 | **Common Code** | 9 digits labelled "Common Code" / "CC". It has **no check digit**, so the label is required | INTL | 2.0 |
| 7 | **Currency code** | ISO code in a label or value | INR→IN, USD→US, GBP→GB, JPY→JP; EUR→split DE / INTL | 2.0 |
| 8 | **Currency symbol / word** | `₹`, `Rs.`, `Rupees`→IN; `£`→GB; `¥`→JP; `$` only if no other currency evidence (US 0.5) | as listed | 1.0 |
| 9 | **Trading platform** | Keywords | NDS-OM, NSE RFQ, BSE RFQ, EBP, CROMS, TREPS→IN; TradeWeb / Bloomberg are not decisive (no vote) | 2.0 |
| 10 | **Security name style** | Regex | `7.10% GS 2034`, `SDL`, `182 DTB 25032027`, `NCD`→IN; `T 4 1/4 05/15/34`, `UST`, `T-Bill` with CUSIP→US; `4¼% Treasury Gilt 2034`→GB; `Bund`, `OBL`, `Schatz`→DE; `JGB`→JP | 1.5 |
| 11 | **Number format** | Digit grouping on amounts | `12,34,567.00` (lakh grouping)→IN; `1.234.567,89`→DE; `1,234,567.00`→no vote (too common) | 1.0 |
| 12 | **SWIFT BIC country** | Chars 5–6 of an 8/11-char BIC | Country of the BIC | 1.5 |
| 13 | **Regulators / keywords** | Keywords | RBI, SEBI, FIMMDA, FEMA, crore, lakh→IN; FINRA, TRACE, SEC→US; FCA, DMO, "gilt-edged"→GB; BaFin→DE; JSDA→JP | 1.5 |

Signals are deduplicated per type: several ISINs on one slip count once per market.

### 3.1 Vote, confidence and conflict rules

```
score[m]           = Σ weight of signals voting for m
winner             = argmax score
market_confidence  = score[winner] / Σ score            (0 when no signal found)
```

| Rule | Outcome |
|---|---|
| No signals at all | `market = UNKNOWN`, confidence 0 → `NEEDS_REVIEW` ("market uncertain") |
| `market_confidence` < **0.70** (**assumption**) | Winner is kept but flagged → `NEEDS_REVIEW` ("market uncertain") |
| Top two markets within **1.0** point of each other (**assumption**) | Conflict: winner kept, both listed in `market.signals` → `NEEDS_REVIEW` |
| A matched template's market is contradicted by **≥ 2 independent** signals of weight ≥ 2.0 for one other market | Template may be wrong for this slip → `NEEDS_REVIEW` ("market conflicts with template") |
| ISIN prefix (issuer country) differs from the settlement-system market | Normal: settlement system wins for `market`, ISIN sets `issuer_country`. Not a conflict |
| Winner has no available profile | `NEEDS_REVIEW` ("market profile not available") |

All signals, with the text that fired them, are returned in the slip result so the reviewer can
see why a market was chosen.

---

## 4. Market profile file format

One JSON file per market: `app/engine/profiles/<code>.json`, where `<code>` is the `market` enum
value (`IN.json`, `US.json`, `GB.json`, `INTL.json`). Profiles are code-reviewed config, not DB
data, so a change goes through CI (`docs/10_testing_strategy.md`).
`GET /api/v1/schema/markets` lists the profiles with `"status": "active"`.

| Key | Purpose |
|---|---|
| `code`, `name`, `status` | `status`: `active` (parsing allowed) or `planned` (detected only) |
| `locale` | Default `slip_locale` |
| `date` | `order` (`DMY` / `MDY` / `YMD`), accepted formats, whether ambiguous numeric dates are allowed |
| `numbers` | Decimal and thousands separators, grouping style, unit words and their multipliers |
| `currency` | Default currency |
| `identifiers` | Accepted identifier types and ISIN prefixes |
| `day_count` | Default day count per `instrument_type` (plus `REPO`) |
| `coupon_frequency` | Default per `instrument_type` |
| `price_quoting` | `decimal` or `32nds`, decimals, per-100 basis |
| `settlement_cycle` | Default `T+n` per `instrument_type` (used for derivation and the "dates" check) |
| `calendar` | Weekend days and holiday list source + file |
| `signals` | Keywords this profile contributes to detection (§3) |
| `synonyms` | Extra label → field synonyms for this market, merged over the built-in map |
| `instrument_types` | Allowed types plus name patterns used to derive `instrument_type` |

### 4.1 `IN.json` (full, active in the MVP)

```json
{
  "code": "IN",
  "name": "India",
  "status": "active",
  "locale": "en-IN",
  "date": {
    "order": "DMY",
    "formats": ["%d-%b-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d", "%d %B %Y", "%d%m%Y"],
    "allow_ambiguous_numeric": true
  },
  "numbers": {
    "decimal_sep": ".",
    "thousands_sep": ",",
    "grouping": "indian",
    "accept_western_grouping": true,
    "units": {"cr": 10000000, "crore": 10000000, "crores": 10000000,
              "lakh": 100000, "lakhs": 100000, "lac": 100000, "lacs": 100000,
              "mn": 1000000, "bn": 1000000000}
  },
  "currency": "INR",
  "identifiers": {"types": ["ISIN"], "isin_prefixes": ["IN"]},
  "day_count": {
    "GSEC": "30/360", "SDL": "30/360",
    "TBILL": "ACT/364", "CMB": "ACT/364",
    "CORPORATE_BOND": "ACT/ACT", "PSU_BOND": "ACT/ACT",
    "CP": "ACT/365", "CD": "ACT/365",
    "REPO": "ACT/365"
  },
  "coupon_frequency": {"GSEC": 2, "SDL": 2, "CORPORATE_BOND": 1, "PSU_BOND": 1},
  "price_quoting": {"style": "decimal", "per": 100, "price_decimals": 4, "yield_decimals": 4},
  "settlement_cycle": {"GSEC": "T+1", "SDL": "T+1", "TBILL": "T+1", "CMB": "T+1",
                       "CORPORATE_BOND": "T+1", "PSU_BOND": "T+1", "CP": "T+0", "CD": "T+0"},
  "calendar": {
    "weekend": ["SAT", "SUN"],
    "source": "RBI / FIMMDA published holiday list (Mumbai)",
    "file": "app/engine/profiles/holidays/IN.json"
  },
  "signals": {
    "settlement_systems": ["CCIL", "NDS-OM", "CROMS", "E-Kuber", "NSDL", "CDSL", "NCL", "ICCL", "SGL", "CSGL"],
    "platforms": ["NDS-OM", "NSE RFQ", "BSE RFQ", "EBP", "CROMS", "TREPS"],
    "keywords": ["RBI", "SEBI", "FIMMDA", "crore", "lakh"],
    "currency_symbols": ["₹", "Rs.", "INR", "Rupees"],
    "name_patterns": ["\\d+(\\.\\d+)?% GS \\d{4}", "\\bSDL\\b", "\\d{2,3} DTB \\d{8}", "\\bNCD\\b"]
  },
  "synonyms": {
    "face_value": ["Nominal (Cr)", "FV (Rs. Cr)", "Amount (Cr)"],
    "consideration": ["Net Payable", "Net Receivable", "Settlement Amt"],
    "deal_id": ["Contract No", "Contract Note No"],
    "security_name": ["Paper", "Scrip"]
  },
  "instrument_types": {
    "GSEC":           {"patterns": ["\\bGS\\b", "GOI", "Government Stock"]},
    "SDL":            {"patterns": ["\\bSDL\\b", "State Development Loan"]},
    "TBILL":          {"patterns": ["\\bDTB\\b", "Treasury Bill", "T-Bill"]},
    "CMB":            {"patterns": ["\\bCMB\\b", "Cash Management Bill"]},
    "CORPORATE_BOND": {"patterns": ["\\bNCD\\b", "Debenture", "^INE"]},
    "PSU_BOND":       {"patterns": ["PSU", "Tax Free"]},
    "CP":             {"patterns": ["Commercial Paper", "\\bCP\\b"]},
    "CD":             {"patterns": ["Certificate of Deposit", "\\bCD\\b"]}
  }
}
```

The holiday file (**assumption** on format): `{"2026": ["2026-01-26", "2026-03-04", ...]}`, updated
yearly from the published list. A missing year produces a validation warning, not a failure.

### 4.2 `US.json` (planned)

```json
{
  "code": "US", "name": "United States", "status": "planned", "locale": "en-US",
  "date": {"order": "MDY", "formats": ["%m/%d/%Y", "%m/%d/%y", "%b %d, %Y", "%Y-%m-%d"],
           "allow_ambiguous_numeric": true},
  "numbers": {"decimal_sep": ".", "thousands_sep": ",", "grouping": "western",
              "units": {"mm": 1000000, "mln": 1000000, "m": 1000000, "bn": 1000000000}},
  "currency": "USD",
  "identifiers": {"types": ["CUSIP", "ISIN"], "isin_prefixes": ["US"]},
  "day_count": {"UST": "ACT/ACT", "TBILL": "ACT/360", "CORPORATE_BOND": "30/360", "REPO": "ACT/360"},
  "coupon_frequency": {"UST": 2, "CORPORATE_BOND": 2},
  "price_quoting": {"style": "32nds", "per": 100, "also_decimal": true},
  "settlement_cycle": {"UST": "T+1", "TBILL": "T+1", "CORPORATE_BOND": "T+1"},
  "calendar": {"weekend": ["SAT", "SUN"], "source": "SIFMA US bond market holiday recommendations",
               "file": "app/engine/profiles/holidays/US.json"},
  "signals": {"settlement_systems": ["Fedwire", "DTC", "DTCC", "FICC"],
              "keywords": ["FINRA", "TRACE", "CUSIP"], "currency_symbols": ["USD", "US$"],
              "name_patterns": ["^T \\d+(\\s\\d/\\d)? \\d{2}/\\d{2}/\\d{2}", "\\bUST\\b"]},
  "synonyms": {"face_value": ["Par", "Par Amount", "Quantity (M)"],
               "settlement_date": ["Settle Date", "Settle"],
               "consideration": ["Net Money", "Total Net"]},
  "instrument_types": {"UST": {"patterns": ["^T ", "Treasury Note", "Treasury Bond"]},
                       "TBILL": {"patterns": ["\\bB \\d", "Treasury Bill"]},
                       "CORPORATE_BOND": {"patterns": []}}
}
```

**32nds price quoting.** `HANDLE-TT[f]`, where `TT` is 32nds and `f` is optional: `+` = ½/32, or a
digit `0–7` = eighths of a 32nd.

| Quote | Calculation | Decimal |
|---|---|---|
| `99-16` | 99 + 16/32 | `99.5` |
| `99-16+` | 99 + 16.5/32 | `99.515625` |
| `99-162` | 99 + (16 + 2/8)/32 | `99.5078125` |
| `101-07¾` / `101-076` | 101 + (7 + 6/8)/32 | `101.2421875` |

Results are exact `Decimal` values, never rounded to 4 dp. The raw quote is kept in the field's
`raw`.

### 4.3 `GB.json` (planned)

```json
{
  "code": "GB", "name": "United Kingdom", "status": "planned", "locale": "en-GB",
  "date": {"order": "DMY", "formats": ["%d/%m/%Y", "%d-%b-%Y", "%d %B %Y", "%Y-%m-%d"],
           "allow_ambiguous_numeric": true},
  "numbers": {"decimal_sep": ".", "thousands_sep": ",", "grouping": "western",
              "units": {"m": 1000000, "mn": 1000000, "bn": 1000000000}},
  "currency": "GBP",
  "identifiers": {"types": ["SEDOL", "ISIN"], "isin_prefixes": ["GB"]},
  "day_count": {"GILT": "ACT/ACT", "TBILL": "ACT/365", "CORPORATE_BOND": "ACT/ACT", "REPO": "ACT/365"},
  "coupon_frequency": {"GILT": 2},
  "price_quoting": {"style": "decimal", "per": 100, "price_decimals": 6},
  "settlement_cycle": {"GILT": "T+1", "TBILL": "T+1", "CORPORATE_BOND": "T+1"},
  "calendar": {"weekend": ["SAT", "SUN"], "source": "UK (England & Wales) bank holidays",
               "file": "app/engine/profiles/holidays/GB.json"},
  "signals": {"settlement_systems": ["CREST", "Euroclear UK"], "keywords": ["DMO", "FCA", "gilt"],
              "currency_symbols": ["£", "GBP"], "name_patterns": ["Treasury Gilt \\d{4}", "Treasury Stock"]},
  "synonyms": {"face_value": ["Nominal", "Nominal Amount"], "accrued_interest": ["Accrued"]},
  "instrument_types": {"GILT": {"patterns": ["Gilt", "Treasury Stock"]},
                       "TBILL": {"patterns": ["Treasury Bill"]}}
}
```

Gilt note: inside the ex-dividend period (7 business days before a coupon), accrued interest is
**negative**. The consideration check must accept that (**assumption**: flag `ex_dividend: true`
in derivation).

### 4.4 `INTL.json` (planned, Eurobonds)

```json
{
  "code": "INTL", "name": "International (Euroclear / Clearstream)", "status": "planned",
  "locale": "en-GB",
  "date": {"order": "DMY", "formats": ["%d-%b-%Y", "%d %B %Y", "%Y-%m-%d"],
           "allow_ambiguous_numeric": false},
  "numbers": {"decimal_sep": ".", "thousands_sep": ",", "grouping": "western",
              "units": {"m": 1000000, "mn": 1000000, "bn": 1000000000}},
  "currency": null,
  "identifiers": {"types": ["ISIN", "COMMON_CODE"], "isin_prefixes": ["XS", "EU"]},
  "day_count": {"EUROBOND": "30/360", "REPO": "ACT/360"},
  "coupon_frequency": {"EUROBOND": 1},
  "price_quoting": {"style": "decimal", "per": 100, "price_decimals": 4},
  "settlement_cycle": {"EUROBOND": "T+2"},
  "calendar": {"weekend": ["SAT", "SUN"], "source": "TARGET2 + currency calendar",
               "file": "app/engine/profiles/holidays/INTL.json"},
  "signals": {"settlement_systems": ["Euroclear Bank", "Clearstream Banking", "Euroclear/Clearstream"],
              "keywords": ["Common Code", "ICMA", "Reg S", "144A"]},
  "synonyms": {"face_value": ["Nominal", "Principal Amount"]},
  "instrument_types": {"EUROBOND": {"patterns": ["^XS", "Reg S", "EMTN"]}}
}
```

- `currency: null`: the currency must be read from the slip. If it is not printed, the slip goes
  to `NEEDS_REVIEW`.
- Eurobonds normally use 30E/360 (ICMA), which is not in the `day_count` enum. Adding `30E/360` is
  an open schema change. Until then the profile maps it to `30/360` and flags the field for
  review (**assumption**).
- `allow_ambiguous_numeric: false`: an INTL slip that prints `05/06/2026` sends the date to
  review (§5).

---

## 5. Ambiguous dates (`05/06/2026`)

A numeric date is ambiguous when both of the first two parts are ≤ 12. Resolution order:

1. **Unambiguous formats need no resolution:** `05-Jun-2026`, `2026-06-05`, `5 June 2026`.
2. **Market profile `date.order`:** `IN` / `GB` → 5 June, `US` → May 6. Used only when the market
   is confidently detected (§3.1).
3. **Same-slip evidence:** if any other numeric date on the slip has a part > 12 (e.g.
   `23/09/2026`), that fixes the order for the whole slip.
4. **Consistency checks:** the reading must satisfy settlement ≥ trade, settlement − trade matching
   the profile's settlement cycle, no weekend or holiday settlement, and maturity > settlement.
   If only one reading passes, it is used.
5. **Still ambiguous** (market uncertain, or `allow_ambiguous_numeric: false`): the date field is
   set with `method: "text"`-level confidence 0.80 (**assumption**). That is below 0.90, so the slip
   goes to `NEEDS_REVIEW` with both candidate dates in the validation message.

A template for a known layout pins the market (signal 1), so step 2 normally decides.

---

## 6. Behaviour in the MVP

| Detected market | Profile | Slip status |
|---|---|---|
| `IN`, confidence ≥ 0.70, no conflict | `IN.json` (`active`) | Normal rules (`PARSED` / `NEEDS_REVIEW` / `NEW_TEMPLATE`) |
| `US`, `GB`, `DE`, `JP`, `INTL` | none, or `planned` | Extraction still runs and `key_values` are returned. The best-effort deal is normalised with **no** market-specific conversion. Status `NEEDS_REVIEW` with reason `"market profile not available"` (or `NEW_TEMPLATE` if no template matched) |
| `UNKNOWN` / uncertain / conflict | n/a | `NEEDS_REVIEW` ("market uncertain") |

A non-`IN` slip can still be onboarded as a template (the layout is learnt), but it will not be
`PARSED` until its profile is `active`. Approving it therefore needs
`accept_validation_issues: true` and is flagged in the audit row (**assumption**).

---

## 7. Adding a new market (checklist)

1. [ ] Write `app/engine/profiles/<CODE>.json` with every key in §4; start with `"status": "planned"`.
2. [ ] Add the holiday file `app/engine/profiles/holidays/<CODE>.json` and record its source and owner.
3. [ ] Add the code to the `market` enum (baseline §5) if it is new, and any new `instrument_type`
       / `day_count` values (for example `30E/360`). This is a schema change: update the baseline
       first.
4. [ ] Implement any new normaliser (for example `parse_32nds`, `1.234,56` numbers) in
       `app/engine/normalize.py`, with property tests (`docs/10_testing_strategy.md` §4).
5. [ ] Add any new identifier validator (CUSIP / SEDOL check digits) in `app/engine/validate.py`.
6. [ ] Add detection signals and check the weights against both the existing golden slips and new
       ones. `IN` mock slips must still detect as `IN` with confidence ≥ 0.70.
7. [ ] Add at least 2 synthetic mock slips for the market (generator in `tools/`) with expected
       JSON, including one ambiguous-date case.
8. [ ] Add unit tests for day count, accrued interest, price quoting and settlement cycle for each
       instrument type.
9. [ ] Business sign-off from someone who trades that market (conventions review).
10. [ ] Flip `"status": "active"`. Confirm that `GET /api/v1/schema/markets` lists it.
11. [ ] Re-run review-queue slips of that market with `POST /slips/{id}/reparse`.

# 05 · Data Dictionary and Outputs

The canonical deal every slip is mapped to, the response envelope the API returns, and the exact
JSON, XML and Excel output formats.

`docs/00_design_baseline.md` is authoritative (§5 canonical schema, D10 decimals, D11 dates,
D15 outputs). Label lists extend `docs/deal_slip_standard_formats.md` §4. Examples use
`samples/mock/*` values. Anything not fixed by the baseline is marked **[A]** (assumption).

---

## 1. Canonical field dictionary

Conventions for the tables below:

- **Type**: `str`, `enum`, `date` (ISO `YYYY-MM-DD`), `decimal` (JSON **string**, D10), `int`
  (JSON number), `float` (JSON number; scores only), `object`.
- **Format** for decimals names the *kind*, which fixes the output scale (§8.2).
- **Required when**: `ALL` = required for every deal type (baseline §5). Missing required fields
  are listed in `missing_required` and force `NEEDS_REVIEW`.
- Every field is always present in `deal`; not applicable / not found ⇒ `null`.
- Typical labels are matched after normalisation (lower-case, punctuation removed, `(…)` dropped as a
  fallback; see `docs/03_lld.md` §4.4–4.5).

| # | Field | Type | Format | Enum values | Required when | Description | Example | Typical slip labels |
|---|---|---|---|---|---|---|---|---|
| 1 | `deal_id` | str | as printed, trimmed | – | – (optional; many slips print none) | Deal / ticket / reference number of the issuing desk or platform | `GS/NDSOM/2026/004571` | Deal Reference No., Deal ID, Ticket No, Ref, Deal No., Trade ID, Order No., Confirmation No., Contract No. |
| 2 | `deal_type` | enum | – | `OUTRIGHT`, `PRIMARY_AUCTION`, `PRIMARY_PLACEMENT`, `REPO`, `REVERSE_REPO`, `TREPS_BORROW`, `TREPS_LEND`, `LAF` | ALL | Kind of transaction | `OUTRIGHT` | Deal Type, Transaction, Transaction Type, Deal Type & Direction; derived from letter text (`Primary Auction`, `Allotment`) |
| 3 | `instrument_type` | enum | – | `GSEC`, `SDL`, `TBILL`, `CMB`, `CORPORATE_BOND`, `PSU_BOND`, `CP`, `CD` (+ `UST`, `GILT`, `BUND`, `JGB`, `EUROBOND` with market profiles) | – | Instrument class | `GSEC` | Security Type, Instrument Type; derived from security name (`GS 2034`, `DTB`, `NCD`) |
| 4 | `buy_sell` | enum | – | `BUY`, `SELL` | `OUTRIGHT` | Our direction. Reverse repo = `BUY` (leg 1); repo = `SELL`; primary auction = `BUY` | `BUY` | Buy/Sell, Direction; derived from Deal Type (`PURCHASE`/`BOUGHT` → BUY, `SALE`/`SOLD` → SELL) |
| 5 | `platform` | str | canonical name when known | – | – | Execution / reporting venue | `NDS-OM` | Trading Platform, Platform, Venue; derived from deal type text (`OTC, reported on NSE RFQ` → `OTC / NSE RFQ`) |
| 6 | `trade_date` | date | ISO | – | ALL | Trade / deal / auction date (time dropped) | `2026-09-24` | Trade Date, Deal Date, Deal Date / Time, Auction Date, Transaction Date, Date of Deal |
| 7 | `settlement_date` | date | ISO | – | ALL | Settlement / value date; for repos = leg 1 date | `2026-09-25` | Settlement Date, Value Date, Date of Issue / Settlement, Pay-in Date, First Leg Date |
| 8 | `security_name` | str | as printed | – | ALL | Security description | `7.10% GS 2034` | Security Description, Security Name, Security, Collateral Security, Scrip Name, Instrument |
| 9 | `issuer` | str | as printed | – | – | Issuer (corporate / CP / CD) | `Sunrise Infra Finance Ltd` | Issuer, Issuer Name |
| 10 | `credit_rating` | str | `<AGENCY> <RATING>` as printed | – | – | Credit rating with agency | `CRISIL AA+` | Rating, Credit Rating |
| 11 | `isin` | str | 12 chars, upper case, ISO 6166 check digit | – | ALL | ISIN of the security / repo collateral | `IN0020240A75` | ISIN, ISIN Code, Security ISIN |
| 12 | `identifiers` | object | §2 | – | – | All security identifiers | `{"isin": "IN0020240A75", …}` | CUSIP, SEDOL, Common Code, ISIN |
| 13 | `coupon_rate` | decimal | rate, % p.a. (7.10 = 7.10 %) | – | – | Coupon | `"7.10"` | Coupon Rate, Coupon, Interest Rate, Rate of Interest; derived from name (`7.10% GS 2034`) |
| 14 | `coupon_frequency` | int | payments / year | 1, 2, 4, 12 | – | Coupon frequency | `2` | Coupon Frequency, Interest Frequency; derived from `(Semi-Annual)` / `Annual` in coupon text or profile default |
| 15 | `maturity_date` | date | ISO | – | – | Maturity / redemption date | `2034-04-08` | Maturity Date, Date of Maturity, Redemption Date, Maturity |
| 16 | `last_coupon_date` | date | ISO | – | – | Last coupon paid before settlement | `2026-04-08` | Last Coupon Date, Last Interest Paid On, Previous Coupon Date |
| 17 | `day_count` | enum | – | `30/360`, `ACT/ACT`, `ACT/365`, `ACT/364`, `ACT/360` | – | Accrual basis of the security | `30/360` | Day Count, Day Count Basis, Basis; from `167 (30/360)`; profile default per instrument |
| 18 | `tenor_days` | int | days | – | – | Tenor (T-Bill / CMB / CP / CD) | `182` | Tenor, Tenor (Days), Days to Maturity; from `182 DTB …` / `182 Day Treasury Bill` |
| 19 | `face_value` | decimal | nominal | – | ALL | Total face value (INR, not crore) | `"50000000"` | Face Value, Total Face Value, Amount Allotted (FV), FV Amount, Nominal, Face Value of Collateral |
| 20 | `face_value_per_unit` | decimal | nominal | – | – | Face value per bond / unit | `"100000"` | FV per Bond, Face Value per Unit, Denomination |
| 21 | `quantity` | int | units | – | – | Number of bonds / units | `250` | No. of Bonds, Quantity, Units, No. of Securities |
| 22 | `price` | decimal | price per 100, ≥ 4 dp | – | `OUTRIGHT`, `PRIMARY_AUCTION` | Clean price per 100 face (repo: leg 1 price) | `"101.2350"` | Clean Price, Price, Deal Price, Cut-off Price, Rate (per 100) |
| 23 | `yield` | decimal | yield %, ≥ 4 dp | – | – | YTM / cut-off yield | `"6.8865"` | YTM, Yield to Maturity, Yield, Cut-off Yield, YTM % |
| 24 | `principal_amount` | decimal | cash, 2 dp | – | – | `face_value × price / 100` | `"50617500.00"` | Principal Amount, Clean Consideration, Deal Amount |
| 25 | `accrued_days` | int | days | – | – | Accrued interest days | `167` | Accrued Interest Days, Interest Days, Accr Days, Broken Period Days |
| 26 | `accrued_interest` | decimal | cash, 2 dp | – | – | Accrued interest | `"1646805.56"` | Accrued Interest, Accrued Int., Broken Period Interest, Interest Amount |
| 27 | `discount_amount` | decimal | cash, 2 dp | – | – | Discount (discount instruments) | `"6870000.00"` | Discount Amount, Discount |
| 28 | `consideration` | decimal | cash, 2 dp | – | ALL | Net settlement amount (repo: leg 1 amount) | `"52264305.56"` | Total Consideration, Net Consideration, Amount Payable, Settlement Amount, Net Amount |
| 29 | `currency` | str | ISO 4217 | – | – | Deal currency | `INR` | Currency; `(INR)` label suffix; profile currency |
| 30 | `settlement_currency` | str | ISO 4217 | – | – | Settlement currency if different | `null` | Settlement Currency |
| 31 | `fx_rate` | decimal | fx, as printed | – | – | FX rate deal → settlement currency | `null` | FX Rate, Exchange Rate |
| 28a | `stamp_duty` | decimal | cash, 2 dp | – | – | Stamp duty on the trade (buyer amount = principal + stamp duty) | `"1.00"` | Stamp Duty, Stamp Duty to be borne by Buyer (Rs.) |
| 28b | `settlement_reference` | str | as printed | – | – | Clearing settlement number; not unique per deal | `2100001` | Settlement No., Settlement Number |
| 32 | `counterparty` | str | as printed | – | – | Counterparty name | `Anonymous (NDS-OM) - CCIL Novated` | Counterparty, Counter Party, Party Name, Client, Buyer / Seller |
| 32a | `counterparty_pan` | str | 10 chars `AAAAA9999A`, full | – | – | Counterparty PAN. **Personal data**: full in outputs, never logged, masked in audit | `ABCDE1234F` (fictitious example) | Seller PAN No., Buyer PAN No., PAN (the one of the counterparty side) |
| 32b | `is_market_linked` | bool | `true` / `false` / null | – | – | Market Linked Debenture flag (`instrument_type` stays `CORPORATE_BOND`) | `true` | derived from Type of Instrument (`Market Linked`) |
| 33 | `broker` | str | as printed; `Direct` / `No Broker` ⇒ `null` | – | – | Broker | `null` | Broker, Broker Name |
| 34 | `settlement_mode` | str | `DVP-I`, `DVP-II`, `DVP-III` when recognisable | – | – | Settlement mode | `DVP-III` | Settlement Mode, Settlement, Clearing |
| 35 | `portfolio` | str | as printed | – | – | Portfolio / book / category | `AFS - Treasury` | Portfolio, Book, Portfolio / Book, Category, Investment Category |
| 36 | `dealer` | str | as printed | – | – | Dealer name | `R. Mehta` | Dealer, Trader, Dealt By |
| 37 | `bid_type` | enum | – | `COMPETITIVE`, `NON_COMPETITIVE` | – | Auction bid type | `COMPETITIVE` | Bid Type; prose `Competitive Bid` |
| 38 | `bid_amount` | decimal | nominal | – | – | Face value bid in the auction | `"300000000"` | Amount Bid (FV), Bid Amount |
| 39 | `issuer_country` | str | ISO 3166-1 alpha-2 | – | – | Issuer country from ISIN prefix (`null` for `XS`) | `IN` | – (market detection) |
| 40 | `market` | enum | – | `IN`, `US`, `GB`, `DE`, `JP`, `INTL`, `UNKNOWN` | – | Detected market | `IN` | – (market detection) |
| 41 | `slip_locale` | str | BCP 47 **[A]** | – | – | Locale of the slip's formatting | `en-IN` | – (market detection) |
| 42 | `market_confidence` | float | 0.00–1.00, 2 dp | – | – | Share of the market vote won by `market` | `1.0` | – (market detection) |
| 43 | `repo` | object | §3 | – | object present for `REPO`, `REVERSE_REPO` | Repo legs; `null` for other deal types | see §3 | – |

Required-field summary (baseline §5):

| Deal type | Required |
|---|---|
| all | `deal_type, trade_date, settlement_date, security_name, isin, face_value, consideration` (`deal_id` is optional) |
| `OUTRIGHT` | + `buy_sell, price` |
| `PRIMARY_AUCTION` | + `price` |
| `REPO`, `REVERSE_REPO` | + `repo.repo_rate, repo.leg1_amount, repo.leg2_date, repo.leg2_amount` |

Note: `TREPS_*` deals have basket collateral and no ISIN; under the baseline rule they will always be
`NEEDS_REVIEW`. TREPS parsing is after MVP.

---

## 2. `identifiers` object

| Field | Type | Format | Description | Example |
|---|---|---|---|---|
| `isin` | str | ISO 6166, 12 chars | Always equal to top-level `isin` | `IN0020240A75` |
| `cusip` | str | 9 chars, check digit validated | US / CA securities | `null` |
| `sedol` | str | 7 chars, check digit validated | UK securities | `null` |
| `common_code` | str | 9 digits | Euroclear / Clearstream code | `null` |

The object is always present (never `null`); unknown identifiers are `null`.

---

## 3. `repo` object

Present (non-null) when `deal_type` is `REPO` or `REVERSE_REPO`, otherwise `null`. Leg 1 is the
opening leg, leg 2 the reversal. Example values are from `samples/mock/04_market_repo_reverse_repo`.

| Field | Type | Format | Required when | Description | Example | Typical slip labels |
|---|---|---|---|---|---|---|
| `repo_rate` | decimal | rate % p.a. | REPO / REVERSE_REPO | Repo rate | `"5.40"` | Repo Rate, Repo Rate (% p.a.) |
| `repo_days` | int | days | – | `leg2_date − leg1_date` | `3` | Repo Period (Days), Repo Tenor, No. of Days |
| `day_count` | enum | `ACT/365`, `ACT/360`, … | – | Basis for repo interest | `ACT/365` | Day Count (Repo Interest) (full label wins over the `day count` short form) |
| `haircut` | decimal | rate % | – | Haircut / margin | `null` | Haircut, Margin |
| `leg1_date` | date | ISO | – | First leg settlement date | `2026-09-25` | First Leg Date, Leg 1 Date, Start Date |
| `leg1_price` | decimal | price per 100 | – | First leg clean price | `"99.8500"` | First Leg Price |
| `leg1_accrued_days` | int | days | – | Accrued days at leg 1 | `98` | First Leg Accr Days |
| `leg1_accrued_interest` | decimal | cash | – | Accrued interest at leg 1 | `"1902833.33"` | First Leg Accrued Int. |
| `leg1_amount` | decimal | cash | REPO / REVERSE_REPO | First leg settlement amount | `"101752833.33"` | First Leg Settlement Amount, First Leg Amount |
| `leg2_date` | date | ISO | REPO / REVERSE_REPO | Second leg (reversal) date | `2026-09-28` | Second Leg Date, Leg 2 Date, Reversal Date, End Date |
| `leg2_price` | decimal | price per 100 | – | Second leg clean price | `"99.8369"` | Second Leg Price |
| `leg2_accrued_days` | int | days | – | Accrued days at leg 2 | `101` | Second Leg Accr Days |
| `leg2_accrued_interest` | decimal | cash | – | Accrued interest at leg 2 | `"1961083.33"` | Second Leg Accrued Int. |
| `leg2_amount` | decimal | cash | REPO / REVERSE_REPO | Second leg settlement amount | `"101797994.86"` | Second Leg Settlement Amount, Second Leg Amount |
| `repo_interest` | decimal | cash | – | `leg2_amount − leg1_amount` | `"45161.53"` | Repo Interest |

Grid keys are composed as `<row label> <column header>` (`First Leg` + `Accr Days` →
`First Leg Accr Days`). For repos, leg 1 values are also copied to the top level when those are
empty: `settlement_date ← leg1_date`, `price ← leg1_price`, `accrued_days ← leg1_accrued_days`,
`accrued_interest ← leg1_accrued_interest`, `consideration ← leg1_amount`.

---

## 4. Market fields

| Field | Source | Rule |
|---|---|---|
| `market` | Vote of signals (ISIN prefix, CUSIP/SEDOL, currency, settlement system, platform, name style, number format, BIC) | Winner if its share ≥ 0.70, else `UNKNOWN` (baseline). No signals ⇒ `UNKNOWN`. |
| `market_confidence` | Same vote | Winner weight ÷ total weight, 2 dp. |
| `issuer_country` | ISIN prefix | `IN0020240A75` → `IN`; `XS…` / `EU…` → `null`. |
| `slip_locale` | Market profile locale, else inferred from number / date formatting | `en-IN` for India. `null` if not inferable. |

`market` ≠ `IN` (MVP) or `UNKNOWN` ⇒ no market profile ⇒ `NEEDS_REVIEW` when a template matched.
The full vote is returned in the envelope's `market.signals` (§5.3).

---

## 5. `ParseResult` response envelope

Returned by `POST /api/v1/parse?format=json`, `POST /api/v1/slips/{id}/preview` and, extended to
`SlipOut`, by `POST /api/v1/slips`, `GET /api/v1/slips/{id}`, `approve` and `reparse`.

### 5.1 Top-level keys

| Key | Type | Description |
|---|---|---|
| `status` | enum | `PARSED`, `NEEDS_REVIEW`, `NEW_TEMPLATE`, `APPROVED`, `UNREADABLE`, `FAILED` (baseline §3) |
| `source` | object | `file` (original file name), `pages` (int), `page_sizes` (list of `[width, height]` in points per page), `sha256` (hex of the PDF bytes) |
| `market` | object | `MarketInfo`, §5.3 |
| `template` | object | `TemplateMatch`, §5.4 |
| `deal` | object | Canonical deal (§1–§3); all keys always present |
| `fields` | object | Map *field path* → `FieldInfo` (§5.5) for every field that has a value; nested repo fields as `repo.<name>` |
| `key_values` | list | Every extracted `KeyValue` (§5.6), in reading order, with `mapped_to` |
| `missing_required` | list[str] | Required field paths with a `null` value |
| `validation` | list | `ValidationIssue` objects (§5.7) – failed checks only |
| `low_confidence` | list[str] | Required field paths whose confidence < `BONDS_CONFIDENCE_THRESHOLD` (0.90) |
| `unmapped` | list | `KeyValue`s that mapped to no field and are not on the template's `ignore` list (same shape as `key_values`) **[A]: list, not the `_unmapped` object of the reference doc** |

`SlipOut` adds: `id` (UUID), `duplicate` (bool), `duplicate_of` (UUID or null), `created_by`,
`created_at`, `updated_at`, `approved_by`, `approved_actor_name`, `approved_at`.

### 5.2 Bounding boxes

`[x0, top, x1, bottom]` in PDF points, origin at the **top-left** of the page (pdfplumber), 1 dp.
The frontend scales with `page_sizes[page-1]`. `key_bbox` is `null` for prose candidates;
`FieldInfo.bbox` is `null` for constants and computed derivations.

### 5.3 `market` (`MarketInfo`)

| Key | Type | Description |
|---|---|---|
| `market`, `issuer_country`, `slip_locale`, `market_confidence` | as §4 | Same values as in `deal` |
| `profile_available` | bool | A market profile exists (MVP: only `IN`) |
| `signals` | list | `{signal, value, market, weight}` per vote cast |

### 5.4 `template` (`TemplateMatch`)

| Key | Type | Description |
|---|---|---|
| `template_id` | str / null | Matched (or draft) template |
| `name` | str / null | Template name |
| `version` | int / null | Version used (`null` for a draft) |
| `score` | float | Jaccard fingerprint score, 4 dp (best score seen when not matched) |
| `threshold` | float | Threshold applied (template's `match_threshold`, default 0.80) |
| `matched` | bool | `score ≥ threshold` |
| `is_draft` | bool | `true` in preview / approve re-parse with a mapping payload |

### 5.5 `fields.<path>` (`FieldInfo`)

| Key | Type | Description |
|---|---|---|
| `value` | same as the deal field | Normalised value (decimals as strings) |
| `raw` | str / null | Text the value was read from |
| `method` | enum | `template`, `region`, `constant`, `regex`, `synonym`, `abbreviation`, `derived`, `fuzzy`, `text` |
| `confidence` | float | Per baseline §4 score table; `accepted_derived` fields 0.95 |
| `label` | str / null | Label on the slip (or rule name for computed values) |
| `page` | int / null | 1-based page |
| `bbox` | [4 floats] / null | Where the value is on the page |

### 5.6 `key_values[]` (`KeyValue`)

| Key | Type | Description |
|---|---|---|
| `key` | str | Label as printed (grid: `<row label> <header>` for multi-row grids) |
| `value` | str | Value text as printed (whitespace collapsed) |
| `source` | enum | `table`, `grid`, `text`, `prose` |
| `page` | int | 1-based page |
| `key_bbox`, `value_bbox` | [4 floats] / null | Positions |
| `mapped_to` | str / null | Field path this pair was mapped to (or `null`) |

### 5.7 `validation[]` (`ValidationIssue`)

| Key | Type | Description |
|---|---|---|
| `code` | str | e.g. `ISIN_CHECK_DIGIT`, `PRINCIPAL_MISMATCH`, `CONSIDERATION_MISMATCH`, `REPO_INTEREST_MISMATCH`, `WEEKEND_SETTLEMENT`, `DATE_AMBIGUOUS`, `CONFLICTING_VALUES` (full list: `docs/03_lld.md` §4.13) |
| `severity` | enum | `ERROR` (blocks `PARSED`) or `WARNING` |
| `message` | str | Human-readable explanation |
| `fields` | list[str] | Field paths involved |
| `expected`, `actual`, `tolerance` | str / null | Decimal strings where applicable |

### 5.8 Full JSON example — `samples/mock/01_gsec_outright_purchase.pdf`

Situation: the slip's layout was onboarded earlier as template `orca_fi_desk_gsec_deal_slip`
(v1), so the upload is `PARSED`. Values are those of
`samples/mock/expected/01_gsec_outright_purchase.json`; bboxes, page size and SHA-256 are the real
pdfplumber / file values of the mock PDF.

```json
{
  "status": "PARSED",
  "source": {
    "file": "01_gsec_outright_purchase.pdf",
    "pages": 1,
    "page_sizes": [[595.3, 841.9]],
    "sha256": "83a4c9f3ff8e0ad69590d764143f8d2bd7b89202a4d3c1349ff102d69cd9b057"
  },
  "market": {
    "market": "IN",
    "issuer_country": "IN",
    "slip_locale": "en-IN",
    "market_confidence": 1.0,
    "profile_available": true,
    "signals": [
      {"signal": "isin_prefix", "value": "IN", "market": "IN", "weight": 3},
      {"signal": "currency", "value": "INR", "market": "IN", "weight": 2},
      {"signal": "settlement_system", "value": "CCIL", "market": "IN", "weight": 2},
      {"signal": "platform", "value": "NDS-OM", "market": "IN", "weight": 2},
      {"signal": "name_style", "value": "7.10% GS 2034", "market": "IN", "weight": 1},
      {"signal": "number_format", "value": "5,00,00,000.00", "market": "IN", "weight": 1}
    ]
  },
  "template": {
    "template_id": "orca_fi_desk_gsec_deal_slip",
    "name": "Orca FI desk G-Sec deal slip",
    "version": 1,
    "score": 1.0,
    "threshold": 0.8,
    "matched": true,
    "is_draft": false
  },
  "deal": {
    "deal_id": "GS/NDSOM/2026/004571",
    "deal_type": "OUTRIGHT",
    "instrument_type": "GSEC",
    "buy_sell": "BUY",
    "platform": "NDS-OM",
    "trade_date": "2026-09-24",
    "settlement_date": "2026-09-25",
    "security_name": "7.10% GS 2034",
    "issuer": null,
    "credit_rating": null,
    "isin": "IN0020240A75",
    "identifiers": {"isin": "IN0020240A75", "cusip": null, "sedol": null, "common_code": null},
    "coupon_rate": "7.10",
    "coupon_frequency": 2,
    "maturity_date": "2034-04-08",
    "last_coupon_date": "2026-04-08",
    "day_count": "30/360",
    "tenor_days": null,
    "face_value": "50000000",
    "face_value_per_unit": null,
    "quantity": null,
    "price": "101.2350",
    "yield": "6.8865",
    "principal_amount": "50617500.00",
    "accrued_days": 167,
    "accrued_interest": "1646805.56",
    "discount_amount": null,
    "consideration": "52264305.56",
    "currency": "INR",
    "settlement_currency": null,
    "fx_rate": null,
    "counterparty": "Anonymous (NDS-OM) - CCIL Novated",
    "broker": null,
    "settlement_mode": "DVP-III",
    "portfolio": "AFS - Treasury",
    "dealer": "R. Mehta",
    "bid_type": null,
    "bid_amount": null,
    "issuer_country": "IN",
    "market": "IN",
    "slip_locale": "en-IN",
    "market_confidence": 1.0,
    "repo": null
  },
  "fields": {
    "deal_id": {"value": "GS/NDSOM/2026/004571", "raw": "GS/NDSOM/2026/004571", "method": "template", "confidence": 1.0, "label": "Deal Reference No.", "page": 1, "bbox": [240.9, 123.2, 538.6, 141.2]},
    "deal_type": {"value": "OUTRIGHT", "raw": "OUTRIGHT PURCHASE", "method": "template", "confidence": 1.0, "label": "Deal Type", "page": 1, "bbox": [240.9, 141.2, 538.6, 159.2]},
    "instrument_type": {"value": "GSEC", "raw": "Central Government Security (Dated)", "method": "template", "confidence": 1.0, "label": "Security Type", "page": 1, "bbox": [240.9, 231.2, 538.6, 249.2]},
    "buy_sell": {"value": "BUY", "raw": "OUTRIGHT PURCHASE", "method": "derived", "confidence": 0.9, "label": "Deal Type", "page": 1, "bbox": [240.9, 141.2, 538.6, 159.2]},
    "platform": {"value": "NDS-OM", "raw": "NDS-OM (Anonymous Order Matching)", "method": "template", "confidence": 1.0, "label": "Trading Platform", "page": 1, "bbox": [240.9, 159.2, 538.6, 177.2]},
    "trade_date": {"value": "2026-09-24", "raw": "24-Sep-2026", "method": "template", "confidence": 1.0, "label": "Trade Date", "page": 1, "bbox": [240.9, 177.2, 538.6, 195.2]},
    "settlement_date": {"value": "2026-09-25", "raw": "25-Sep-2026 (T+1)", "method": "template", "confidence": 1.0, "label": "Settlement Date", "page": 1, "bbox": [240.9, 213.2, 538.6, 231.2]},
    "security_name": {"value": "7.10% GS 2034", "raw": "7.10% GS 2034", "method": "template", "confidence": 1.0, "label": "Security Description", "page": 1, "bbox": [240.9, 249.2, 538.6, 267.2]},
    "isin": {"value": "IN0020240A75", "raw": "IN0020240A75", "method": "template", "confidence": 1.0, "label": "ISIN", "page": 1, "bbox": [240.9, 267.2, 538.6, 285.2]},
    "coupon_rate": {"value": "7.10", "raw": "7.10% p.a. (Semi-Annual)", "method": "template", "confidence": 1.0, "label": "Coupon Rate", "page": 1, "bbox": [240.9, 285.2, 538.6, 303.2]},
    "coupon_frequency": {"value": 2, "raw": "7.10% p.a. (Semi-Annual)", "method": "derived", "confidence": 0.9, "label": "Coupon Rate", "page": 1, "bbox": [240.9, 285.2, 538.6, 303.2]},
    "maturity_date": {"value": "2034-04-08", "raw": "08-Apr-2034", "method": "template", "confidence": 1.0, "label": "Maturity Date", "page": 1, "bbox": [240.9, 303.2, 538.6, 321.2]},
    "last_coupon_date": {"value": "2026-04-08", "raw": "08-Apr-2026", "method": "template", "confidence": 1.0, "label": "Last Coupon Date", "page": 1, "bbox": [240.9, 321.2, 538.6, 339.2]},
    "day_count": {"value": "30/360", "raw": "167 (30/360)", "method": "derived", "confidence": 0.9, "label": "Accrued Interest Days", "page": 1, "bbox": [240.9, 411.2, 538.6, 429.2]},
    "face_value": {"value": "50000000", "raw": "5,00,00,000.00", "method": "template", "confidence": 1.0, "label": "Face Value (INR)", "page": 1, "bbox": [240.9, 339.2, 538.6, 357.2]},
    "price": {"value": "101.2350", "raw": "101.2350", "method": "template", "confidence": 1.0, "label": "Clean Price", "page": 1, "bbox": [240.9, 357.2, 538.6, 375.2]},
    "yield": {"value": "6.8865", "raw": "6.8865", "method": "template", "confidence": 1.0, "label": "Yield to Maturity (%)", "page": 1, "bbox": [240.9, 375.2, 538.6, 393.2]},
    "principal_amount": {"value": "50617500.00", "raw": "5,06,17,500.00", "method": "template", "confidence": 1.0, "label": "Principal Amount (INR)", "page": 1, "bbox": [240.9, 393.2, 538.6, 411.2]},
    "accrued_days": {"value": 167, "raw": "167 (30/360)", "method": "template", "confidence": 1.0, "label": "Accrued Interest Days", "page": 1, "bbox": [240.9, 411.2, 538.6, 429.2]},
    "accrued_interest": {"value": "1646805.56", "raw": "16,46,805.56", "method": "template", "confidence": 1.0, "label": "Accrued Interest (INR)", "page": 1, "bbox": [240.9, 429.2, 538.6, 447.2]},
    "consideration": {"value": "52264305.56", "raw": "5,22,64,305.56", "method": "template", "confidence": 1.0, "label": "Total Consideration (INR)", "page": 1, "bbox": [240.9, 447.2, 538.6, 465.2]},
    "currency": {"value": "INR", "raw": "Face Value (INR)", "method": "derived", "confidence": 0.9, "label": "Face Value (INR)", "page": 1, "bbox": [56.7, 339.2, 240.9, 357.2]},
    "counterparty": {"value": "Anonymous (NDS-OM) - CCIL Novated", "raw": "Anonymous (NDS-OM) - CCIL Novated", "method": "template", "confidence": 1.0, "label": "Counterparty", "page": 1, "bbox": [240.9, 465.2, 538.6, 483.2]},
    "settlement_mode": {"value": "DVP-III", "raw": "DVP-III through CCIL", "method": "template", "confidence": 1.0, "label": "Settlement Mode", "page": 1, "bbox": [240.9, 483.2, 538.6, 501.2]},
    "portfolio": {"value": "AFS - Treasury", "raw": "AFS - Treasury", "method": "template", "confidence": 1.0, "label": "Portfolio / Book", "page": 1, "bbox": [240.9, 501.2, 538.6, 519.2]},
    "dealer": {"value": "R. Mehta", "raw": "R. Mehta", "method": "template", "confidence": 1.0, "label": "Dealer", "page": 1, "bbox": [240.9, 537.2, 538.6, 555.2]}
  },
  "key_values": [
    {"key": "Deal Reference No.", "value": "GS/NDSOM/2026/004571", "source": "table", "page": 1, "key_bbox": [56.7, 123.2, 240.9, 141.2], "value_bbox": [240.9, 123.2, 538.6, 141.2], "mapped_to": "deal_id"},
    {"key": "Deal Type", "value": "OUTRIGHT PURCHASE", "source": "table", "page": 1, "key_bbox": [56.7, 141.2, 240.9, 159.2], "value_bbox": [240.9, 141.2, 538.6, 159.2], "mapped_to": "deal_type"},
    {"key": "Trading Platform", "value": "NDS-OM (Anonymous Order Matching)", "source": "table", "page": 1, "key_bbox": [56.7, 159.2, 240.9, 177.2], "value_bbox": [240.9, 159.2, 538.6, 177.2], "mapped_to": "platform"},
    {"key": "Trade Date", "value": "24-Sep-2026", "source": "table", "page": 1, "key_bbox": [56.7, 177.2, 240.9, 195.2], "value_bbox": [240.9, 177.2, 538.6, 195.2], "mapped_to": "trade_date"},
    {"key": "Trade Time", "value": "11:42:17", "source": "table", "page": 1, "key_bbox": [56.7, 195.2, 240.9, 213.2], "value_bbox": [240.9, 195.2, 538.6, 213.2], "mapped_to": null},
    {"key": "Settlement Date", "value": "25-Sep-2026 (T+1)", "source": "table", "page": 1, "key_bbox": [56.7, 213.2, 240.9, 231.2], "value_bbox": [240.9, 213.2, 538.6, 231.2], "mapped_to": "settlement_date"},
    {"key": "Security Type", "value": "Central Government Security (Dated)", "source": "table", "page": 1, "key_bbox": [56.7, 231.2, 240.9, 249.2], "value_bbox": [240.9, 231.2, 538.6, 249.2], "mapped_to": "instrument_type"},
    {"key": "Security Description", "value": "7.10% GS 2034", "source": "table", "page": 1, "key_bbox": [56.7, 249.2, 240.9, 267.2], "value_bbox": [240.9, 249.2, 538.6, 267.2], "mapped_to": "security_name"},
    {"key": "ISIN", "value": "IN0020240A75", "source": "table", "page": 1, "key_bbox": [56.7, 267.2, 240.9, 285.2], "value_bbox": [240.9, 267.2, 538.6, 285.2], "mapped_to": "isin"},
    {"key": "Coupon Rate", "value": "7.10% p.a. (Semi-Annual)", "source": "table", "page": 1, "key_bbox": [56.7, 285.2, 240.9, 303.2], "value_bbox": [240.9, 285.2, 538.6, 303.2], "mapped_to": "coupon_rate"},
    {"key": "Maturity Date", "value": "08-Apr-2034", "source": "table", "page": 1, "key_bbox": [56.7, 303.2, 240.9, 321.2], "value_bbox": [240.9, 303.2, 538.6, 321.2], "mapped_to": "maturity_date"},
    {"key": "Last Coupon Date", "value": "08-Apr-2026", "source": "table", "page": 1, "key_bbox": [56.7, 321.2, 240.9, 339.2], "value_bbox": [240.9, 321.2, 538.6, 339.2], "mapped_to": "last_coupon_date"},
    {"key": "Face Value (INR)", "value": "5,00,00,000.00", "source": "table", "page": 1, "key_bbox": [56.7, 339.2, 240.9, 357.2], "value_bbox": [240.9, 339.2, 538.6, 357.2], "mapped_to": "face_value"},
    {"key": "Clean Price", "value": "101.2350", "source": "table", "page": 1, "key_bbox": [56.7, 357.2, 240.9, 375.2], "value_bbox": [240.9, 357.2, 538.6, 375.2], "mapped_to": "price"},
    {"key": "Yield to Maturity (%)", "value": "6.8865", "source": "table", "page": 1, "key_bbox": [56.7, 375.2, 240.9, 393.2], "value_bbox": [240.9, 375.2, 538.6, 393.2], "mapped_to": "yield"},
    {"key": "Principal Amount (INR)", "value": "5,06,17,500.00", "source": "table", "page": 1, "key_bbox": [56.7, 393.2, 240.9, 411.2], "value_bbox": [240.9, 393.2, 538.6, 411.2], "mapped_to": "principal_amount"},
    {"key": "Accrued Interest Days", "value": "167 (30/360)", "source": "table", "page": 1, "key_bbox": [56.7, 411.2, 240.9, 429.2], "value_bbox": [240.9, 411.2, 538.6, 429.2], "mapped_to": "accrued_days"},
    {"key": "Accrued Interest (INR)", "value": "16,46,805.56", "source": "table", "page": 1, "key_bbox": [56.7, 429.2, 240.9, 447.2], "value_bbox": [240.9, 429.2, 538.6, 447.2], "mapped_to": "accrued_interest"},
    {"key": "Total Consideration (INR)", "value": "5,22,64,305.56", "source": "table", "page": 1, "key_bbox": [56.7, 447.2, 240.9, 465.2], "value_bbox": [240.9, 447.2, 538.6, 465.2], "mapped_to": "consideration"},
    {"key": "Counterparty", "value": "Anonymous (NDS-OM) - CCIL Novated", "source": "table", "page": 1, "key_bbox": [56.7, 465.2, 240.9, 483.2], "value_bbox": [240.9, 465.2, 538.6, 483.2], "mapped_to": "counterparty"},
    {"key": "Settlement Mode", "value": "DVP-III through CCIL", "source": "table", "page": 1, "key_bbox": [56.7, 483.2, 240.9, 501.2], "value_bbox": [240.9, 483.2, 538.6, 501.2], "mapped_to": "settlement_mode"},
    {"key": "Portfolio / Book", "value": "AFS - Treasury", "source": "table", "page": 1, "key_bbox": [56.7, 501.2, 240.9, 519.2], "value_bbox": [240.9, 501.2, 538.6, 519.2], "mapped_to": "portfolio"},
    {"key": "SGL / CSGL A/c", "value": "SGL-0457 (RBI)", "source": "table", "page": 1, "key_bbox": [56.7, 519.2, 240.9, 537.2], "value_bbox": [240.9, 519.2, 538.6, 537.2], "mapped_to": null},
    {"key": "Dealer", "value": "R. Mehta", "source": "table", "page": 1, "key_bbox": [56.7, 537.2, 240.9, 555.2], "value_bbox": [240.9, 537.2, 538.6, 555.2], "mapped_to": "dealer"}
  ],
  "missing_required": [],
  "validation": [],
  "low_confidence": [],
  "unmapped": [
    {"key": "Trade Time", "value": "11:42:17", "source": "table", "page": 1, "key_bbox": [56.7, 195.2, 240.9, 213.2], "value_bbox": [240.9, 195.2, 538.6, 213.2], "mapped_to": null},
    {"key": "SGL / CSGL A/c", "value": "SGL-0457 (RBI)", "source": "table", "page": 1, "key_bbox": [56.7, 519.2, 240.9, 537.2], "value_bbox": [240.9, 519.2, 538.6, 537.2], "mapped_to": null}
  ]
}
```

Reading notes:

- `buy_sell`, `coupon_frequency`, `day_count` and `currency` are `derived` (0.90): from
  `OUTRIGHT PURCHASE`, `(Semi-Annual)`, `167 (30/360)` and the `(INR)` label suffix.
- `Trade Time` and `SGL / CSGL A/c` have no canonical field and appear in `unmapped`; adding them to
  the template's `ignore` list removes them.
- On the very first upload (no template yet) the same slip returns `NEW_TEMPLATE`, `template.matched
  = false`, and the same fields with `method: "synonym"` (0.95) instead of `template`.

### 5.9 Review example (fragment) — `samples/mock/03_tbill_primary_auction_allotment.pdf`

The T-Bill letter prints the ISIN only in prose, so even with a matched template it is found by
text search (`text`, 0.80 < 0.90) and the slip is `NEEDS_REVIEW` until the template lists `isin` in
`accepted_derived` (then 0.95) or gets a `regex_rules` entry for it (0.95).

```json
{
  "status": "NEEDS_REVIEW",
  "fields": {
    "isin": {"value": "IN002026Y188", "raw": "IN002026Y188", "method": "text", "confidence": 0.8, "label": "ISIN", "page": 1, "bbox": [404.5, 203.6, 474.6, 213.6]},
    "deal_type": {"value": "PRIMARY_AUCTION", "raw": "Allotment in Primary Auction of 182 Day Treasury Bill", "method": "derived", "confidence": 0.9, "label": "Sub", "page": 1, "bbox": null}
  },
  "missing_required": [],
  "validation": [],
  "low_confidence": ["isin"]
}
```

An illustrative validation issue (not produced by the mock slips, which are internally consistent):

```json
{"code": "CONSIDERATION_MISMATCH", "severity": "ERROR",
 "message": "principal_amount + accrued_interest (52264305.56) differs from consideration (52264350.56) by 45.00",
 "fields": ["principal_amount", "accrued_interest", "consideration"],
 "expected": "52264305.56", "actual": "52264350.56", "tolerance": "0.01"}
```

### 5.10 Multi-deal JSON (`GET /api/v1/exports/deals?format=json`) **[A]**

```json
{
  "generated_at": "2026-09-27T10:15:00.000000Z",
  "count": 1,
  "filters": {"status": "APPROVED", "from": "2026-09-01", "to": "2026-09-30"},
  "items": [
    {"slip_id": "0f8c2d6e-…", "status": "APPROVED", "file": "01_gsec_outright_purchase.pdf",
     "template_id": "orca_fi_desk_gsec_deal_slip", "template_version": 1, "deal": {"deal_id": "GS/NDSOM/2026/004571", "…": "…"}}
  ]
}
```

### 5.11 JSON serialisation rules

| Rule | Detail |
|---|---|
| Encoding | UTF-8, no BOM; exports pretty-printed with 2-space indent, API responses compact |
| Keys | Fixed order as in this document; `yield` is emitted by alias (Python attribute `yield_`) |
| Nulls | Always emitted (`"broker": null`), never omitted |
| Decimals | Strings in plain notation (`"50000000"`, never `"5E+7"`) |
| Ints | JSON numbers |
| Scores | JSON numbers (`confidence`, `score`, `market_confidence`) |
| Dates / timestamps | `"2026-09-24"`; `"2026-09-27T10:15:00.000000Z"` |

---

## 6. XML output

Produced by `app/export/xml_export.py` with `xml.etree.ElementTree` (XSD after MVP).

### 6.1 Structure rules

| Rule | Detail |
|---|---|
| Declaration | `<?xml version="1.0" encoding="UTF-8"?>`, 2-space indentation (`ET.indent`) |
| Root | Single slip: `<parse_result schema_version="1">` containing the envelope keys in JSON order. Many deals: `<deals generated_at="…" count="n">` with one `<deal_record slip_id="…" status="…" file="…" template_id="…" template_version="…">` per slip, each containing a `<deal>` **[A]** |
| Objects | One child element per key, in JSON key order |
| Lists | Container element named after the key; one `<item>` child per entry; nested lists nest `<item>` (e.g. `page_sizes`, bboxes) |
| Nulls | Empty element with `null="true"`: `<broker null="true" />` |
| Empty lists | Empty container without attribute: `<validation />` (distinguishes `[]` from `null`) |
| Scalars | Same text as the JSON value: decimals in plain notation, dates ISO, booleans `true`/`false`, floats as in JSON |
| Text escaping | ElementTree escapes `&`, `<`, `>`; control characters other than tab/LF/CR are removed |
| Tag sanitising | Key → tag: every character outside `[A-Za-z0-9_.-]` becomes `_`; if the result does not start with a letter or `_`, or starts with `xml` (any case), it is prefixed with `_`; when the tag differs from the key the original key is kept in a `name` attribute (e.g. a label-map key `Stl Dt` → `<Stl_Dt name="Stl Dt">`). All canonical field names are already valid tags; `repo.leg1_date` keys in `fields` stay as is (dots are legal). |

### 6.2 XML example — same slip as §5.8

The `deal` element is complete. `fields` and `key_values` show the first entries; the remaining
entries follow the identical pattern (one element per `fields` key, one `<item>` per key/value
pair; the JSON in §5.8 lists them all).

```xml
<?xml version="1.0" encoding="UTF-8"?>
<parse_result schema_version="1">
  <status>PARSED</status>
  <source>
    <file>01_gsec_outright_purchase.pdf</file>
    <pages>1</pages>
    <page_sizes>
      <item>
        <item>595.3</item>
        <item>841.9</item>
      </item>
    </page_sizes>
    <sha256>83a4c9f3ff8e0ad69590d764143f8d2bd7b89202a4d3c1349ff102d69cd9b057</sha256>
  </source>
  <market>
    <market>IN</market>
    <issuer_country>IN</issuer_country>
    <slip_locale>en-IN</slip_locale>
    <market_confidence>1.0</market_confidence>
    <profile_available>true</profile_available>
    <signals>
      <item>
        <signal>isin_prefix</signal>
        <value>IN</value>
        <market>IN</market>
        <weight>3</weight>
      </item>
      <item>
        <signal>currency</signal>
        <value>INR</value>
        <market>IN</market>
        <weight>2</weight>
      </item>
      <item>
        <signal>settlement_system</signal>
        <value>CCIL</value>
        <market>IN</market>
        <weight>2</weight>
      </item>
      <item>
        <signal>platform</signal>
        <value>NDS-OM</value>
        <market>IN</market>
        <weight>2</weight>
      </item>
      <item>
        <signal>name_style</signal>
        <value>7.10% GS 2034</value>
        <market>IN</market>
        <weight>1</weight>
      </item>
      <item>
        <signal>number_format</signal>
        <value>5,00,00,000.00</value>
        <market>IN</market>
        <weight>1</weight>
      </item>
    </signals>
  </market>
  <template>
    <template_id>orca_fi_desk_gsec_deal_slip</template_id>
    <name>Orca FI desk G-Sec deal slip</name>
    <version>1</version>
    <score>1.0</score>
    <threshold>0.8</threshold>
    <matched>true</matched>
    <is_draft>false</is_draft>
  </template>
  <deal>
    <deal_id>GS/NDSOM/2026/004571</deal_id>
    <deal_type>OUTRIGHT</deal_type>
    <instrument_type>GSEC</instrument_type>
    <buy_sell>BUY</buy_sell>
    <platform>NDS-OM</platform>
    <trade_date>2026-09-24</trade_date>
    <settlement_date>2026-09-25</settlement_date>
    <security_name>7.10% GS 2034</security_name>
    <issuer null="true" />
    <credit_rating null="true" />
    <isin>IN0020240A75</isin>
    <identifiers>
      <isin>IN0020240A75</isin>
      <cusip null="true" />
      <sedol null="true" />
      <common_code null="true" />
    </identifiers>
    <coupon_rate>7.10</coupon_rate>
    <coupon_frequency>2</coupon_frequency>
    <maturity_date>2034-04-08</maturity_date>
    <last_coupon_date>2026-04-08</last_coupon_date>
    <day_count>30/360</day_count>
    <tenor_days null="true" />
    <face_value>50000000</face_value>
    <face_value_per_unit null="true" />
    <quantity null="true" />
    <price>101.2350</price>
    <yield>6.8865</yield>
    <principal_amount>50617500.00</principal_amount>
    <accrued_days>167</accrued_days>
    <accrued_interest>1646805.56</accrued_interest>
    <discount_amount null="true" />
    <consideration>52264305.56</consideration>
    <currency>INR</currency>
    <settlement_currency null="true" />
    <fx_rate null="true" />
    <counterparty>Anonymous (NDS-OM) - CCIL Novated</counterparty>
    <broker null="true" />
    <settlement_mode>DVP-III</settlement_mode>
    <portfolio>AFS - Treasury</portfolio>
    <dealer>R. Mehta</dealer>
    <bid_type null="true" />
    <bid_amount null="true" />
    <issuer_country>IN</issuer_country>
    <market>IN</market>
    <slip_locale>en-IN</slip_locale>
    <market_confidence>1.0</market_confidence>
    <repo null="true" />
  </deal>
  <fields>
    <deal_id>
      <value>GS/NDSOM/2026/004571</value>
      <raw>GS/NDSOM/2026/004571</raw>
      <method>template</method>
      <confidence>1.0</confidence>
      <label>Deal Reference No.</label>
      <page>1</page>
      <bbox>
        <item>240.9</item>
        <item>123.2</item>
        <item>538.6</item>
        <item>141.2</item>
      </bbox>
    </deal_id>
    <buy_sell>
      <value>BUY</value>
      <raw>OUTRIGHT PURCHASE</raw>
      <method>derived</method>
      <confidence>0.9</confidence>
      <label>Deal Type</label>
      <page>1</page>
      <bbox>
        <item>240.9</item>
        <item>141.2</item>
        <item>538.6</item>
        <item>159.2</item>
      </bbox>
    </buy_sell>
    <face_value>
      <value>50000000</value>
      <raw>5,00,00,000.00</raw>
      <method>template</method>
      <confidence>1.0</confidence>
      <label>Face Value (INR)</label>
      <page>1</page>
      <bbox>
        <item>240.9</item>
        <item>339.2</item>
        <item>538.6</item>
        <item>357.2</item>
      </bbox>
    </face_value>
    <!-- … one element per remaining "fields" key (26 in total), same structure … -->
  </fields>
  <key_values>
    <item>
      <key>Deal Reference No.</key>
      <value>GS/NDSOM/2026/004571</value>
      <source>table</source>
      <page>1</page>
      <key_bbox>
        <item>56.7</item>
        <item>123.2</item>
        <item>240.9</item>
        <item>141.2</item>
      </key_bbox>
      <value_bbox>
        <item>240.9</item>
        <item>123.2</item>
        <item>538.6</item>
        <item>141.2</item>
      </value_bbox>
      <mapped_to>deal_id</mapped_to>
    </item>
    <item>
      <key>Deal Type</key>
      <value>OUTRIGHT PURCHASE</value>
      <source>table</source>
      <page>1</page>
      <key_bbox>
        <item>56.7</item>
        <item>141.2</item>
        <item>240.9</item>
        <item>159.2</item>
      </key_bbox>
      <value_bbox>
        <item>240.9</item>
        <item>141.2</item>
        <item>538.6</item>
        <item>159.2</item>
      </value_bbox>
      <mapped_to>deal_type</mapped_to>
    </item>
    <!-- … 22 more <item> elements, one per key/value pair … -->
  </key_values>
  <missing_required />
  <validation />
  <low_confidence />
  <unmapped>
    <item>
      <key>Trade Time</key>
      <value>11:42:17</value>
      <source>table</source>
      <page>1</page>
      <key_bbox>
        <item>56.7</item>
        <item>195.2</item>
        <item>240.9</item>
        <item>213.2</item>
      </key_bbox>
      <value_bbox>
        <item>240.9</item>
        <item>195.2</item>
        <item>538.6</item>
        <item>213.2</item>
      </value_bbox>
      <mapped_to null="true" />
    </item>
    <item>
      <key>SGL / CSGL A/c</key>
      <value>SGL-0457 (RBI)</value>
      <source>table</source>
      <page>1</page>
      <key_bbox>
        <item>56.7</item>
        <item>519.2</item>
        <item>240.9</item>
        <item>537.2</item>
      </key_bbox>
      <value_bbox>
        <item>240.9</item>
        <item>519.2</item>
        <item>538.6</item>
        <item>537.2</item>
      </value_bbox>
      <mapped_to null="true" />
    </item>
  </unmapped>
</parse_result>
```

---

## 7. Excel output

Produced by `app/export/excel_export.py` with openpyxl. Values only, no formulas.

### 7.1 Workbook layout

| Sheet | Present | One row per | Columns (in order) |
|---|---|---|---|
| `Deals` | always | slip | see §7.2 |
| `Fields` | single-slip export only | field with a value | `field`, `value`, `raw`, `method`, `confidence`, `label`, `page`, `bbox` |
| `Key Values` | always | extracted pair | `slip_id`, `page`, `source`, `key`, `value`, `mapped_to`, `key_bbox`, `value_bbox` |
| `Validation` | always | issue | `slip_id`, `code`, `severity`, `fields`, `message`, `expected`, `actual`, `tolerance` |

Sheet order: single slip `Deals, Fields, Key Values, Validation`; multi-deal
`Deals, Key Values, Validation`. A sheet without data still has its header row. In `Validation`,
each `missing_required` entry is written as a row with code `REQUIRED_MISSING` (ERROR) and each
`low_confidence` entry as `LOW_CONFIDENCE` (WARNING), so the sheet is a complete review list **[A]**.
`fields` is joined with `, `; bboxes are written as text `56.7, 123.2, 240.9, 141.2`.

### 7.2 `Deals` columns

| # | Column | Excel type | Number format |
|---|---|---|---|
| 1 | `slip_id` | text | `@` (empty for `/parse`) |
| 2 | `status` | text | |
| 3 | `file_name` | text | |
| 4 | `template_id` | text | |
| 5 | `template_version` | number | `0` |
| 6 | `match_score` | number | `0.0000` |
| 7–46 | `deal_id` … `bid_amount` in §1 order, with `identifiers` flattened after `isin` as `identifiers.cusip`, `identifiers.sedol`, `identifiers.common_code` | per §7.3 | per §7.3 |
| 47–50 | `issuer_country`, `market`, `slip_locale`, `market_confidence` | text / number | `0.00` for confidence |
| 51–65 | `repo.repo_rate`, `repo.repo_days`, `repo.day_count`, `repo.haircut`, `repo.leg1_date`, `repo.leg1_price`, `repo.leg1_accrued_days`, `repo.leg1_accrued_interest`, `repo.leg1_amount`, `repo.leg2_date`, `repo.leg2_price`, `repo.leg2_accrued_days`, `repo.leg2_accrued_interest`, `repo.leg2_amount`, `repo.repo_interest` | per §7.3 | per §7.3 |

`repo.*` cells are empty when `repo` is `null`. Column numbers 7–46 are indicative; the order is
the §1 order and is the contract.

### 7.3 Cell types by field kind

| Kind | Fields | Cell value | Number format |
|---|---|---|---|
| date | all `*_date` fields | real Excel date (`datetime.date`) | `dd-mmm-yyyy` (`24-Sep-2026`) |
| timestamp | (audit export) | naive UTC `datetime` | `yyyy-mm-dd hh:mm:ss` |
| nominal | `face_value`, `face_value_per_unit`, `bid_amount` | number (`Decimal`) | `#,##0` (`#,##0.##` if fractional) |
| cash | `principal_amount`, `accrued_interest`, `discount_amount`, `consideration`, `repo.leg*_accrued_interest`, `repo.leg*_amount`, `repo.repo_interest` | number | `#,##0.00` |
| price | `price`, `repo.leg*_price` | number | `0.0000` |
| yield | `yield` | number | `0.0000` |
| rate | `coupon_rate`, `repo.repo_rate`, `repo.haircut` | number | `0.00` |
| fx | `fx_rate` | number | `0.000000` |
| int | `coupon_frequency`, `tenor_days`, `quantity`, `accrued_days`, `repo.repo_days`, `repo.leg*_accrued_days` | number | `0` |
| score | `confidence`, `market_confidence` | number | `0.00` |
| text | everything else incl. `deal_id`, `isin` | text | `@` |

Western grouping is used in Excel formats; Indian lakh grouping is a display option after MVP **[A]**.

### 7.4 Styling and safety

| Item | Rule |
|---|---|
| Header row | Bold, white font, fill `#1F3B63`, centred, wrap text, row height 30 |
| Freeze / filter | Freeze panes at `A2`; auto-filter over the header range |
| Column width | `min(max(len(header), longest value) + 2, 50)` characters |
| Low confidence | In `Fields`, `confidence` cells below the threshold get fill `#FFE699` |
| Status | In `Deals`, `status` cells: `PARSED`/`APPROVED` green `#C6EFCE`, `NEEDS_REVIEW`/`NEW_TEMPLATE` amber `#FFE699`, `UNREADABLE`/`FAILED` red `#FFC7CE` **[A]** |
| Formula injection | Text starting with `=`, `+`, `-`, `@` is written with `data_type="s"` (never interpreted as a formula); same rule for the CSV audit export (prefixed with `'`) |
| Precision | Excel stores IEEE doubles (15 significant digits). All INR amounts in scope (< 10¹³ with 2 dp) are exact; JSON / XML remain the system of record |
| Properties | Workbook title = deal id or `deals`, creator `bonds-parser` |

---

## 8. Number, date and precision policy

### 8.1 Decimal handling

| Rule | Detail |
|---|---|
| Construction | `Decimal(str)` from slip text only; `Decimal(float)` is forbidden (lint rule) |
| Arithmetic | `decimal.localcontext(prec=34)`; derivations quantise to the field's scale with `ROUND_HALF_UP` (same as `tools/generate_mock_slips.py::q`) |
| Source values | Never rounded down: output scale = `max(kind minimum, scale printed on the slip)` |
| Comparison | Validation compares `Decimal`s with the tolerances in `docs/03_lld.md` §4.13 |
| Serialisation | `format(d, "f")`: plain notation, `.` decimal separator, no grouping, leading `-` for negatives, no `+` |
| Percentages | Stored as percent numbers (`"7.10"` = 7.10 %), never fractions |

### 8.2 Scale by kind

| Kind | Output scale | Examples |
|---|---|---|
| nominal | integral values without decimals; otherwise trailing zeros stripped | `5,00,00,000.00` → `"50000000"`; `5.00 Cr` → `"50000000"`; `1,00,000.00` → `"100000"` |
| cash | ≥ 2 dp | `16,46,805.56` → `"1646805.56"`; derived `45161.53` |
| price | ≥ 4 dp | `101.235` → `"101.2350"`; T-Bill `100/(1+0.056512×182/364)` → `"97.2520"` |
| yield | ≥ 4 dp | `6.8865` → `"6.8865"` |
| rate | ≥ 2 dp | `7.1% p.a.` → `"7.10"`; `5.4` → `"5.40"` |
| fx | as printed | `83.2150` → `"83.2150"` |
| int | integer | `167 (30/360)` → `167` |
| score | float, 2 dp (match score 4 dp) | `0.9`, `1.0`, `0.9642` |

### 8.3 Input number formats accepted (India profile)

| Printed | Parsed |
|---|---|
| `5,00,00,000.00` (Indian grouping) | `50000000.00` |
| `50,000,000.00` (western grouping) | `50000000.00` |
| `5.00 Cr`, `5 crore` | `50000000.00` |
| `50 Lakh`, `50 lacs` | `5000000` |
| `INR 30,00,00,000.00`, `Rs. 1,000/-`, `₹ 1,000` | currency / suffix stripped |
| `(1,234.50)`, `-1,234.50` | negative |
| `1,23,4567` (bad grouping) | error `AMOUNT_INVALID` |

### 8.4 Dates

| Printed | Output | Rule |
|---|---|---|
| `24-Sep-2026`, `24 September 2026`, `Sep 24, 2026` | `2026-09-24` | textual month: unambiguous |
| `2026-09-24` | `2026-09-24` | ISO: always Y-M-D |
| `24/09/2026`, `24.09.2026` | `2026-09-24` | day > 12: unambiguous |
| `05/09/2026` | `2026-09-05` (IN, DMY) | both ≤ 12: profile date order; no profile ⇒ `null` + `DATE_AMBIGUOUS` |
| `23-Sep-2026 15:07`, `25-Sep-2026 (T+1)` | `2026-09-23`, `2026-09-25` | time and notes stripped |
| `25032027` (in `182 DTB 25032027`) | `2027-03-25` | compact `ddmmyyyy` (DMY profile) |

Output: business dates `YYYY-MM-DD`; timestamps UTC `YYYY-MM-DDTHH:MM:SS.ffffffZ` (D11).

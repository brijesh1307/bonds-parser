# 03 · Low-Level Design (LLD)

Module-level design of `v0.1.0` as implemented. Names and values: [`00_design_baseline.md`](00_design_baseline.md)
(B§). Architecture: [`02_hld.md`](02_hld.md). The code is the final reference; this document explains it.

---

## 0. Conventions

| Topic | Convention |
|---|---|
| Coordinates | PDF points, pdfplumber orientation (origin top-left), `bbox = (x0, top, x1, bottom)` rounded to 0.1; pages 1-based |
| Money / rates | `decimal.Decimal` end to end; output scale by kind (nominal / cash / price / yield / rate / fx) |
| Dates | `datetime.date`; ISO on output; timestamps UTC `…Z` |
| Field paths | `face_value`, `repo.leg2_amount`, `identifiers.isin`; working fields start with `_` |
| Layering | `api → services → engine / export`; engine and export never import FastAPI or services (`tests/test_architecture.py`) |
| Python | 3.11+, full type hints, `mypy --strict`, `ruff` |

---

## 1. Module map

```
app/
├── main.py              create_app(): settings, stores, limiter, middleware, CORS, routers
├── config.py            Settings (BONDS_*), EngineSettings, get_settings()
├── errors.py            AppError hierarchy → HTTP status + problem type
├── auth.py              ClientStore (Argon2, JSON file), FailureLimiter
├── cli.py               debug-extract, add-client, list-clients, disable-client, rotate-secret, verify-audit
├── api/
│   ├── deps.py          require_client (HTTP Basic), RequestContext (+ .audit), store accessors
│   ├── errors.py        RFC 7807 handlers, PROBLEM_RESPONSES
│   ├── health.py        GET /health
│   ├── parse.py         POST /api/v1/parse
│   ├── templates.py     preview, approve, list, get, put, patch, import
│   └── audit.py         audit search / verify; schema fields / markets / xsd
├── schemas/             common (Problem, HealthOut, PlainDecimal), deal, slip (ParseResult …),
│                        template (MappingSpec, TemplateOut, ApproveResult …), audit
├── services/
│   ├── parse_service.py      read_upload, parse_once, to_schema
│   ├── template_service.py   validate_spec, DraftBuilder, preview, approve, put_version, patch, import
│   ├── template_store.py     TemplateStore (JSON file per template)
│   └── audit_service.py      AuditLog (append-only JSON lines, hash chain)
├── engine/
│   ├── fields.py        enums, FIELD_SPECS, synonyms, abbreviations, leg keys, required rules
│   ├── models.py        KeyValue, ExtractedDocument, FieldValue, Candidate, TemplateDefinition, …
│   ├── extract.py       PDF → ExtractedDocument
│   ├── market.py        detect_market (weighted vote)
│   ├── profiles/        IN.json + loader (get_profile, available_profiles)
│   ├── detect.py        fingerprint, jaccard, match_template
│   ├── mapping.py       lookup_label, collect_candidates, apply_rules, resolve
│   ├── normalize.py     dates, amounts, rates, ints, ISIN, PAN, enums, scale
│   ├── derive.py        reassign_per_unit_face_value, derive
│   ├── validate.py      validate, missing_required, low_confidence
│   └── pipeline.py      run, build_deal, decide_status
└── export/              json_export, xml_export (to_xml), excel_export (to_xlsx), xsd (build_xsd)
```

---

## 2. Engine data structures (`engine/models.py`)

```python
BBox = tuple[float, float, float, float]
Word = tuple[str, BBox]

KeyValue(key, value, source: Source, page, key_bbox: BBox | None, value_bbox: BBox | None)   # frozen
ExtractedDocument(pairs, text, pages, page_sizes, table_bboxes, has_text_layer, words)        # words per page
FieldValue(value, raw, method: Method, confidence, label, page, bbox)
Candidate(field, kv: KeyValue | None, raw, method, confidence)
RegionRule(field, page, bbox); RegexRule(field, pattern, group=1)
TemplateDefinition(template_id, version, market, fingerprint_labels, keywords, match_threshold,
                   label_map, region_rules, regex_rules, constants, ignore, accepted_derived,
                   date_order, name)    # from_json / to_json = B§6 shape
MarketProfile(code, locale, currency, date_order, amount_units, weekend, holidays,
              default_day_count, default_coupon_frequency, platforms, settlement_systems,
              instrument_patterns, tbill_basis)
MarketDetection(market, issuer_country, slip_locale, market_confidence, signals, profile)
TemplateMatchResult(template, score, threshold, matched, is_draft)
Issue(code, severity, message, fields, expected, actual, tolerance)
EngineResult(status, file_name, pages, page_sizes, sha256, market, template, deal, fields,
             key_values, mapped_to, missing_required, validation, low_confidence, unmapped)
```

Enums (`engine/fields.py`): `SlipStatus, DealType, InstrumentType, BuySell, DayCount, BidType,
Market, Method, Source, Severity`. `METHOD_SCORE` = B§4 table; `FUZZY_FACTOR = 0.85`;
`ACCEPTED_DERIVED_SCORE = 0.95`; `IGNORE = "_ignore"`.

---

## 3. Algorithms

### 3.1 Extraction (`extract.py`)

```
extract(pdf, max_pages):
    open with pdfplumber; encrypted → EncryptedPdfError; unreadable → InvalidPdfError
    pages > max_pages → TooManyPagesError
    per page: tables = find_tables(); pairs += table pairs; pairs += text-line pairs outside tables;
              words (text, bbox) kept for region rules
    has_text_layer = ≥ 20 non-whitespace characters; then prose pairs over the full text
```

**Table classification** (`classify_table`, after `repair_isin_overflow` and `clean_cell`):

| Kind | Rule |
|---|---|
| `KV` | 2 columns, column 0 label-like on ≥ 80 % of rows |
| `KV_ROWS` | 4 columns with columns 0 and 2 label-like on ≥ 80 % of rows; **or** ≥ 3 columns, ≥ 3 rows, column 0 label-like ≥ 80 %, and some merged / empty cells (dealer-to-client letters) |
| `GRID` | ≥ 2 rows and a header row whose (≥ 2) cells are all label-like |
| `UNKNOWN` | otherwise (its words are read as text lines) |

`label_like(s)`: has letters, ≤ 60 characters, < 30 % digits.

**KV / KV_ROWS rows** — walk the non-empty cells left to right: a label-like cell pairs with the
next cell unless it is itself "label + value" (`X: value`, or ends in a PAN / ISIN); a leftover
cell is split by `inline_pair` (`SELLER PAN No. ABCPD1234E`, `PAN:- X`). Each value cell is also
checked by `sub_pairs`: when it holds **two or more** labelled parts (`A : x | B : y`,
`Date – … ⏎ Level – …`) each part is emitted as `"<part label> (<cell label>)"`.

**GRID** — one value row: header cell → value; several rows (repo legs): key =
`"<row label> <header>"`, e.g. `Second Leg Settlement Amount`.

**Text lines** — words outside table boxes are grouped into lines (≤ 3 pt), split into segments
on gaps > 10 pt and on `|`; colon pairs (`: ` or `:-`) per segment, otherwise a two-segment
`Label    value` line.

**Prose** — valid ISINs; `competitive / non-competitive bid`; `NNN-day treasury bill`;
`at a yield of X%`; `conducted by the Reserve Bank of India`; `(A. Name) ⏎ Dealer`.

### 3.2 Market vote (`market.py`)

Signals, each type voting at most once per market: `isin_prefix` (3), `currency` (2; INR / Rs /
₹ / Rupees → IN), `settlement_system` (2; CCIL, NSDL, CDSL, NCL, ICCL, SGL…), `platform` (2;
NDS-OM, CROMS, E-Kuber, NSE/BSE RFQ, EBP, TREPS, ICDM), `name_style` (1), `number_format` (1;
Indian grouping). `confidence = winner / total`; below **0.70** → `UNKNOWN`. The profile comes
from `profiles/<CODE>.json` when `status` is `active` (only `IN`).

### 3.3 Template match (`detect.py`)

`fingerprint(doc)` = normalised full labels of table / grid / text pairs (not prose).
`match_template`: skip templates whose `keywords` are not all in the text; score =
Jaccard(fingerprint, template labels) rounded to 4 dp; best score ≥ the template's threshold
wins. A draft (preview / approve) is always "matched" with `is_draft = true`.

### 3.4 Label lookup and candidates (`mapping.py`)

```
collect_candidates(doc, template):
    for each pair: template.ignore / label_map "_ignore" → ignored (not unmapped)
                   template.label_map hit → (path, template, 1.00)
                   else lookup_label(key) → list of (path, method, confidence)
    prose pairs count as method text (0.80) unless the template mapped them

lookup_label(label):   keys = (full, without parentheses)
    synonym or repo-leg key (0.95) → abbreviation expansion (0.90) →
    compound label: split on "/" and look up each part (SECURITY NAME/ISIN NUMBER → 2 fields) →
    fuzzy: difflib ≥ 0.85 on keys ≥ 6 chars, confidence = ratio × 0.85

apply_rules(doc, template, cands):
    region rule → words fully inside the box (±1 pt) → candidate (region, 1.00); none → RULE_NO_MATCH warning
    regex rule  → re.search over the slip text, capture group → candidate (regex, 0.95)
    constant    → only when no region / regex / template candidate exists (constant, 1.00)
```

**Resolution** (`resolve`): per field, prefer candidates whose value normalised; order by
method rank (region < regex < template < constant < synonym < abbreviation < fuzzy < text <
derived), confidence, source (table < grid < text < prose), page position. A same-rank rival
with a different value → `CONFLICTING_VALUES` warning and confidence capped at 0.85.

### 3.5 Normalisation (`normalize.py`)

| Kind | Rule |
|---|---|
| Date | Drop times, `(…)`, weekday names, ordinals; ISO; `d-Mon-yyyy`; `Month d yyyy`; numeric with day / month disambiguation (value > 12), then template `date_order`, then profile order (IN: DMY), else `DATE_AMBIGUOUS`; compact `ddmmyyyy` / `yyyymmdd` |
| Amount | Strip currency tokens and `/-`; Indian or western grouping only; unit after the number or in the label (`Nominal (Cr)`) from the profile (Cr 10⁷, Lakh 10⁵, Mn 10⁶); `(x)` / `-x` negative |
| Rate / price | First number, printed precision kept |
| Int | Leading integer (`167 (30/360)` → 167) |
| ISIN | Whole-word token first, then with spaces removed; check-digit-valid preferred |
| PAN | `[A-Z]{5}\d{4}[A-Z]` |
| Security name | ISIN printed in the cell removed (`(ISIN - X)`, `/ X`) |
| Enums | `deal_type`, `buy_sell`, `day_count`, `bid_type`, `instrument_type` (profile patterns); unknown → `ENUM_UNKNOWN` |
| Platform / settlement mode / broker | Profile platform names; `DVP-I/II/III`; "Direct / No Broker" → null |
| Scale | nominal: integral → no decimals; cash ≥ 2 dp; price, yield ≥ 4 dp; rate ≥ 2 dp; fx as printed |

### 3.6 Derivation (`derive.py`)

Before resolution, `reassign_per_unit_face_value`: two face-value candidates where quantity ×
smaller = larger → the smaller becomes `face_value_per_unit`.

After resolution, rules fill **only empty** fields (method `derived` 0.90, or `text` 0.80 when
read from the whole slip): deal type from text; `buy_sell` from the deal-type words or the deal
type's usual side; counterparty from `Our Buy from:- X` / `Our Sell to:- X`; our side's amount
(BUY → buyer amount, SELL → seller amount), counterparty PAN, principal from the seller amount,
stamp duty = buyer − seller; instrument type from name / ISIN; `is_market_linked`; platform from
the deal type or text; coupon from the name; coupon frequency from the coupon text or profile
default (not for MLDs or discount instruments); day count from `(30/360)` or profile default when
accrual is shown; T-Bill tenor and maturity from `182 DTB ddmmyyyy`; currency from `(INR)` /
`(Rs.)` labels or the profile; DVP from text; RBI as counterparty of E-Kuber auctions; repo days,
repo interest and leg 1 → top level; face value ↔ quantity × per-unit; T-Bill price from yield;
`identifiers.isin`.

After derivation, a matched template's `accepted_derived` fields with method derived / text /
fuzzy are raised to 0.95.

### 3.7 Validation (`validate.py`)

| Code | Severity | Rule |
|---|---|---|
| `ISIN_FORMAT`, `ISIN_CHECK_DIGIT` | ERROR | 12 characters, ISO 6166 check digit |
| `SETTLEMENT_BEFORE_TRADE`, `MATURITY_NOT_AFTER_SETTLEMENT` | ERROR | date order |
| `WEEKEND_SETTLEMENT` | ERROR | settlement / repo leg dates on the profile weekend |
| `PRINCIPAL_MISMATCH` | ERROR | face × price / 100 within a tolerance scaled to the price's printed decimals |
| `CONSIDERATION_MISMATCH` | ERROR | outright: principal + accrued (+ stamp duty when BUY) ± 0.01 |
| `ACCRUED_DAYS_MISMATCH` | WARNING | 30/360 or actual days since the last coupon |
| `ACCRUED_MISMATCH` | ERROR | face × coupon × days / basis ± 0.01 |
| `DISCOUNT_MISMATCH`, `DISCOUNT_PRICE_MISMATCH` | ERROR | discount instruments |
| `QUANTITY_MISMATCH` | ERROR | quantity × per-unit = face value |
| `REPO_LEG_AMOUNT_MISMATCH`, `REPO_INTEREST_MISMATCH`, `REPO_DAYS_MISMATCH` | ERROR | repo legs |
| `REPO_INTEREST_CALC` | WARNING | leg 1 × rate × days / basis |
| `CONFLICTING_VALUES`, `RULE_NO_MATCH`, `MARKET_UNCERTAIN`, `MARKET_PROFILE_UNAVAILABLE` | WARNING | earlier steps |
| `DATE_*`, `AMOUNT_INVALID`, `VALUE_UNPARSEABLE`, `ENUM_UNKNOWN` | ERROR | no candidate of a field parsed |

`missing_required` uses `required_fields(deal_type)` (B§5); `low_confidence` lists required
fields with confidence < `BONDS_CONFIDENCE_THRESHOLD`.

### 3.8 Pipeline and status (`pipeline.py`)

```
run(pdf, *, file_name, sha256, es, templates=(), draft=None):
    doc = extract(pdf);  no text layer → UNREADABLE result
    market = detect_market(doc)
    match = draft ? draft(doc, market) as a draft match : match_template(doc, templates, threshold)
    market pinned to the template's market when the vote is UNKNOWN
    candidates (+ template rules) → normalise (profile, template date_order) → per-unit face value
    → resolve → derive → accepted_derived → build_deal → validate → missing / low confidence
    status = decide_status(...)

decide_status: no text → UNREADABLE; no template → NEW_TEMPLATE; missing, low confidence, ERROR
               issue, no profile or UNKNOWN market → NEEDS_REVIEW; else PARSED
```

`build_deal` writes every canonical key (null when unknown), the `repo` object for repo deal
types, and the market fields; working fields (`_…`) never reach the deal or `fields`.

---

## 4. Services

### 4.1 `parse_service`

`read_upload(upload, settings) → (bytes, sha256)`: 64 KiB chunks, stop at
`max_upload_bytes` (413), require `%PDF-` (415). `parse_once(upload, settings, store) →
(EngineResult, size)` runs the pipeline with `store.active_definitions()`. `to_schema(result) →
ParseResult` (decimals as strings, bboxes as lists).

### 4.2 `template_service`

- `validate_spec(spec)`: every `label_map` target, rule field and constant is a valid path; regexes
  compile and have the requested group; boxes are well-formed → `MappingValidationError` (400)
  with one entry per problem.
- `DraftBuilder(spec, template_id, version, base, default_threshold, name)`: callable used by the
  pipeline; merges the base version's label map with the spec (spec wins; `_ignore` moves a
  label to `ignore`), takes the slip's fingerprint, the detected market (or the base's),
  keywords / rules / constants from the spec or the base, and keeps the built definition.
- `preview(...)`: validate → draft run → `EngineResult` (nothing saved).
- `approve(...)`: validate → draft run → 422 if market UNKNOWN or required fields missing → 409 if
  ERROR issues and not accepted → `accepted_derived` += required fields found by derived / text /
  fuzzy → `store.create` or `store.add_version` → re-run with the saved definition → `(record,
  result, created)`.
- `put_version`, `patch`, `import_template`, `to_out`.

### 4.3 `TemplateStore` (`template_store.py`)

One file `<id>.json` per template (B§6); ids `^[a-z0-9][a-z0-9_]{0,99}$` (slug of the name, `_2`,
`_3` on clashes). Writes: temp file in the same folder + `os.replace` (atomic), under a process
lock. `find(is_active, market)`, `get`, `active_definitions`, `create`, `add_version` (only
`current + 1`), `patch` (returns the diff), `writable`.

### 4.4 `AuditLog` (`audit_service.py`)

`record(action, …)`: under a lock, take the last `(seq, row_hash)` (read once, then cached), build
the record (B§6 fields), mask PAN-shaped text in `actor_name`, `error` and `details`, set
`row_hash = SHA-256(canonical JSON without row_hash)`, append one line, flush and `fsync`.
`search(...)` filters and returns newest first; `verify()` recomputes the chain and reports the
first bad `seq` with `prev_hash_mismatch` or `row_hash_mismatch`.

### 4.5 Auth (`auth.py`, `api/deps.py`)

`ClientStore`: `clients.json` with Argon2id hashes; `add` / `rotate` return a fresh
`secrets.token_urlsafe(32)` once; `verify` always runs one Argon2 verification (a dummy hash for
unknown clients) and returns `(ok, reason)`; `last_used_at` persisted at most once a minute.
`FailureLimiter`: per-IP failure timestamps in a sliding window; at the limit the IP is blocked
for `block_seconds` (429 with `Retry-After`). `require_client` (FastAPI dependency with
`HTTPBasic`): limiter check → verify → on failure audit `AUTH_FAILED` (and `AUTH_BLOCKED`) and
raise 401; on success set `request.state.client_id` and return a `RequestContext`.

---

## 5. Sequences

**Parse**
```
client → POST /api/v1/parse (Basic, file)
  require_client → limiter → ClientStore.verify
  parse_service.read_upload (413 / 415) → pipeline.run(templates = active)
  audit SLIP_PARSED {sha256, size, pages, status, market, template…}
  ← JSON | XML | XLSX          (PDF bytes dropped when the request ends)
```

**Approve**
```
client → POST /api/v1/templates (file, mapping)
  _spec(mapping) (400) → validate_spec (400) → read_upload
  store.get(template_id)? → DraftBuilder → pipeline.run(draft)
  422 / 409 checks → store.create | store.add_version (atomic write)
  pipeline.run(templates = [saved]) → audit TEMPLATE_CREATED | TEMPLATE_VERSION_ADDED
  ← 201 {template, result}
```

**Failed login**
```
require_client → verify fails → audit AUTH_FAILED → limiter.fail(ip) → (blocked → audit AUTH_BLOCKED)
  ← 401 problem (WWW-Authenticate); later requests from the IP ← 429 until the block ends
```

---

## 6. Files

| File | Format |
|---|---|
| `templates/<id>.json` | `{id, name, description, market, is_active, current_version, created_by, created_at, updated_at, versions: [{version, definition, note, created_by, created_at}]}` |
| `data/clients.json` | `{clients: [{client_id, secret_hash, description, is_active, created_at, last_used_at, rotated_at}]}` |
| `data/audit/audit.jsonl` | one canonical-JSON record per line (B§6) |

---

## 7. Errors

| Exception (`app/errors.py`) | HTTP | Problem slug |
|---|---|---|
| `BadRequestError` | 400 | `bad-request` |
| `MappingValidationError` | 400 | `invalid-mapping` |
| `AuthError` | 401 | `unauthorized` |
| `NotFoundError` | 404 | `not-found` |
| `InvalidStateError` | 409 | `invalid-state` |
| `ValidationNotAcceptedError` | 409 | `validation-not-accepted` |
| `ConflictError` | 409 | `conflict` |
| `PayloadTooLargeError` | 413 | `payload-too-large` |
| `UnsupportedMediaTypeError` | 415 | `unsupported-media-type` |
| `TooManyPagesError`, `EncryptedPdfError`, `InvalidPdfError` | 422 | `too-many-pages`, `encrypted-pdf`, `invalid-pdf` |
| `ApprovalBlockedError` | 422 | `approval-blocked` |
| `AuthBlockedError` | 429 | `auth-blocked` |
| `ServiceUnavailableError` | 503 | `service-unavailable` |
| FastAPI `RequestValidationError` | 422 | `request-validation` |
| any other exception | 500 | `internal-error` (logged with request id; no internals in the body) |
| `ConfigError` | – | invalid `BONDS_*` value; the process refuses to start |

---

## 8. Runtime concerns

| Concern | Rule |
|---|---|
| Concurrency | Handlers are sync (thread pool). Template store, client store and audit log each serialise writes with a lock; run **one** process per data folder (ADR-0009) |
| Atomicity | Templates and clients: temp file + `os.replace`; audit: append + `fsync` |
| Memory | A request holds at most `BONDS_MAX_UPLOAD_MB` of PDF plus the parse result |
| Logging | Access log: method, path, status, duration, client id, request id. Never logged: credentials, PDF text, deal values |
| Request id | `X-Request-ID` validated or generated in middleware; echoed on every response; stored in audit |

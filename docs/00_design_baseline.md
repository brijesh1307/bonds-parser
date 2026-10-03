# 00 · Design Baseline (single source of truth)

Every other document in `docs/` uses the names, values and decisions below. If a document
disagrees with this file, this file wins and the other document is wrong.

> **2026-10-02 — stateless design.** Deal slips are **never stored**: no database, no S3, no files.
> A PDF is processed in memory and discarded; the result is returned and not kept. There is no
> database at all: templates, API clients and the audit log are files. Documents written before
> this date that describe `slips` tables, `/api/v1/slips/*` endpoints, SQLite/PostgreSQL or
> uploads storage are superseded by this file (see ADR-0009).

---

## 1. Product in one paragraph

**Bonds Deal Slip Parser** is a stateless Python REST API (FastAPI, Swagger UI) that accepts a bond
deal slip PDF, extracts every label/value on it, maps them to one canonical deal schema, validates
the numbers, and returns the deal as JSON, XML or Excel. **Nothing from the slip is stored.** Slip
layouts it has not seen before are returned with all extracted key/value pairs (with page
positions) so a separate frontend project can let a user map them once; the frontend sends the PDF
and the mapping back to preview and approve. The approved mapping is saved as a versioned
**template** (labels and rules only, never slip data), and every later slip in that layout parses
automatically. Every action is written to a tamper-evident, metadata-only audit log. Access is
controlled by client credentials sent in the HTTP header.

**Out of scope:** the frontend (separate project), RBAC / user roles, SSO, storing slips or deals.

---

## 2. Key decisions

| # | Decision | Choice |
|---|---|---|
| D1 | Language / framework | Python 3.11+, FastAPI, Pydantic v2, Uvicorn |
| D2 | API docs | Swagger UI at `/docs`, ReDoc at `/redoc`, OpenAPI JSON at `/openapi.json` |
| D3 | PDF extraction | pdfplumber (text layer, words with bbox, tables). OCR (Tesseract) later |
| D4 | Storage | **No database.** Slips and deals are never stored. Files only for templates, clients, audit |
| D5 | Slip handling | PDF read into memory, parsed, discarded at the end of the request |
| D6 | Parsing approach | Template-driven, with a generic fallback (synonyms → abbreviations → compound labels → fuzzy). No LLM / no external calls with slip data |
| D7 | Templates | One JSON file per template in `BONDS_TEMPLATES_DIR` (default `./templates`), all versions inside, versions never overwritten; reviewable in git |
| D8 | Auth | **HTTP Basic** `Authorization: Basic base64(client_id:client_secret)`; clients in `BONDS_CLIENTS_FILE` with Argon2 hashes; no RBAC |
| D9 | Audit | Append-only JSON-lines file `BONDS_AUDIT_FILE`, SHA-256 hash chain, **metadata only** (no file names, no deal values, no PANs) |
| D10 | Money / rates | `Decimal` everywhere, serialised as strings in JSON/XML; never float |
| D11 | Dates | ISO `YYYY-MM-DD` in outputs; timestamps UTC ISO-8601 |
| D12 | Markets | Market detected before normalisation (vote of signals). India (`IN`) fully parsed; other markets detected and sent to review |
| D13 | API versioning | All business endpoints under `/api/v1`; `/health`, `/docs`, `/redoc` at root |
| D14 | Errors | RFC 7807 `application/problem+json` |
| D15 | Outputs | JSON, XML (ElementTree), Excel (openpyxl) |

---

## 3. Parse status

Every parse (one-shot or preview) returns one status. Nothing is stored, so there is no slip
lifecycle; approving creates a **template**, not a slip state.

| Status | Meaning |
|---|---|
| `PARSED` | Known template matched **and** all required fields present **and** validation passed **and** every required field confidence ≥ threshold **and** market profile available. Direct answer. |
| `NEEDS_REVIEW` | Template matched, but a required field is missing, validation failed (ERROR), confidence low, or market profile not available / market uncertain. |
| `NEW_TEMPLATE` | No template matched (score < match threshold). Best-effort deal still returned plus all key/values. |
| `UNREADABLE` | No text layer (scanned image) — OCR needed. |
| `FAILED` | Unexpected internal error (500 with problem+json; outcome recorded in the audit log). |

A preview with a draft mapping reports the status the slip *would* get with that mapping.

---

## 4. Processing pipeline

```
1 Receive     read upload into memory, size limit (413), %PDF- check (415), SHA-256 (audit only)
2 Extract     pdfplumber → KeyValue candidates {key, value, source, page, key_bbox, value_bbox}
              sources: table (label|value, 4-column, merged-cell rows, label+value in one cell,
                       several "Label : value" parts in one cell), grid (header row + value rows),
                       text ("Label: value", "Label :- value", "A: x | B: y"), prose (regex)
3 Market      vote: ISIN prefix, currency, settlement system, platform, name style, number
              format → market, issuer_country, slip_locale, market_confidence
4 Detect      fingerprint = set of normalised labels; keyword gate; Jaccard vs active templates;
              best ≥ threshold (default 0.80) wins
5 Map         template label_map → built-in synonyms → abbreviation expansion → compound labels
              ("SECURITY NAME/ISIN NUMBER") → fuzzy
6 Rules       template region_rules (bbox), regex_rules, constants
7 Normalise   per market profile: amounts (lakh/crore), dates (day/month order, weekdays), rates,
              ISIN, PAN, enums
8 Derive      side + counterparty from "Our Buy from:-", per-unit face value, buyer/seller amounts,
              stamp duty, coupon/tenor from name, repo leg 1 → top level, platform, currency, …
9 Validate    ISIN check digit, dates, weekend, principal, consideration (incl. stamp duty),
              accrued, discount, quantity, repo legs
10 Decide     status per §3
11 Respond    JSON / XML / Excel; the PDF and the result are then discarded
```

### Confidence per field (`method` → score)

| method | score | meaning |
|---|---|---|
| `template` | 1.00 | label found in the matched template's label_map |
| `region` | 1.00 | read from a template bbox rule |
| `constant` | 1.00 | template constant |
| `regex` | 0.95 | template regex rule |
| `synonym` | 0.95 | built-in synonym (incl. each part of a compound label) |
| `abbreviation` | 0.90 | synonym after abbreviation expansion (`Stl Dt` → settlement date) |
| `derived` | 0.90 | computed from another field |
| `fuzzy` | ≤ 0.85 | fuzzy label match (ratio × 0.85) |
| `text` | 0.80 | found by searching the full slip text |

**Unit in the label:** when a label carries a unit and the value does not (`Nominal (Cr)` = `5.00`),
the normaliser applies the label's multiplier (Cr ×10^7, Lakh ×10^5, Mn ×10^6).

**Market confidence:** the market vote must reach **0.70**; below that `market = UNKNOWN` and the
slip goes to `NEEDS_REVIEW`. A template may pin `market` and a `date_order` (`DMY` / `MDY`).

Confidence threshold default **0.90** (`BONDS_CONFIDENCE_THRESHOLD`). Template match threshold
default **0.80** (`BONDS_TEMPLATE_MATCH_THRESHOLD`). A template may list `accepted_derived` fields
(the reviewer accepted a text/derived value for that layout) → those fields get confidence 0.95 when
that template matches.

---

## 5. Canonical deal schema (summary)

Full definitions: `docs/05_data_dictionary_and_outputs.md`.

Top-level fields: `deal_id, deal_type, instrument_type, buy_sell, platform, trade_date,
settlement_date, security_name, issuer, credit_rating, isin, identifiers{isin,cusip,sedol,common_code},
coupon_rate, coupon_frequency, maturity_date, last_coupon_date, day_count, tenor_days, face_value,
face_value_per_unit, quantity, price, yield, principal_amount, accrued_days, accrued_interest,
discount_amount, consideration, stamp_duty, settlement_reference, currency, settlement_currency, fx_rate,
counterparty, counterparty_pan, is_market_linked, broker, settlement_mode, portfolio, dealer, bid_type,
bid_amount, issuer_country, market, slip_locale, market_confidence, repo{…}`

`repo` object: `repo_rate, repo_days, day_count, haircut, leg1_date, leg1_price, leg1_accrued_days,
leg1_accrued_interest, leg1_amount, leg2_date, leg2_price, leg2_accrued_days,
leg2_accrued_interest, leg2_amount, repo_interest`

Working fields (mappable, used by derivation, never in the deal output): `_seller_amount,
_buyer_amount, _seller_pan, _buyer_pan, _seller_name, _buyer_name`.

Client confirmation letters (JM Financial MLD format, 2026-09-27):
- `stamp_duty` (decimal, cash): stamp duty on the trade; buyer amount = principal + stamp duty.
- `settlement_reference` (str): clearing settlement number (`SETTLEMENT NO.`); not unique per deal.
- `counterparty_pan` (str): counterparty's Indian PAN, output **in full** in the response (JSON/XML/
  Excel) by decision. Personal data: never logged and never written to the audit log.
- `is_market_linked` (bool): `true` for Market Linked Debentures; `instrument_type` stays `CORPORATE_BOND`.
- "Our side" is JM Financial: `Our Buy from:- <client>` = `BUY`, counterparty = client,
  `consideration` = buyer settlement amount (incl. stamp duty), `principal_amount` = seller amount.

Enums:
- `deal_type`: `OUTRIGHT, PRIMARY_AUCTION, PRIMARY_PLACEMENT, REPO, REVERSE_REPO, TREPS_BORROW, TREPS_LEND, LAF`
- `instrument_type`: `GSEC, SDL, TBILL, CMB, CORPORATE_BOND, PSU_BOND, CP, CD` (+ `UST, GILT, BUND, JGB, EUROBOND`)
- `buy_sell`: `BUY, SELL`
- `day_count`: `30/360, 30E/360, ACT/ACT, ACT/365, ACT/364, ACT/360`
- `bid_type`: `COMPETITIVE, NON_COMPETITIVE`
- `market`: `IN, US, GB, DE, JP, INTL, UNKNOWN`

Required (all deal types): `deal_type, trade_date, settlement_date, security_name, isin, face_value,
consideration`. `deal_id` is optional (many slips print none). Plus: `OUTRIGHT` → `buy_sell, price`;
`PRIMARY_AUCTION` → `price`; `REPO`/`REVERSE_REPO` → `repo.repo_rate, repo.leg1_amount,
repo.leg2_date, repo.leg2_amount`.

---

## 6. Files (no database)

| File | Content | Never contains |
|---|---|---|
| `BONDS_TEMPLATES_DIR/<template_id>.json` (default `./templates`) | One template: `id, name, description, market, is_active, current_version, created_by, created_at, updated_at, versions[]` where each version = `{version, definition, note, created_by, created_at}`. Written atomically (temp file + rename). | PDFs, deal values, PANs |
| `BONDS_CLIENTS_FILE` (default `./data/clients.json`, git-ignored) | API clients: `client_id, secret_hash (Argon2), description, is_active, created_at, last_used_at, rotated_at` | plain secrets |
| `BONDS_AUDIT_FILE` (default `./data/audit/audit.jsonl`, git-ignored) | One JSON object per line, append-only, hash-chained | file names, deal values, PANs, secrets |

### Template definition (`versions[].definition`)

```json
{
  "template_id": "broker_xyz_gsec_confirm",
  "version": 2,
  "market": "IN",
  "fingerprint": {"labels": ["contract no", "stl dt", "paper"], "keywords": ["XYZ Securities"]},
  "match_threshold": 0.80,
  "label_map": {"contract no": "deal_id", "stl dt": "settlement_date", "paper": "security_name"},
  "region_rules": [{"field": "counterparty", "page": 1, "bbox": [320, 140, 560, 158]}],
  "regex_rules": [{"field": "yield", "pattern": "at a yield of ([\\d.]+)%", "group": 1}],
  "constants": {"platform": "OTC", "currency": "INR"},
  "ignore": ["gst no", "page"],
  "accepted_derived": ["dealer"],
  "date_order": null
}
```

### Audit record (one line of `audit.jsonl`)

`seq, event_id, occurred_at, actor_type (client/system/cli), actor_id, actor_name, action,
entity_type (parse/template/client/audit), entity_id, outcome (SUCCESS/FAILURE), error, details,
request_id, ip_address, user_agent, prev_hash, row_hash`.

`row_hash = SHA-256(canonical JSON of every field except row_hash)`; the first record's `prev_hash`
is 64 zeros. `details` holds metadata only, e.g. for a parse: `{sha256, size, pages, status, market,
template_id, template_version, match_score, format}`.

### Audit actions

`SLIP_PARSED, TEMPLATE_PREVIEWED, TEMPLATE_CREATED, TEMPLATE_VERSION_ADDED, TEMPLATE_UPDATED,
TEMPLATE_ENABLED, TEMPLATE_DISABLED, TEMPLATE_IMPORTED, AUTH_FAILED, AUTH_BLOCKED, CLIENT_ADDED,
CLIENT_DISABLED, CLIENT_SECRET_ROTATED, AUDIT_EXPORTED`. Reads (list / get / history / verify /
metrics) are not audited.

---

## 7. API endpoints

All under `/api/v1` except System. All require header credentials except `/health`.

| Tag | Method | Path | Purpose |
|---|---|---|---|
| System | GET | `/health` | Liveness + template store check (open) |
| | GET | `/metrics` | Prometheus metrics (credentials required) |
| Parse | POST | `/api/v1/parse?format=json\|xml\|xlsx` | Parse one PDF with the active templates; nothing stored |
| Templates | POST | `/api/v1/templates/preview` | PDF + draft mapping → the result that mapping would give (nothing saved) |
| | POST | `/api/v1/templates` | PDF + approved mapping → create a template, or a new version of `template_id` |
| | GET | `/api/v1/templates?is_active=&market=` | List templates |
| | GET | `/api/v1/templates/{id}` | Template with all versions |
| | PUT | `/api/v1/templates/{id}` | Add a new version from a definition (no PDF needed) |
| | PATCH | `/api/v1/templates/{id}` | Enable / disable, rename, describe |
| | GET | `/api/v1/templates/{id}/history` | Versions with the changes of each version |
| | POST | `/api/v1/templates/import` | Import a template exported from another environment |
| Schema | GET | `/api/v1/schema/fields` | Canonical fields (type, enum, required-for) for mapping UIs |
| | GET | `/api/v1/schema/markets` | Active market profiles |
| | GET | `/api/v1/schema/xsd` | XSD of the XML output |
| Audit | GET | `/api/v1/audit?action=&actor_id=&entity_id=&from=&to=&limit=&offset=` | Search the audit log |
| | GET | `/api/v1/audit/verify` | Verify the hash chain |
| | GET | `/api/v1/audit/export?format=csv\|xlsx\|json&from=&to=` | Download the audit log (oldest first) |

Preview and approve are `multipart/form-data`: `file` (the PDF) and `mapping` (JSON text):

```json
{
  "label_map": {"Contract Dt": "trade_date", "GST No": "_ignore"},
  "region_rules": [{"field": "counterparty", "page": 1, "bbox": [320, 140, 560, 158]}],
  "regex_rules": [{"field": "yield", "pattern": "at a yield of ([\\d.]+)%"}],
  "constants": {"platform": "OTC"},
  "template_id": null,
  "template_name": "Broker XYZ G-Sec confirmation",
  "keywords": ["XYZ Securities"],
  "accept_validation_issues": false,
  "note": "first onboarding"
}
```

Request headers: `Authorization: Basic …` (required), `X-Actor-Name` (optional, informational person
name, stored in audit), `X-Request-ID` (optional; generated if absent and always echoed back).

### HTTP status codes (all errors are RFC 7807 problem+json)

| Code | When |
|---|---|
| 200 / 201 | OK / template or version created |
| 400 | Malformed request (invalid mapping JSON, unknown field path, bad regex, bbox outside page) |
| 401 | Missing or wrong credentials (`WWW-Authenticate: Basic`) |
| 404 | Template not found |
| 409 | Approve with ERROR validation issues and `accept_validation_issues=false`; template id clash |
| 413 | File larger than `BONDS_MAX_UPLOAD_MB` |
| 415 | Not a PDF (magic bytes `%PDF-` missing) |
| 422 | Approve while required fields are still missing or the market is unknown; encrypted PDF; corrupt PDF; more pages than `BONDS_MAX_PAGES`; request fails schema validation |
| 429 | Client IP blocked by the auth limiter |
| 500 | Unexpected error |
| 503 | `/health` when the template store or audit log is not writable |

---

## 8. Project file structure

```
bonds-parser/
├── app/
│   ├── main.py                 FastAPI app, Swagger metadata, middleware, routers, error handlers
│   ├── config.py               Settings from BONDS_* env vars
│   ├── auth.py                 HTTP Basic verification, Argon2, client store, brute-force limiter
│   ├── cli.py                  add-client, list-clients, disable-client, rotate-secret, verify-audit, debug-extract
│   ├── api/                    routers: health, parse, templates, schema, audit; deps.py, errors.py
│   ├── schemas/                Pydantic models: common, deal, slip (parse result), template, audit
│   ├── services/               parse_service, template_service, audit_service
│   ├── engine/                 fields, extract, market, profiles/ (IN.json, …), detect, mapping,
│   │                           normalize, derive, validate, pipeline
│   └── export/                 json_export, xml_export, excel_export
├── templates/                  approved templates (JSON, reviewed in git)
├── tests/                      unit, golden (samples vs expected), api, auth, audit
├── docs/                       this documentation set
├── samples/mock/               mock slips + expected JSON (committed, fictitious)
├── samples/real/               real masked slips (git-ignored)
├── tools/                      generate_mock_slips.py, build_task_plan.py, demo.py
├── data/                       (git-ignored) clients.json, audit/audit.jsonl
├── .env.example                documented env vars
├── requirements.txt, requirements-dev.txt
├── Dockerfile, docker-compose.yml
└── README.md                   quick start
```

---

## 9. Configuration (env vars)

| Variable | Default | Purpose |
|---|---|---|
| `BONDS_DATA_DIR` | `./data` | Folder for clients and audit files |
| `BONDS_TEMPLATES_DIR` | `./templates` | Template JSON files |
| `BONDS_CLIENTS_FILE` | `<data dir>/clients.json` | API clients (Argon2 hashes) |
| `BONDS_AUDIT_FILE` | `<data dir>/audit/audit.jsonl` | Append-only audit log |
| `BONDS_MAX_UPLOAD_MB` | `10` | Reject larger files (413) |
| `BONDS_MAX_PAGES` | `20` | Reject longer PDFs (422) |
| `BONDS_CONFIDENCE_THRESHOLD` | `0.90` | Required-field confidence for `PARSED` |
| `BONDS_TEMPLATE_MATCH_THRESHOLD` | `0.80` | Default Jaccard score for template match |
| `BONDS_CORS_ORIGINS` | `http://localhost:3000,http://localhost:5173` | Frontend origins |
| `BONDS_DOCS_ENABLED` | `true` | Hide Swagger in prod if `false` |
| `BONDS_AUTH_MAX_FAILURES` | `10` | Failed auths per IP per window before block |
| `BONDS_AUTH_WINDOW_SECONDS` | `300` | Window for counting failures |
| `BONDS_AUTH_BLOCK_SECONDS` | `900` | Block duration (429) |
| `BONDS_LOG_LEVEL` | `INFO` | Logging level |
| `BONDS_AUDIT_RETENTION_YEARS` | `8` | Retention setting for the archive job |

---

## 10. Libraries

`fastapi, uvicorn, python-multipart, pydantic, pdfplumber, openpyxl, argon2-cffi,
prometheus-client, reportlab (mock generator), pytest, httpx2, xmlschema (tests)`. Later: `pytesseract / ocrmypdf` (OCR), `pikepdf`
(password-protected PDFs), `structlog, sentry-sdk`.

---

## 11. Scope

**v0.1.0 (MVP):** engine (India profile + market detection), templates as files with versions,
preview / approve with the PDF in the request, JSON / XML / Excel, HTTP Basic auth + CLI clients +
brute-force limiter, audit log (hash chain, metadata only), schema and audit endpoints, Docker,
tests on 6 mock slips + API flows.

**Later:** OCR, password-protected PDFs, multi-deal PDFs, holiday calendar, other market profiles
(US/GB/INTL), XSD, metrics, HTTPS termination (reverse proxy), audit archive job.

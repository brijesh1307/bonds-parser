# 00 · Design Baseline (single source of truth)

Every other document in `docs/` uses the names, values and decisions below. If a document
disagrees with this file, this file wins and the other document is wrong.

---

## 1. Product in one paragraph

**Bonds Deal Slip Parser** is a Python REST API (FastAPI, Swagger UI) that accepts a bond deal
slip PDF, extracts every label/value on it, maps them to one canonical deal schema, validates
the numbers, and returns the deal as JSON, XML or Excel. Slip layouts it has not seen before are
returned with all extracted key/value pairs (with page positions) so a separate frontend project
can let a user map them once. The approved mapping is saved as a versioned **template**, and every
later slip in that layout parses automatically. Every action is written to a tamper-evident audit
trail. Access is controlled by client credentials sent in the HTTP header.

**Out of scope for this project:** the frontend (separate project), RBAC / user roles, SSO.

---

## 2. Key decisions

| # | Decision | Choice |
|---|---|---|
| D1 | Language / framework | Python 3.11+, FastAPI, Pydantic v2, Uvicorn |
| D2 | API docs | Swagger UI at `/docs`, ReDoc at `/redoc`, OpenAPI JSON at `/openapi.json` |
| D3 | PDF extraction | pdfplumber (text layer, words with bbox, tables). OCR (Tesseract) after MVP |
| D4 | Storage | SQLAlchemy 2.x; SQLite for MVP (`data/bonds.db`), PostgreSQL in production; Alembic migrations after MVP |
| D5 | File storage | Uploaded PDFs in `data/uploads/<slip_id>.pdf` (S3/NAS later) |
| D6 | Parsing approach | Template-driven, with a generic fallback (synonyms → abbreviations → fuzzy). No LLM / no external calls with slip data |
| D7 | Templates | Stored in DB (`templates` + `template_versions`), versioned, never overwritten; JSON import/export |
| D8 | Auth | **HTTP Basic** `Authorization: Basic base64(client_id:client_secret)`; secrets hashed with Argon2; no RBAC |
| D9 | Audit | Append-only `audit_log` table, SHA-256 hash chain, written in the same DB transaction as the action |
| D10 | Money / rates | `Decimal` everywhere, serialised as strings in JSON/XML; never float |
| D11 | Dates | ISO `YYYY-MM-DD` in outputs; timestamps UTC ISO-8601 |
| D12 | Markets | Market detected before normalisation (vote of signals). MVP fully parses India (`IN`); other markets detected and sent to review |
| D13 | API versioning | All business endpoints under `/api/v1`; `/health`, `/docs`, `/redoc` at root |
| D14 | Errors | RFC 7807 `application/problem+json` |
| D15 | Outputs | JSON, XML (ElementTree, XSD after MVP), Excel (openpyxl) |

---

## 3. Slip status (state machine)

| Status | Meaning |
|---|---|
| `PARSED` | Known template matched **and** all required fields present **and** validation passed **and** every required field confidence ≥ threshold **and** market profile available. Direct answer. |
| `NEEDS_REVIEW` | Template matched, but a required field is missing, validation failed, confidence low, or market profile not available / market uncertain. |
| `NEW_TEMPLATE` | No template matched (score < match threshold). Best-effort deal still returned plus all key/values. |
| `APPROVED` | A client approved the mapping via `POST /slips/{id}/approve`. Final. |
| `UNREADABLE` | No text layer (scanned image) — OCR needed. |
| `FAILED` | Unexpected internal error while processing (details in audit log). |

Transitions: upload → {`PARSED` \| `NEEDS_REVIEW` \| `NEW_TEMPLATE` \| `UNREADABLE` \| `FAILED`};
`NEEDS_REVIEW`/`NEW_TEMPLATE`/`PARSED` → `APPROVED` (approve); any non-`APPROVED` → re-evaluated by reparse.
`APPROVED` can be re-parsed only with `force=true` (audited).

---

## 4. Processing pipeline

```
1 Ingest      store PDF, SHA-256, size/page limits, duplicate check
2 Extract     pdfplumber → KeyValue candidates {key, value, source, page, key_bbox, value_bbox}
              sources: table (label|value, label|value|label|value), grid (header row + value rows),
                       text ("Label: value", "Label   value" gap-separated), prose (regex)
3 Market      vote: ISIN prefix, CUSIP/SEDOL, currency, settlement system, platform, name style,
              number format, BIC → market, issuer_country, slip_locale, market_confidence
4 Detect      fingerprint = set of normalised labels; Jaccard vs active templates; best ≥ 0.80 wins
5 Map         template label_map → built-in synonyms → abbreviation expansion → fuzzy
6 Rules       template region_rules (bbox), regex_rules, constants
7 Normalise   per market profile: amounts (lakh/crore), dates (day/month order), rates, ISIN, enums
8 Derive      coupon from name, tenor from name, repo leg 1 → top level, buy/sell from deal type, …
9 Validate    ISIN check digit, principal, consideration, discount, quantity, repo legs, dates, holidays
10 Decide     status per §3
11 Export     JSON / XML / Excel on request
```

### Confidence per field (`method` → score)

| method | score | meaning |
|---|---|---|
| `template` | 1.00 | label found in the matched template's label_map |
| `region` | 1.00 | read from a template bbox rule |
| `constant` | 1.00 | template constant |
| `regex` | 0.95 | template regex rule |
| `synonym` | 0.95 | built-in synonym |
| `abbreviation` | 0.90 | synonym after abbreviation expansion (`Stl Dt` → settlement date) |
| `derived` | 0.90 | computed from another field |
| `fuzzy` | ≤ 0.85 | fuzzy label match (ratio × 0.85) |
| `text` | 0.80 | found by searching the full slip text |

**Unit in the label:** when a label carries a unit and the value does not (`Nominal (Cr)` = `5.00`),
the normaliser applies the label's multiplier (Cr ×10^7, Lakh ×10^5, Mn ×10^6). A wrong scale is
caught by the principal / consideration checks.

**Market confidence:** the market vote must reach **0.70**; below that `market = UNKNOWN` and the
slip goes to `NEEDS_REVIEW`. A template may pin `market` and a `date_order` (`DMY` / `MDY`) for its layout.

Confidence threshold default **0.90** (`BONDS_CONFIDENCE_THRESHOLD`). Template match threshold
default **0.80** (`BONDS_TEMPLATE_MATCH_THRESHOLD`). A template may list `accepted_derived` fields
(reviewer accepted a text/derived value for that layout) → those fields get confidence 0.95 when
that template matches.

---

## 5. Canonical deal schema (summary)

Full definitions: `docs/deal_slip_standard_formats.md` §3 and `docs/05_data_dictionary_and_outputs.md`.

Top-level fields: `deal_id, deal_type, instrument_type, buy_sell, platform, trade_date,
settlement_date, security_name, issuer, credit_rating, isin, identifiers{isin,cusip,sedol,common_code},
coupon_rate, coupon_frequency, maturity_date, last_coupon_date, day_count, tenor_days, face_value,
face_value_per_unit, quantity, price, yield, principal_amount, accrued_days, accrued_interest,
discount_amount, consideration, stamp_duty, settlement_reference, currency, settlement_currency, fx_rate,
counterparty, counterparty_pan, is_market_linked, broker,
settlement_mode, portfolio, dealer, bid_type, bid_amount, issuer_country, market, slip_locale,
market_confidence, repo{…}`

`repo` object: `repo_rate, repo_days, day_count, haircut, leg1_date, leg1_price, leg1_accrued_days,
leg1_accrued_interest, leg1_amount, leg2_date, leg2_price, leg2_accrued_days,
leg2_accrued_interest, leg2_amount, repo_interest`

Added for client confirmation letters (JM Financial MLD format, 2026-09-27):
- `stamp_duty` (decimal, cash): stamp duty on the trade; buyer amount = principal + stamp duty.
- `settlement_reference` (str): clearing settlement number (`SETTLEMENT NO.`); not unique per deal.
- `counterparty_pan` (str): counterparty's Indian PAN, output **in full** in the deal (JSON/XML/Excel) by
  decision. It is personal data: never written to logs, and **masked** (`ABCDE****F`) in audit `changes`/`details`.
- `is_market_linked` (bool): `true` for Market Linked Debentures; `instrument_type` stays `CORPORATE_BOND`.
"Our side" for JM Financial client letters is JM Financial: `Our Buy from:- <client>` = `BUY`, counterparty = client,
`consideration` = buyer settlement amount (incl. stamp duty), `principal_amount` = seller settlement amount.

Enums:
- `deal_type`: `OUTRIGHT, PRIMARY_AUCTION, PRIMARY_PLACEMENT, REPO, REVERSE_REPO, TREPS_BORROW, TREPS_LEND, LAF`
- `instrument_type`: `GSEC, SDL, TBILL, CMB, CORPORATE_BOND, PSU_BOND, CP, CD` (+ `UST, GILT, BUND, JGB, EUROBOND` with market profiles)
- `buy_sell`: `BUY, SELL`
- `day_count`: `30/360, 30E/360, ACT/ACT, ACT/365, ACT/364, ACT/360`
- `bid_type`: `COMPETITIVE, NON_COMPETITIVE`
- `market`: `IN, US, GB, DE, JP, INTL, UNKNOWN`

Required (all deal types): `deal_type, trade_date, settlement_date, security_name, isin, face_value, consideration`.
`deal_id` is optional: many slips (e.g. client confirmation letters) print no deal/ticket number;
it is captured when present, and a stored slip is always identified by its own slip id.
Plus: `OUTRIGHT` → `buy_sell, price`; `PRIMARY_AUCTION` → `price`;
`REPO`/`REVERSE_REPO` → `repo.repo_rate, repo.leg1_amount, repo.leg2_date, repo.leg2_amount`.

---

## 6. Database tables

| Table | Key columns |
|---|---|
| `clients` | `id` PK, `client_id` UNIQUE, `secret_hash` (Argon2), `description`, `is_active`, `created_at`, `last_used_at`, `rotated_at` |
| `slips` | `id` (UUID) PK, `file_name`, `file_size`, `sha256` (idx), `storage_path`, `page_count`, `status` (idx), `market`, `issuer_country`, `template_id` FK, `template_version`, `match_score`, `result_json`, `approved_mapping_json`, `duplicate_of`, `created_by`, `created_at`, `updated_at`, `approved_by`, `approved_actor_name`, `approved_at` |
| `templates` | `id` (slug) PK, `name`, `description`, `market`, `is_active`, `current_version`, `match_count`, `last_matched_at`, `created_by`, `created_at`, `updated_at` |
| `template_versions` | `id` PK, `template_id` FK, `version`, `definition_json`, `source_slip_id`, `note`, `created_by`, `created_at`; UNIQUE(`template_id`,`version`) |
| `audit_log` | `id` PK, `event_id` UUID, `occurred_at`, `actor_type` (`client`/`system`/`cli`), `actor_id`, `actor_name`, `action`, `entity_type` (`slip`/`template`/`client`/`export`/`audit`; auth events use `client`), `entity_id`, `status_before`, `status_after`, `changes` JSON, `details` JSON, `request_id`, `ip_address`, `user_agent`, `outcome` (`SUCCESS`/`FAILURE`), `error`, `prev_hash`, `row_hash`. Append-only (triggers block UPDATE/DELETE). |

### Template definition (`template_versions.definition_json`)

```json
{
  "template_id": "broker_xyz_gsec_confirm",
  "version": 2,
  "market": "IN",
  "fingerprint": {"labels": ["contract no", "stl dt", "paper"], "keywords": ["XYZ Securities"]},
  "match_threshold": 0.80,
  "label_map": {"contract no": "deal_id", "stl dt": "settlement_date", "paper": "security_name"},
  "region_rules": [{"field": "counterparty", "page": 1, "bbox": [320, 140, 560, 158]}],
  "regex_rules": [{"field": "yield", "pattern": "at a yield of ([\\d.]+)%"}],
  "constants": {"platform": "OTC", "currency": "INR"},
  "ignore": ["gst no", "page"],
  "accepted_derived": ["dealer"]
}
```

### Audit actions

`SLIP_UPLOADED, SLIP_PARSED, SLIP_PREVIEWED, SLIP_APPROVED, SLIP_REPARSED, SLIP_DELETED,
SLIP_VIEWED, SLIP_PDF_DOWNLOADED, SLIP_EXPORTED, DEALS_EXPORTED, TEMPLATE_CREATED,
TEMPLATE_VERSION_ADDED, TEMPLATE_ENABLED, TEMPLATE_DISABLED, TEMPLATE_IMPORTED, AUTH_FAILED,
AUTH_BLOCKED, CLIENT_ADDED, CLIENT_DISABLED, CLIENT_SECRET_ROTATED, AUDIT_EXPORTED,
TEMPLATE_UPDATED (rename / description), AUDIT_ARCHIVED`

Audit rules settled: list/search reads are not audited (single-slip view, PDF download and exports are);
all decimals inside audit JSON are strings so hashes are deterministic.

### Duplicate uploads

A file whose SHA-256 matches an existing slip is **still stored as a new slip** (every upload is
recorded) with `duplicate_of = <original slip id>`, and the response includes `duplicate_of` so the
caller can decide to ignore it.

---

## 7. API endpoints

All under `/api/v1` except System. All require header credentials except `/health`.

| Tag | Method | Path | Purpose |
|---|---|---|---|
| System | GET | `/health` | Liveness + DB check (open) |
| Parse | POST | `/api/v1/parse?format=json\|xml\|xlsx` | One-shot parse, nothing stored |
| Slips | POST | `/api/v1/slips` | Upload + store + parse one PDF |
| | POST | `/api/v1/slips/batch` | Upload many PDFs |
| | GET | `/api/v1/slips?status=&market=&template_id=&limit=&offset=` | List / review queue |
| | GET | `/api/v1/slips/{id}` | Full result (deal, fields with confidence + bbox, key_values, validation) |
| | GET | `/api/v1/slips/{id}/pdf` | Original PDF |
| | POST | `/api/v1/slips/{id}/preview` | Re-parse with a draft mapping (not saved) |
| | POST | `/api/v1/slips/{id}/approve` | Save template (new or new version) + mark `APPROVED` |
| | POST | `/api/v1/slips/{id}/reparse?force=` | Re-run with current templates |
| | GET | `/api/v1/slips/{id}/export?format=` | Download JSON / XML / Excel |
| | GET | `/api/v1/slips/{id}/history` | Audit timeline of the slip |
| | DELETE | `/api/v1/slips/{id}` | Delete slip + PDF (audit row kept) |
| Templates | GET | `/api/v1/templates` | List templates |
| | GET | `/api/v1/templates/{id}` | Template + versions |
| | PUT | `/api/v1/templates/{id}` | Add a new version from a definition |
| | PATCH | `/api/v1/templates/{id}` | Enable/disable, rename |
| | POST | `/api/v1/templates/import` | Import a template JSON |
| | GET | `/api/v1/templates/{id}/history` | Audit timeline + version diffs |
| Schema | GET | `/api/v1/schema/fields` | Canonical fields (type, required, enum values) |
| | GET | `/api/v1/schema/markets` | Market profiles available |
| Exports | GET | `/api/v1/exports/deals?format=&status=&from=&to=` | Combined export of many deals |
| Audit | GET | `/api/v1/audit?entity_type=&entity_id=&actor_id=&action=&from=&to=&limit=&offset=` | Search audit log |
| | GET | `/api/v1/audit/verify` | Verify hash chain |
| | GET | `/api/v1/audit/export?format=csv\|xlsx\|json&from=&to=` | Download audit log |

Request headers: `Authorization: Basic …` (required), `X-Actor-Name` (optional, informational person
name, stored in audit), `X-Request-ID` (optional; generated if absent and always echoed back).

### HTTP status codes (all errors are RFC 7807 problem+json)

| Code | When |
|---|---|
| 200 / 201 | OK / slip or template created |
| 400 | Malformed request (bad query value, invalid mapping payload, unknown `format`) |
| 401 | Missing or wrong credentials (`WWW-Authenticate: Basic`) |
| 404 | Slip / template not found |
| 409 | Approve with validation issues and `accept_validation_issues=false`; reparse an `APPROVED` slip without `force=true`; template version conflict |
| 413 | File larger than `BONDS_MAX_UPLOAD_MB` |
| 415 | Not a PDF (magic bytes `%PDF-` missing) |
| 422 | Approve while required fields are still missing; encrypted/password-protected PDF; more pages than `BONDS_MAX_PAGES`; request body fails schema validation |
| 429 | Client IP blocked by the auth limiter |
| 500 | Unexpected error (slip status `FAILED`, details in audit log) |
| 503 | `/health` when the database is unreachable |

### Audit of the one-shot parse

`POST /api/v1/parse` stores no slip but still writes one `SLIP_PARSED` audit row with
`entity_type = "slip"` (never a separate type), `entity_id = null`, and `details` = `{file_name, sha256, status, market, template}`.

Mapping payload used by preview / approve:

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

---

## 8. Project file structure

```
bonds-parser/
├── app/
│   ├── main.py                 FastAPI app, Swagger metadata, CORS, routers, error handlers
│   ├── config.py               Settings from BONDS_* env vars
│   ├── auth.py                 HTTP Basic verification, Argon2, brute-force limiter
│   ├── cli.py                  init-db, add-client, list-clients, disable-client, rotate-secret
│   ├── api/                    routers: health, parse, slips, templates, schema, exports, audit; deps.py
│   ├── schemas/                Pydantic models: common, deal, slip, template, audit
│   ├── services/               slip_service, template_service, export_service, audit_service
│   ├── engine/                 fields, extract, market, profiles/ (IN.json, …), detect, mapping, normalize,
│   │                           derive, validate, pipeline
│   ├── export/                 json_export, xml_export, excel_export
│   └── db/                     database.py (engine/session), models.py, migrations/ (Alembic, later)
├── tests/                      unit (engine), golden (samples vs expected), api, audit, auth
├── docs/                       this documentation set
├── samples/mock/               mock slips + expected JSON (committed)
├── samples/real/               real masked slips (git-ignored)
├── tools/generate_mock_slips.py
├── data/                       (git-ignored) bonds.db, uploads/
├── .env.example                documented env vars
├── requirements.txt
├── Dockerfile, docker-compose.yml   (after MVP)
└── README.md                   quick start
```

---

## 9. Configuration (env vars)

| Variable | Default | Purpose |
|---|---|---|
| `BONDS_DATA_DIR` | `./data` | DB file + uploads |
| `BONDS_DATABASE_URL` | `sqlite:///./data/bonds.db` | SQLAlchemy URL (Postgres in prod) |
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
| `BONDS_AUDIT_RETENTION_YEARS` | `8` | Retention setting for archive job |

---

## 10. Libraries

MVP: `fastapi, uvicorn, python-multipart, pydantic, sqlalchemy, pdfplumber, openpyxl, argon2-cffi,
pytest, httpx, reportlab`. After MVP: `alembic, psycopg[binary], pytesseract / ocrmypdf, pikepdf
(password-protected PDFs), structlog, prometheus-client, sentry-sdk`.

---

## 11. Scope: MVP (2 hours) vs after MVP

**MVP:** engine (India profile + market detection), templates (DB, versions), JSON/XML/Excel,
HTTP Basic auth + CLI clients, SQLite, audit log with hash chain + triggers, endpoints: health,
parse, slips (upload, list, get, pdf, preview, approve, export, history), templates (list, get),
schema/fields, audit (list, verify), tests on 4 mock slips + API flow.

**After MVP:** Postgres + Alembic, batch upload, reparse, delete, template PUT/PATCH/import/history,
exports/deals, audit export, brute-force limiter, OCR, password-protected PDFs, multi-deal PDFs,
other market profiles (US/GB/INTL), XSD, Docker, CI, logging/metrics, backups, HTTPS.

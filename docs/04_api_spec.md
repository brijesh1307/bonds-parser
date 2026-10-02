# 04 · API Specification

> **Update 2026-10-02 — stateless design ([ADR-0009](adr/0009-stateless-api-no-slip-storage.md)).**
> Deal slips are never stored and there is no database. Wherever this document describes a `slips`
> table, `/api/v1/slips/*` endpoints, SQLite / PostgreSQL, uploads storage or slip statuses such as
> `APPROVED`, [`00_design_baseline.md`](00_design_baseline.md) is authoritative: templates, clients and
> the audit log are files, and preview / approve take the PDF + mapping in one request.

Source of truth: [`00_design_baseline.md`](00_design_baseline.md) (§2 decisions, §3 statuses, §6 audit
actions, §7 endpoints, §9 env vars). If this document disagrees with the baseline, the baseline wins.
Anything not stated in the baseline is marked **(assumption)**.

Examples use the mock slip `samples/mock/01_gsec_outright_purchase.pdf` and its expected output
`samples/mock/expected/01_gsec_outright_purchase.json`.

---

## 1. Endpoint summary

| # | Tag | Method | Path | Purpose | Scope |
|---|---|---|---|---|---|
| 1 | System | GET | `/health` | Liveness + DB check (no auth) | MVP |
| 2 | Parse | POST | `/api/v1/parse?format=json\|xml\|xlsx` | One-shot parse, nothing stored | MVP |
| 3 | Slips | POST | `/api/v1/slips` | Upload + store + parse one PDF | MVP |
| 4 | Slips | POST | `/api/v1/slips/batch` | Upload many PDFs | Later |
| 5 | Slips | GET | `/api/v1/slips` | List / review queue | MVP |
| 6 | Slips | GET | `/api/v1/slips/{id}` | Full result (deal, fields + confidence + bbox, key_values, validation) | MVP |
| 7 | Slips | GET | `/api/v1/slips/{id}/pdf` | Original PDF | MVP |
| 8 | Slips | POST | `/api/v1/slips/{id}/preview` | Re-parse with a draft mapping (not saved) | MVP |
| 9 | Slips | POST | `/api/v1/slips/{id}/approve` | Save template (new / new version) + mark `APPROVED` | MVP |
| 10 | Slips | POST | `/api/v1/slips/{id}/reparse?force=` | Re-run with current templates | Later |
| 11 | Slips | GET | `/api/v1/slips/{id}/export?format=` | Download JSON / XML / Excel | MVP |
| 12 | Slips | GET | `/api/v1/slips/{id}/history` | Audit timeline of the slip | MVP |
| 13 | Slips | DELETE | `/api/v1/slips/{id}` | Delete slip + PDF (audit rows kept) | Later |
| 14 | Templates | GET | `/api/v1/templates` | List templates | MVP |
| 15 | Templates | GET | `/api/v1/templates/{id}` | Template + versions | MVP |
| 16 | Templates | PUT | `/api/v1/templates/{id}` | Add a new version from a definition | Later |
| 17 | Templates | PATCH | `/api/v1/templates/{id}` | Enable / disable, rename | Later |
| 18 | Templates | POST | `/api/v1/templates/import` | Import a template JSON | Later |
| 19 | Templates | GET | `/api/v1/templates/{id}/history` | Audit timeline + version diffs | Later |
| 20 | Schema | GET | `/api/v1/schema/fields` | Canonical fields (type, required, enum values) | MVP |
| 21 | Schema | GET | `/api/v1/schema/markets` | Market profiles available | Later (assumption: not in MVP list, §11) |
| 22 | Exports | GET | `/api/v1/exports/deals` | Combined export of many deals | Later |
| 23 | Audit | GET | `/api/v1/audit` | Search audit log | MVP |
| 24 | Audit | GET | `/api/v1/audit/verify` | Verify hash chain | MVP |
| 25 | Audit | GET | `/api/v1/audit/export?format=csv\|xlsx\|json` | Download audit log | Later |

Also served at the root (no `/api/v1`): Swagger UI `/docs`, ReDoc `/redoc`, OpenAPI `/openapi.json`
(hidden when `BONDS_DOCS_ENABLED=false`).

---

## 2. Conventions

### 2.1 Base URL and versioning

| Environment | Base URL |
|---|---|
| Local dev | `http://localhost:8000` |
| Production | `https://<host>` — TLS terminated at the reverse proxy (see `07_security.md`) |

All business endpoints live under `/api/v1`. A breaking change creates `/api/v2`; `/api/v1` keeps
working until clients have migrated. Additive changes (new optional fields, new endpoints) are not
breaking — clients must ignore unknown JSON fields.

### 2.2 Headers

| Header | Direction | Required | Notes |
|---|---|---|---|
| `Authorization: Basic base64(client_id:client_secret)` | request | yes (except `/health`) | Client credentials (D8). Missing / wrong → `401` with `WWW-Authenticate: Basic realm="bonds-parser"`. |
| `X-Actor-Name` | request | no | Informational name of the person using the client (e.g. `Priya Shah`). Stored in `audit_log.actor_name` and, on approve, `slips.approved_actor_name`. **Not verified.** Max 100 chars, control characters stripped (assumption). |
| `X-Request-ID` | request + response | no | Correlation ID. If absent (or not matching `^[A-Za-z0-9._-]{1,64}$`, assumption) the server generates a UUID. Always echoed in the response and stored in `audit_log.request_id`. |
| `Content-Type` | request | for bodies | `multipart/form-data` (uploads), `application/json` (mapping, template bodies). |
| `Location` | response | on `201` | URL of the created resource. |
| `Content-Disposition` | response | on downloads | `attachment; filename="<deal_id or slip id>.<ext>"` (PDF: `inline`). |
| `Retry-After` | response | on `429` | Seconds until the auth block ends. |

### 2.3 Content types

| Content | Media type |
|---|---|
| JSON results | `application/json` |
| XML export | `application/xml` |
| Excel export | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| CSV (audit export) | `text/csv; charset=utf-8` |
| PDF | `application/pdf` |
| Errors | `application/problem+json` (RFC 7807) |

### 2.4 Pagination

List endpoints (`GET /slips`, `GET /templates`, `GET /audit`) take `limit` (default **50**, max **200**)
and `offset` (default **0**). `limit > 200` or negative values → `422`. Response envelope:

```json
{ "items": [ ... ], "total": 137, "limit": 50, "offset": 0 }
```

Default order: newest first (`created_at` / `id` descending) (assumption). History endpoints return
oldest first (a timeline).

### 2.5 Data formats

| Kind | Format | Example |
|---|---|---|
| Money, rates, prices, yields (D10) | JSON **string** holding a decimal, never a float | `"face_value": "50000000"`, `"price": "101.2350"` |
| Integers (days, quantity, frequency) | JSON number | `"accrued_days": 167` |
| Scores (`confidence`, `match_score`, `market_confidence`) | JSON number 0–1 (assumption: scores are not money) | `0.95` |
| Dates (D11) | `YYYY-MM-DD` | `"trade_date": "2026-09-24"` |
| Timestamps (D11) | UTC ISO-8601 with `Z` | `"2026-09-27T10:15:02.418Z"` |
| Bounding box | `[x0, top, x1, bottom]` in PDF points, origin **top-left** of the page, pages 1-based | `[243.9, 124.3, 336.8, 132.9]` |
| IDs | slip: UUID; template: slug (`broker_xyz_gsec_confirm`); client: `client_id` string | |

Query parameters `from` / `to` accept `YYYY-MM-DD` (whole UTC day, `to` inclusive) or a full
ISO-8601 timestamp (assumption).

### 2.6 Errors (RFC 7807, D14)

Every non-2xx response is `application/problem+json`:

```http
HTTP/1.1 422 Unprocessable Entity
Content-Type: application/problem+json
X-Request-ID: 6b1f0c1e-3a0d-4f45-8a3e-2f4c9d7a1b20
```

```json
{
  "type": "https://bonds-parser/problems/missing-required-fields",
  "title": "Required fields are missing",
  "status": 422,
  "detail": "Cannot approve slip 3f6c1e2a-8b4d-4f7a-9c2e-5d1b7a9e0c44: 2 required field(s) have no value after applying the mapping.",
  "instance": "/api/v1/slips/3f6c1e2a-8b4d-4f7a-9c2e-5d1b7a9e0c44/approve",
  "code": "MISSING_REQUIRED_FIELDS",
  "request_id": "6b1f0c1e-3a0d-4f45-8a3e-2f4c9d7a1b20",
  "errors": [
    { "field": "deal_type", "message": "required for all deal types" },
    { "field": "buy_sell", "message": "required for OUTRIGHT" }
  ]
}
```

`type`, `title`, `status`, `detail`, `instance` are RFC 7807 standard members; `code`, `request_id`,
`errors` are extensions. Clients branch on `code`, never on `detail` text. `errors` is present for
field-level problems (including FastAPI request validation, `loc` → `field`).

Error codes (assumption: names; statuses follow the baseline where it gives one):

| Status | `code` | When |
|---|---|---|
| 400 | `BAD_REQUEST` | Malformed multipart / JSON body |
| 401 | `UNAUTHORIZED` | Missing, malformed or wrong credentials, disabled client |
| 404 | `NOT_FOUND` | Slip / template / route not found |
| 409 | `INVALID_STATE` | Action not allowed in the slip's current status |
| 409 | `VALIDATION_ISSUES_NOT_ACCEPTED` | Approve with validation issues and `accept_validation_issues=false` |
| 409 | `REPARSE_REQUIRES_FORCE` | Reparse an `APPROVED` slip without `force=true` |
| 409 | `TEMPLATE_EXISTS` | New template slug / imported id already exists |
| 413 | `FILE_TOO_LARGE` | Upload > `BONDS_MAX_UPLOAD_MB` (default 10) |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | File is not a PDF (magic bytes `%PDF-` absent) |
| 422 | `VALIDATION_ERROR` | Invalid query/body values (bad enum, `limit` > 200, unknown canonical field in mapping, invalid regex, bbox outside page) |
| 422 | `TOO_MANY_PAGES` | PDF > `BONDS_MAX_PAGES` (default 20) |
| 422 | `PDF_ENCRYPTED` | Password-protected PDF (not supported yet) |
| 422 | `PDF_CORRUPT` | PDF cannot be opened |
| 422 | `MISSING_REQUIRED_FIELDS` | Approve while required fields are still missing |
| 429 | `AUTH_BLOCKED` | Too many failed auths from this IP (limiter, Later) |
| 500 | `INTERNAL_ERROR` | Unexpected error (details only in audit/log, never in the response) |
| 503 | `SERVICE_UNAVAILABLE` | `/health` when the DB check fails |

Note: `UNREADABLE` (no text layer) and `FAILED` are **slip statuses**, not HTTP errors — the upload
returns `201` and the slip is stored with that status.

### 2.7 Status codes used

`200` OK · `201` Created · `204` No Content · `400` · `401` · `404` · `409` · `413` · `415` · `422` ·
`429` · `500` · `503`.

---

## 3. Shared objects

### 3.1 SlipResult (returned by upload, get, preview, approve, reparse)

```json
{
  "id": "3f6c1e2a-8b4d-4f7a-9c2e-5d1b7a9e0c44",
  "file_name": "01_gsec_outright_purchase.pdf",
  "file_size": 4187,
  "sha256": "9b2f4c1d7e0a6b3c8d5e2f1a0c9b8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a2b1c",
  "page_count": 1,
  "page_sizes": [{ "page": 1, "width": 595.28, "height": 841.89 }],
  "status": "NEW_TEMPLATE",
  "market": "IN",
  "issuer_country": "IN",
  "template_id": null,
  "template_version": null,
  "match_score": null,
  "duplicate_of": null,
  "created_by": "treasury-frontend",
  "created_at": "2026-09-27T10:15:02.418Z",
  "updated_at": "2026-09-27T10:15:02.911Z",
  "approved_by": null,
  "approved_actor_name": null,
  "approved_at": null,
  "deal": { "...": "canonical deal, see §3.2" },
  "fields": {
    "deal_id": {
      "value": "GS/NDSOM/2026/004571", "raw": "GS/NDSOM/2026/004571",
      "confidence": 0.95, "method": "synonym", "source_label": "Deal Reference No.",
      "page": 1, "bbox": [243.9, 124.3, 336.8, 132.9]
    },
    "isin": {
      "value": "IN0020240A75", "raw": "IN0020240A75",
      "confidence": 0.95, "method": "synonym", "source_label": "ISIN",
      "page": 1, "bbox": [243.9, 253.9, 311.4, 262.5]
    },
    "face_value": {
      "value": "50000000", "raw": "5,00,00,000.00",
      "confidence": 0.95, "method": "synonym", "source_label": "Face Value (INR)",
      "page": 1, "bbox": [243.9, 318.7, 309.9, 327.3]
    }
  },
  "key_values": [
    { "key": "Deal Reference No.", "value": "GS/NDSOM/2026/004571", "source": "table", "page": 1,
      "key_bbox": [59.7, 124.1, 146.3, 133.0], "value_bbox": [243.9, 124.3, 336.8, 132.9], "mapped_to": "deal_id" },
    { "key": "Deal Type", "value": "OUTRIGHT PURCHASE", "source": "table", "page": 1,
      "key_bbox": [59.7, 140.3, 106.2, 149.2], "value_bbox": [243.9, 140.5, 338.9, 149.1], "mapped_to": null },
    { "key": "Trading Platform", "value": "NDS-OM (Anonymous Order Matching)", "source": "table", "page": 1,
      "key_bbox": [59.7, 156.5, 131.0, 165.4], "value_bbox": [243.9, 156.7, 402.6, 165.3], "mapped_to": null }
  ],
  "missing_required": ["deal_type", "buy_sell"],
  "validation": [],
  "unmapped": ["Deal Type", "Trading Platform", "Trade Time", "Security Type", "SGL / CSGL A/c"]
}
```

| Field | Meaning |
|---|---|
| `page_sizes` | Page width/height in PDF points — the frontend scales `bbox` to the rendered page with it. |
| `fields.<name>` | One entry per filled canonical field. Nested repo fields use `repo.<field>` as key. `method` / `confidence` per baseline §4. |
| `key_values` | **Every** label/value candidate extracted (D6, pipeline step 2). `source` ∈ `table, grid, text, prose`. `mapped_to` = canonical field or `null`. |
| `missing_required` | Required fields (baseline §5) with no value. |
| `validation` | Failed checks only: `{ "check": "principal", "fields": ["face_value","price","principal_amount"], "message": "face_value × price / 100 = 50617500.00 ≠ 50617400.00" }`. |
| `unmapped` | Labels found on the slip but not mapped (`_unmapped` in standard formats §3). |

(assumption: exact shape of `fields`, `validation` and `page_sizes`; names follow the baseline.)

### 3.2 Canonical deal (`deal`, and JSON export body)

Field set per baseline §5; `null` for fields that do not apply. Example (mock 01, after approval):

```json
{
  "deal_id": "GS/NDSOM/2026/004571",
  "deal_type": "OUTRIGHT",
  "instrument_type": "GSEC",
  "buy_sell": "BUY",
  "platform": "NDS-OM",
  "trade_date": "2026-09-24",
  "settlement_date": "2026-09-25",
  "security_name": "7.10% GS 2034",
  "isin": "IN0020240A75",
  "coupon_rate": "7.10",
  "coupon_frequency": 2,
  "maturity_date": "2034-04-08",
  "last_coupon_date": "2026-04-08",
  "day_count": "30/360",
  "face_value": "50000000",
  "quantity": null,
  "price": "101.2350",
  "yield": "6.8865",
  "principal_amount": "50617500.00",
  "accrued_days": 167,
  "accrued_interest": "1646805.56",
  "consideration": "52264305.56",
  "currency": "INR",
  "counterparty": "Anonymous (NDS-OM) - CCIL Novated",
  "settlement_mode": "DVP-III",
  "portfolio": "AFS - Treasury",
  "dealer": "R. Mehta",
  "market": "IN",
  "slip_locale": "en-IN",
  "market_confidence": 0.97,
  "repo": null
}
```

Repo slips fill `repo` (mock 04): `"repo": {"repo_rate": "5.40", "repo_days": 3, "day_count": "ACT/365",
"leg1_date": "2026-09-25", "leg1_amount": "101752833.33", "leg2_date": "2026-09-28",
"leg2_amount": "101797994.86", "repo_interest": "45161.53", ...}`.

### 3.3 MappingSpec / ApproveRequest

Body of `POST /slips/{id}/preview` (MappingSpec) and `POST /slips/{id}/approve` (ApproveRequest —
same schema). Baseline §7 payload:

```json
{
  "label_map": { "Deal Type": "deal_type", "Trading Platform": "_ignore", "Trade Time": "_ignore",
                 "Security Type": "_ignore", "SGL / CSGL A/c": "_ignore" },
  "region_rules": [ { "field": "counterparty", "page": 1, "bbox": [240.9, 412.0, 538.6, 428.0] } ],
  "regex_rules": [ { "field": "yield", "pattern": "at a yield of ([\\d.]+)%" } ],
  "constants": { "platform": "NDS-OM" },
  "template_id": null,
  "template_name": "Orca Treasury G-Sec deal slip",
  "keywords": ["DEAL SLIP - GOVERNMENT SECURITIES"],
  "accept_validation_issues": false,
  "note": "first onboarding"
}
```

| Field | Type | Req. | Default | Semantics |
|---|---|---|---|---|
| `label_map` | object<string,string> | no | `{}` | **Key** = slip label exactly as shown in `key_values[*].key` (server normalises it for storage, e.g. `Stl Dt` → `stl dt`). **Value** = a canonical top-level field (`trade_date`), `repo.<field>` (`repo.leg2_amount`), or `_ignore` (label goes to the template `ignore` list). Unknown field → `422`. |
| `region_rules` | array | no | `[]` | `{field, page, bbox}`; `bbox` = `[x0, top, x1, bottom]` in PDF points, origin top-left, `page` 1-based. Words inside the box form the raw value, then normal normalisation applies. Box outside page → `422`. |
| `regex_rules` | array | no | `[]` | `{field, pattern}`; Python regex run on the full slip text; first capture group = raw value. Invalid pattern or > 500 chars → `422` (assumption: length cap, see `07_security.md`). |
| `constants` | object<string,string> | no | `{}` | Field → fixed value for every slip of this layout. |
| `template_id` | string \| null | no | `null` | `null` → create a **new** template (approve). Existing id → add a **new version** of that template. Unknown id → `404`. |
| `template_name` | string | when `template_id` is `null` (approve) | — | Human name; the new template id is its slug (`orca_treasury_g_sec_deal_slip`). Slug already exists → `409 TEMPLATE_EXISTS`. |
| `keywords` | string[] | no | `[]` | Added to `fingerprint.keywords` (distinctive text on the slip). |
| `accept_validation_issues` | bool | no | `false` | Approve even when validation checks fail. |
| `note` | string | no | `null` | Stored in `template_versions.note` (max 500, assumption). |

Precedence when the same field is set twice: `constants` > `region_rules` > `regex_rules` > `label_map`
> built-in synonyms (assumption; baseline pipeline order is map → rules).

Preview uses only `label_map`, `region_rules`, `regex_rules`, `constants`; the other fields are
accepted and ignored so the frontend can send the same object to both endpoints.

**Approve semantics**
1. Slip status must be `NEW_TEMPLATE`, `NEEDS_REVIEW` or `PARSED` (baseline §3) → else `409 INVALID_STATE`.
2. Mapping is applied (as in preview).
3. Required fields still missing → **`422 MISSING_REQUIRED_FIELDS`** (nothing saved).
4. Validation issues present and `accept_validation_issues=false` → **`409 VALIDATION_ISSUES_NOT_ACCEPTED`**
   (nothing saved; `errors` lists the failed checks).
5. Otherwise, in **one DB transaction**: create template v1 (or version n+1) with fingerprint = the
   slip's normalised labels + `keywords`; derived/text fields the reviewer accepted go to
   `accepted_derived`; slip → `APPROVED`, `approved_by`, `approved_actor_name`, `approved_at`,
   `approved_mapping_json`; audit rows written.

---

## 4. Endpoints

Every curl example assumes:

```bash
BASE=http://localhost:8000
CRED=treasury-frontend:Q3u9...secret   # client_id:client_secret
```

### 4.1 `GET /health` — MVP

**Purpose:** load-balancer / monitoring liveness probe; confirms the DB is reachable.
**Auth:** none. **Audit:** none.

```bash
curl $BASE/health
```

```json
{ "status": "ok", "db": "ok", "version": "0.1.0", "time": "2026-09-27T10:14:00.000Z" }
```

Errors: `503` `{ "status": "error", "db": "unreachable" }` (as problem+json).
Never returns configuration, client or slip information.

---

### 4.2 `POST /api/v1/parse` — MVP

**Purpose:** stateless "just give me the deal" call for systems that do not need review, templates or
storage. Uses active templates and the generic fallback; **nothing is stored** (no slip row, no file).

| Param | In | Type | Req. | Default | Notes |
|---|---|---|---|---|---|
| `format` | query | `json` \| `xml` \| `xlsx` | no | `json` | Output format |
| `file` | multipart | PDF | yes | — | One file |

```bash
curl -u $CRED -H "X-Actor-Name: Priya Shah" \
  -F "file=@samples/mock/01_gsec_outright_purchase.pdf;type=application/pdf" \
  "$BASE/api/v1/parse?format=json"
```

Response `200` (`format=json`): SlipResult without storage fields (`id`, `created_*`, `approved_*`
are `null`). `xml` / `xlsx` return the file directly (see §4.11).

Errors: `401`, `413`, `415`, `422` (`TOO_MANY_PAGES`, `PDF_ENCRYPTED`, `PDF_CORRUPT`, `VALIDATION_ERROR`
for bad `format`), `429`, `500`.

Audit: `SLIP_PARSED` with `entity_type="slip"`, `entity_id=null`, `details={file_name, sha256,
file_size, page_count, status, market, template_id, match_score, format}` — metadata only, no slip
values (baseline "Audit of the one-shot parse").

---

### 4.3 `POST /api/v1/slips` — MVP

**Purpose:** main entry — store the PDF, parse it, keep the result for review, approval, export and
audit.

| Param | In | Type | Req. | Notes |
|---|---|---|---|---|
| `file` | multipart | PDF | yes | ≤ `BONDS_MAX_UPLOAD_MB`, ≤ `BONDS_MAX_PAGES` |

```bash
curl -u $CRED -H "X-Actor-Name: Priya Shah" -H "X-Request-ID: ui-7c1d" \
  -F "file=@samples/mock/01_gsec_outright_purchase.pdf;type=application/pdf" \
  $BASE/api/v1/slips
```

Response `201`, `Location: /api/v1/slips/3f6c1e2a-...`, body = SlipResult (§3.1). `status` is one of
`PARSED`, `NEEDS_REVIEW`, `NEW_TEMPLATE`, `UNREADABLE`, `FAILED`.

Duplicates: if the SHA-256 matches an existing slip, the new slip is still stored and returned with
`duplicate: true` and `duplicate_of` = the earlier slip id (baseline "Duplicate uploads").

Errors: `401`, `413 FILE_TOO_LARGE`, `415 UNSUPPORTED_MEDIA_TYPE`, `422 TOO_MANY_PAGES | PDF_ENCRYPTED |
PDF_CORRUPT`, `429`, `500`.

Audit: `SLIP_UPLOADED` (details: `file_name`, `file_size`, `sha256`, `page_count`, `duplicate_of`) then
`SLIP_PARSED` (`status_before=null`, `status_after=<status>`, details: `template_id`,
`template_version`, `match_score`, `market`, `missing_required`, `validation_failed` check names).
Rejected uploads (413/415/422) write `SLIP_UPLOADED` with `outcome=FAILURE` and `error` (assumption).

---

### 4.4 `POST /api/v1/slips/batch` — Later

**Purpose:** back-office bulk load (e.g. a day's confirmations).

| Param | In | Type | Req. | Notes |
|---|---|---|---|---|
| `files` | multipart (repeated) | PDF[] | yes | Max 20 files per call (assumption); each file obeys single-upload limits |

```bash
curl -u $CRED -F "files=@samples/mock/01_gsec_outright_purchase.pdf" \
  -F "files=@samples/mock/04_market_repo_reverse_repo.pdf" $BASE/api/v1/slips/batch
```

Response `200` — per-file result; one bad file does not fail the batch:

```json
{
  "items": [
    { "file_name": "01_gsec_outright_purchase.pdf", "slip_id": "3f6c1e2a-8b4d-4f7a-9c2e-5d1b7a9e0c44", "status": "PARSED", "error": null },
    { "file_name": "scan.jpg", "slip_id": null, "status": null,
      "error": { "status": 415, "code": "UNSUPPORTED_MEDIA_TYPE", "detail": "Not a PDF" } }
  ],
  "total": 2
}
```

Errors (whole request): `401`, `413` (total body too large), `422` (no files / too many files), `429`.
Audit: same as §4.3 per file.

---

### 4.5 `GET /api/v1/slips` — MVP

**Purpose:** inbox and review queue for the frontend.

| Param | In | Type | Default | Notes |
|---|---|---|---|---|
| `status` | query | slip status | all | e.g. `NEEDS_REVIEW` (repeatable or comma-separated, assumption) |
| `market` | query | market enum | all | |
| `template_id` | query | string | all | |
| `limit` / `offset` | query | int | 50 / 0 | max 200 |

```bash
curl -u $CRED "$BASE/api/v1/slips?status=NEW_TEMPLATE,NEEDS_REVIEW&limit=50&offset=0"
```

```json
{
  "items": [
    { "id": "3f6c1e2a-8b4d-4f7a-9c2e-5d1b7a9e0c44", "file_name": "01_gsec_outright_purchase.pdf",
      "status": "NEW_TEMPLATE", "market": "IN", "template_id": null, "match_score": null,
      "deal_id": "GS/NDSOM/2026/004571", "security_name": "7.10% GS 2034", "consideration": "52264305.56",
      "missing_required_count": 2, "validation_issue_count": 0, "duplicate_of": null,
      "created_by": "treasury-frontend", "created_at": "2026-09-27T10:15:02.418Z" }
  ],
  "total": 1, "limit": 50, "offset": 0
}
```

Errors: `401`, `422` (bad enum / limit), `429`. Audit: none (list reads not audited — assumption, to keep
the log about actions on individual records).

---

### 4.6 `GET /api/v1/slips/{id}` — MVP

**Purpose:** everything the review screen needs: deal, per-field confidence + bbox, all key/values,
missing fields, validation.

```bash
curl -u $CRED -H "X-Actor-Name: Priya Shah" $BASE/api/v1/slips/3f6c1e2a-8b4d-4f7a-9c2e-5d1b7a9e0c44
```

Response `200`: SlipResult (§3.1). Errors: `401`, `404`, `429`. Audit: `SLIP_VIEWED`.

---

### 4.7 `GET /api/v1/slips/{id}/pdf` — MVP

**Purpose:** original file for the review screen's PDF viewer and for evidence.

```bash
curl -u $CRED -o slip.pdf $BASE/api/v1/slips/3f6c1e2a-8b4d-4f7a-9c2e-5d1b7a9e0c44/pdf
```

Response `200 application/pdf`, `Content-Disposition: inline; filename="01_gsec_outright_purchase.pdf"`
(sanitised name). Errors: `401`, `404` (slip or file missing), `429`. Audit: `SLIP_PDF_DOWNLOADED`.

---

### 4.8 `POST /api/v1/slips/{id}/preview` — MVP

**Purpose:** fast feedback loop while the user maps labels: apply a draft mapping and see the
resulting deal, missing fields and validation **without saving anything**.

Body: MappingSpec (§3.3), `application/json`.

```bash
curl -u $CRED -H "Content-Type: application/json" -H "X-Actor-Name: Priya Shah" \
  -d '{"label_map":{"Deal Type":"deal_type","Trading Platform":"_ignore","Trade Time":"_ignore","Security Type":"_ignore","SGL / CSGL A/c":"_ignore"},"constants":{"platform":"NDS-OM"}}' \
  $BASE/api/v1/slips/3f6c1e2a-8b4d-4f7a-9c2e-5d1b7a9e0c44/preview
```

Response `200`: SlipResult computed with the draft. `status` is the **would-be** status (e.g. `PARSED`);
the stored slip is unchanged. With the mapping above, `deal_type` = `OUTRIGHT` (method `template`),
`buy_sell` = `BUY` (method `derived` from deal type), `platform` = `NDS-OM` (method `constant`),
`missing_required: []`, `validation: []`, `unmapped: []`.

Errors: `401`, `404`, `409 INVALID_STATE` (`UNREADABLE`/`FAILED`: nothing to map), `422 VALIDATION_ERROR`
(unknown field, bad regex, bbox off page), `429`.

Audit: `SLIP_PREVIEWED` (details: counts of `label_map` / rules / constants, resulting would-be status,
`missing_required`). Mapping details are not repeated on every preview (assumption: keep volume low;
the final mapping is recorded on approve).

---

### 4.9 `POST /api/v1/slips/{id}/approve` — MVP

**Purpose:** the one-time onboarding step — turns a reviewed mapping into a versioned template so every
later slip in this layout parses automatically, and finalises this slip.

Body: ApproveRequest (§3.3).

```bash
curl -u $CRED -H "Content-Type: application/json" -H "X-Actor-Name: Priya Shah" \
  -d @approve.json $BASE/api/v1/slips/3f6c1e2a-8b4d-4f7a-9c2e-5d1b7a9e0c44/approve
```

Response `200`:

```json
{
  "slip": { "id": "3f6c1e2a-8b4d-4f7a-9c2e-5d1b7a9e0c44", "status": "APPROVED",
            "template_id": "orca_treasury_g_sec_deal_slip", "template_version": 1,
            "approved_by": "treasury-frontend", "approved_actor_name": "Priya Shah",
            "approved_at": "2026-09-27T10:21:44.090Z", "deal": { "...": "..." } },
  "template": { "id": "orca_treasury_g_sec_deal_slip", "version": 1, "created": true }
}
```

Errors:

| Status | Code | When |
|---|---|---|
| 401 | `UNAUTHORIZED` | |
| 404 | `NOT_FOUND` | Slip or `template_id` not found |
| 409 | `INVALID_STATE` | Slip is `APPROVED`, `UNREADABLE` or `FAILED` |
| 409 | `VALIDATION_ISSUES_NOT_ACCEPTED` | Validation fails and `accept_validation_issues=false` |
| 409 | `TEMPLATE_EXISTS` | `template_id=null` and slug of `template_name` already exists |
| 422 | `MISSING_REQUIRED_FIELDS` | Required fields still missing after mapping |
| 422 | `VALIDATION_ERROR` | Bad body (missing `template_name`, unknown field, bad regex/bbox) |
| 429 | `AUTH_BLOCKED` | |

Audit (same transaction): `TEMPLATE_CREATED` or `TEMPLATE_VERSION_ADDED` (details: full definition),
then `SLIP_APPROVED` (`status_before` → `APPROVED`, `changes` = field-level diff of the deal,
details: `template_id`, `template_version`, `accept_validation_issues`, accepted issues).

---

### 4.10 `POST /api/v1/slips/{id}/reparse` — Later

**Purpose:** re-evaluate a slip after templates changed (e.g. clear the `NEEDS_REVIEW` queue after a
new template version).

| Param | In | Type | Default | Notes |
|---|---|---|---|---|
| `force` | query | bool | `false` | Required to reparse an `APPROVED` slip (baseline §3) |

```bash
curl -u $CRED -X POST "$BASE/api/v1/slips/3f6c1e2a-8b4d-4f7a-9c2e-5d1b7a9e0c44/reparse?force=false"
```

Response `200`: SlipResult with the new status. Errors: `401`, `404`, `409 REPARSE_REQUIRES_FORCE`, `429`.
Audit: `SLIP_REPARSED` (`status_before`, `status_after`, `changes` = deal diff, details: `force`,
old/new `template_id`/`template_version`).

---

### 4.11 `GET /api/v1/slips/{id}/export` — MVP

**Purpose:** hand the deal to downstream systems / users in their format.

| Param | In | Type | Default |
|---|---|---|---|
| `format` | query | `json` \| `xml` \| `xlsx` | `json` |

```bash
curl -u $CRED -OJ "$BASE/api/v1/slips/3f6c1e2a-8b4d-4f7a-9c2e-5d1b7a9e0c44/export?format=xml"
```

- `json` → canonical deal (§3.2) + `_source` `{file_name, template_id, template_version, page_count}`,
  `_validation`, `_unmapped` (standard formats §3).
- `xml` → same content, one element per field, decimals as text (assumption: root `<deal>`):

```xml
<deal>
  <deal_id>GS/NDSOM/2026/004571</deal_id>
  <deal_type>OUTRIGHT</deal_type>
  <face_value>50000000</face_value>
  <consideration>52264305.56</consideration>
  <repo/>
</deal>
```

- `xlsx` → sheets `Deal` (field / value), `Validation`, `Key Values` (assumption).

Any status can be exported; the payload carries `status` in `_source` so consumers can refuse
non-`APPROVED`/`PARSED` deals (assumption). Errors: `401`, `404`, `422` (bad format), `429`.
Audit: `SLIP_EXPORTED` (details: `format`, `status`).

---

### 4.12 `GET /api/v1/slips/{id}/history` — MVP

**Purpose:** show "who did what" on the slip screen without needing audit-search rights (there is no
RBAC anyway).

```bash
curl -u $CRED $BASE/api/v1/slips/3f6c1e2a-8b4d-4f7a-9c2e-5d1b7a9e0c44/history
```

Response `200`: `{ "slip_id": "...", "items": [AuditEntry, ...] }` oldest first; AuditEntry shape as in
§4.23. Works for deleted slips (audit rows are kept). Errors: `401`, `429`. Audit: none (assumption).

---

### 4.13 `DELETE /api/v1/slips/{id}` — Later

**Purpose:** remove a wrongly uploaded or sensitive slip (e.g. wrong counterparty file) and its PDF.

```bash
curl -u $CRED -X DELETE -H "X-Actor-Name: Priya Shah" $BASE/api/v1/slips/3f6c1e2a-8b4d-4f7a-9c2e-5d1b7a9e0c44
```

Response `204`. Slip row and PDF are removed; audit rows stay (hash chain intact). Templates created
from this slip keep working; `source_slip_id` becomes a dangling reference (assumption).
Errors: `401`, `404`, `429`. Audit: `SLIP_DELETED` (`status_before`, details: `file_name`, `sha256`,
`deal_id`).

---

### 4.14 `GET /api/v1/templates` — MVP

**Purpose:** template management list; lets the frontend offer "add version to existing template".

| Param | In | Default |
|---|---|---|
| `market`, `is_active` | query | all |
| `limit` / `offset` | query | 50 / 0 |

```json
{
  "items": [
    { "id": "orca_treasury_g_sec_deal_slip", "name": "Orca Treasury G-Sec deal slip",
      "description": null, "market": "IN", "is_active": true, "current_version": 1,
      "match_count": 12, "last_matched_at": "2026-09-27T11:02:10.004Z",
      "created_by": "treasury-frontend", "created_at": "2026-09-27T10:21:44.090Z",
      "updated_at": "2026-09-27T10:21:44.090Z" }
  ],
  "total": 1, "limit": 50, "offset": 0
}
```

Errors: `401`, `422`, `429`. Audit: none.

### 4.15 `GET /api/v1/templates/{id}` — MVP

Template plus all versions (`version`, `definition_json` as in baseline §6, `source_slip_id`, `note`,
`created_by`, `created_at`). Errors: `401`, `404`, `429`. Audit: none.

### 4.16 `PUT /api/v1/templates/{id}` — Later

**Purpose:** add a version from an edited definition (power users, template fixes without a slip).
Body: `{ "definition": { ...template definition... }, "note": "fix stl dt" }`. The server assigns
`version = current_version + 1` (body `version` ignored). Versions are never overwritten (D7).
Response `201` with the new version. Errors: `401`, `404`, `422` (invalid definition), `429`.
Audit: `TEMPLATE_VERSION_ADDED` (`changes` = diff vs previous definition).

### 4.17 `PATCH /api/v1/templates/{id}` — Later

**Purpose:** switch a template off without deleting it, or rename it.
Body: `{ "is_active": false }` and/or `{ "name": "...", "description": "..." }`. Response `200` template.
Errors: `401`, `404`, `422`, `429`. Audit: `TEMPLATE_ENABLED` / `TEMPLATE_DISABLED`. Rename: no action
exists in baseline §6 — **open point**, proposed `TEMPLATE_UPDATED` (assumption).

### 4.18 `POST /api/v1/templates/import` — Later

**Purpose:** move templates between environments (UAT → prod) as JSON (D7).
Body `application/json`: a template definition (baseline §6) plus optional `name`, `description`.
Response `201`, `Location: /api/v1/templates/{id}`. Existing id → `409 TEMPLATE_EXISTS` (use `PUT`).
Errors: `401`, `409`, `422`, `429`. Audit: `TEMPLATE_IMPORTED` (details: definition, source).

### 4.19 `GET /api/v1/templates/{id}/history` — Later

Audit entries for the template, oldest first, plus `version_diffs`:

```json
{
  "template_id": "orca_treasury_g_sec_deal_slip",
  "items": [ { "action": "TEMPLATE_CREATED", "occurred_at": "2026-09-27T10:21:44.090Z", "...": "..." } ],
  "version_diffs": [
    { "from_version": 1, "to_version": 2,
      "changes": { "label_map.stl dt": { "old": null, "new": "settlement_date" } } }
  ]
}
```

Errors: `401`, `404`, `429`. Audit: none.

---

### 4.20 `GET /api/v1/schema/fields` — MVP

**Purpose:** drives the frontend's "map to field" dropdown and validation hints — no hard-coded list in
the UI.

```json
{
  "items": [
    { "name": "deal_id", "group": "Identity", "type": "string", "required_for": [], "enum": null },
    { "name": "buy_sell", "group": "Identity", "type": "enum", "required_for": ["OUTRIGHT"], "enum": ["BUY", "SELL"] },
    { "name": "face_value", "group": "Economics", "type": "decimal", "required_for": ["ALL"], "enum": null },
    { "name": "repo.leg2_amount", "group": "Repo", "type": "decimal", "required_for": ["REPO", "REVERSE_REPO"], "enum": null }
  ]
}
```

Errors: `401`, `429`. Audit: none.

### 4.21 `GET /api/v1/schema/markets` — Later

```json
{ "items": [
  { "market": "IN", "name": "India", "support": "FULL", "currency": "INR", "date_order": "DMY" },
  { "market": "US", "name": "United States", "support": "DETECT_ONLY", "currency": "USD", "date_order": "MDY" }
] }
```

`DETECT_ONLY` slips go to `NEEDS_REVIEW` (D12). Errors: `401`, `429`. Audit: none.

---

### 4.22 `GET /api/v1/exports/deals` — Later

**Purpose:** one file with many deals (end-of-day feed, reconciliation).

| Param | In | Type | Default |
|---|---|---|---|
| `format` | query | `json` \| `xml` \| `xlsx` | `json` |
| `status` | query | slip status(es) | `APPROVED` (assumption) |
| `from` / `to` | query | date | none — filter on slip `created_at` (assumption) |

`json` → `{ "items": [deal, ...], "total": n }`; `xml` → `<deals><deal>…</deal></deals>`; `xlsx` → one
row per deal. Max 10 000 deals per call (assumption; `422` beyond). Errors: `401`, `422`, `429`.
Audit: `DEALS_EXPORTED` (details: filters, `format`, row count, slip ids).

---

### 4.23 `GET /api/v1/audit` — MVP

**Purpose:** audit search for auditors, support and the frontend history views.

| Param | In | Type | Notes |
|---|---|---|---|
| `entity_type` | query | `slip` \| `template` \| `client` \| `export` \| `audit` | |
| `entity_id` | query | string | |
| `actor_id` | query | string | `client_id` (or `cli`/`system`) |
| `action` | query | action enum (baseline §6) | |
| `from` / `to` | query | date / timestamp | on `occurred_at` |
| `limit` / `offset` | query | int | 50 / 0, max 200 |

```bash
curl -u $CRED "$BASE/api/v1/audit?entity_type=slip&entity_id=3f6c1e2a-8b4d-4f7a-9c2e-5d1b7a9e0c44"
```

```json
{
  "items": [
    { "id": 1043, "event_id": "a7e3c0d2-1f4b-4c89-9e61-3b2d5f7a8c10", "occurred_at": "2026-09-27T10:21:44.090Z",
      "actor_type": "client", "actor_id": "treasury-frontend", "actor_name": "Priya Shah",
      "action": "SLIP_APPROVED", "entity_type": "slip", "entity_id": "3f6c1e2a-8b4d-4f7a-9c2e-5d1b7a9e0c44",
      "status_before": "NEW_TEMPLATE", "status_after": "APPROVED",
      "changes": { "deal_type": { "old": null, "new": "OUTRIGHT" } },
      "details": { "template_id": "orca_treasury_g_sec_deal_slip", "template_version": 1 },
      "request_id": "ui-9f2a", "ip_address": "10.20.4.17", "user_agent": "Mozilla/5.0 ...",
      "outcome": "SUCCESS", "error": null,
      "prev_hash": "5d0c...e91a", "row_hash": "c4b7...02fd" }
  ],
  "total": 1, "limit": 50, "offset": 0
}
```

Errors: `401`, `422`, `429`. Audit: none (assumption; exports are audited). Full design:
`06_audit_trail.md`.

### 4.24 `GET /api/v1/audit/verify` — MVP

**Purpose:** prove the audit log has not been altered.

```json
{ "valid": true, "rows_checked": 1043, "first_id": 1, "last_id": 1043,
  "last_row_hash": "c4b7...02fd", "broken_at": null, "checked_at": "2026-09-27T12:00:00.000Z" }
```

On failure: `200` with `"valid": false, "broken_at": {"id": 812, "event_id": "...", "reason": "ROW_HASH_MISMATCH"}`
(the check ran; the result is data, not an HTTP error). Errors: `401`, `429`, `500`.
Audit: none (assumption; see `06_audit_trail.md` §3.2).

### 4.25 `GET /api/v1/audit/export` — Later

| Param | In | Type | Default |
|---|---|---|---|
| `format` | query | `csv` \| `xlsx` \| `json` | `csv` |
| `from` / `to` | query | date / timestamp | whole log |

Returns a file with every column including `prev_hash` / `row_hash`, so an auditor can re-verify offline.
Errors: `401`, `422`, `429`. Audit: `AUDIT_EXPORTED` (details: `format`, `from`, `to`, row count,
last exported `row_hash`).

---

## 5. Using Swagger UI

1. Start the API and open `http://localhost:8000/docs` (ReDoc: `/redoc`). If you get `404`,
   `BONDS_DOCS_ENABLED=false` is set (typical in production).
2. Click **Authorize** (padlock, top right). Scheme `HTTPBasic`: enter your `client_id` as
   *Username* and the client secret as *Password* → **Authorize** → **Close**. Swagger now sends the
   `Authorization` header on every call (credentials live only in the browser tab).
3. Upload: open **Slips → POST /api/v1/slips** → **Try it out** → *file* → **Choose File** → pick
   `samples/mock/01_gsec_outright_purchase.pdf` → optionally fill `X-Actor-Name` → **Execute**.
4. Copy `id` from the response body; use it in **GET /api/v1/slips/{id}**, `/preview`, `/approve`,
   `/export`.
5. For preview/approve paste a MappingSpec (§3.3) into the request body editor.
6. Downloads (`/pdf`, `/export`) show a **Download file** link in the response panel.

Getting credentials: an operator runs `python -m app.cli add-client <client_id>` (secret shown once;
see `07_security.md` §2).

---

## 6. Frontend integration guide

The frontend is a separate project. It must hold credentials server-side where possible (see
`07_security.md` §2.7) and send `X-Actor-Name` with the logged-in person's name on every call.

### 6.1 Inbox

```
GET /api/v1/slips?status=NEW_TEMPLATE,NEEDS_REVIEW&limit=50&offset=0     → review queue
GET /api/v1/slips?status=PARSED,APPROVED&limit=50&offset=0               → done list
POST /api/v1/slips (multipart)                                           → upload, then route by status
```

Route after upload: `PARSED` → deal view (optionally approve); `NEEDS_REVIEW` / `NEW_TEMPLATE` →
review screen; `UNREADABLE` → "scanned PDF, OCR not available yet"; `FAILED` → show `request_id` for
support. Paginate with `offset += limit` until `offset >= total`.

### 6.2 Review screen

```
GET /api/v1/slips/{id}          → SlipResult
GET /api/v1/slips/{id}/pdf      → blob → render with pdf.js
GET /api/v1/schema/fields       → dropdown options + required_for (cache per session)
GET /api/v1/templates?market=IN → "add version to existing template" option
```

Fetch the PDF with the `Authorization` header (`fetch` → `blob` → `URL.createObjectURL`) — a plain
`<iframe src>` cannot send the header.

Drawing highlights: for each page, rendered at `renderedWidth` pixels:

```js
const { width, height } = slip.page_sizes.find(p => p.page === pageNo);
const scale = renderedWidth / width;           // same factor for y (aspect preserved)
const [x0, top, x1, bottom] = field.bbox;      // PDF points, origin top-left
rect = { left: x0 * scale, top: top * scale, width: (x1 - x0) * scale, height: (bottom - top) * scale };
```

Suggested colours: mapped fields by `confidence` (≥ 0.90 green, lower amber), `unmapped` key_values
grey (use `key_bbox` + `value_bbox`), `missing_required` listed in a side panel. Clicking a key/value
lets the user pick a canonical field (→ `label_map[kv.key]`) or "ignore" (→ `_ignore`). Drawing a box
on the PDF produces a `region_rules` entry: convert pixels back with `/ scale`.

### 6.3 Preview loop

```
user edits mapping → debounce 300–500 ms → POST /slips/{id}/preview (MappingSpec)
                   → re-render deal, highlights, missing_required, validation
repeat until missing_required == [] and validation == [] (or the user accepts issues)
```

Preview never changes stored data, so it is safe to call often; it is audited as `SLIP_PREVIEWED`.

### 6.4 Approve

```
POST /slips/{id}/approve  (same MappingSpec + template_name or template_id, keywords, note)
  200 → show template id/version, slip APPROVED
  422 MISSING_REQUIRED_FIELDS        → highlight problem.errors[*].field
  409 VALIDATION_ISSUES_NOT_ACCEPTED → show checks; offer "approve anyway" → resend with accept_validation_issues=true
  409 TEMPLATE_EXISTS                → ask for another name or choose that template_id
  409 INVALID_STATE                  → reload slip (someone else approved it)
```

### 6.5 Download

```
GET /api/v1/slips/{id}/export?format=json|xml|xlsx   → save using Content-Disposition filename
GET /api/v1/exports/deals?format=xlsx&status=APPROVED&from=2026-09-01&to=2026-09-30   (Later)
```

### 6.6 History

`GET /api/v1/slips/{id}/history` for the slip timeline panel. Always log the `X-Request-ID` response
header on errors so support can find the audit rows.

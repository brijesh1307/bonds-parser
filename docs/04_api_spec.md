# 04 · API Specification

The REST API of the Bonds Deal Slip Parser, version `v0.1.0`. Names and rules come from
[`00_design_baseline.md`](00_design_baseline.md) (B§). The live, always-current contract is the
OpenAPI document served by the API itself: Swagger UI at `/docs`, ReDoc at `/redoc`, JSON at
`/openapi.json`.

**The API is stateless for slip data** ([ADR-0009](adr/0009-stateless-api-no-slip-storage.md)):
every PDF is processed in memory and discarded, and no parse result is kept. The only state is
the approved templates, the API clients and the audit log.

---

## 1. Endpoint summary

| Tag | Method | Path | Purpose | Audit |
|---|---|---|---|---|
| System | GET | `/health` | Liveness (open, no credentials) | – |
| Parse | POST | `/api/v1/parse?format=json\|xml\|xlsx` | Parse one PDF with the active templates | `SLIP_PARSED` |
| Templates | POST | `/api/v1/templates/preview` | PDF + draft mapping → the result it would give | `TEMPLATE_PREVIEWED` |
| | POST | `/api/v1/templates` | PDF + mapping → create a template or a new version | `TEMPLATE_CREATED` / `TEMPLATE_VERSION_ADDED` |
| | GET | `/api/v1/templates?is_active=&market=` | List templates | – |
| | GET | `/api/v1/templates/{id}` | One template with all versions | – |
| | PUT | `/api/v1/templates/{id}` | New version from an edited definition | `TEMPLATE_VERSION_ADDED` |
| | PATCH | `/api/v1/templates/{id}` | Enable / disable, rename, describe | `TEMPLATE_ENABLED` / `_DISABLED` / `_UPDATED` |
| | POST | `/api/v1/templates/import` | Import a template from another environment | `TEMPLATE_IMPORTED` |
| Schema | GET | `/api/v1/schema/fields` | Canonical fields for mapping dropdowns | – |
| | GET | `/api/v1/schema/markets` | Active market profiles | – |
| | GET | `/api/v1/schema/xsd` | XSD of the XML output | – |
| Audit | GET | `/api/v1/audit` | Search the audit log (newest first) | – |
| | GET | `/api/v1/audit/verify` | Verify the hash chain | – |

---

## 2. Conventions

### 2.1 Base URL, versioning

All business endpoints are under `/api/v1`. A breaking change to a `/api/v1` response needs
`/api/v2`; adding a field is not breaking.

### 2.2 Headers

| Header | Direction | Required | Meaning |
|---|---|---|---|
| `Authorization: Basic base64(client_id:client_secret)` | request | yes (not on `/health`) | Client credentials (B D8) |
| `X-Actor-Name` | request | no | Person using the client (informational, stored in the audit log; control characters removed, max 200) |
| `X-Request-ID` | request / response | no | Correlation id `[A-Za-z0-9._-]{1,128}`; generated when absent and always echoed back |
| `Content-Disposition` | response | – | File name of XML / Excel / XSD downloads |

### 2.3 Content types

Uploads are `multipart/form-data` with the PDF in the field `file`; preview and approve add a
`mapping` field holding JSON text. JSON bodies are `application/json`; XML downloads
`application/xml`; Excel downloads
`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`.

### 2.4 Data formats

Decimals are JSON strings in plain notation (`"50000000"`, `"101.2350"`), never floats (B D10).
Business dates are `YYYY-MM-DD`; timestamps UTC `YYYY-MM-DDTHH:MM:SS.ffffffZ` (B D11). Integers and
scores (`confidence`, `score`, `market_confidence`) are JSON numbers. `null` is always emitted.
Field-level formats: [`05_data_dictionary_and_outputs.md`](05_data_dictionary_and_outputs.md).

### 2.5 Errors (RFC 7807)

Every error is `application/problem+json`:

```json
{
  "type": "urn:bonds-parser:problem:approval-blocked",
  "title": "Approval blocked: required fields missing",
  "status": 422,
  "detail": "required fields still missing: price, consideration",
  "instance": "/api/v1/templates",
  "request_id": "3f2a9c0e8b7d4e1f9a0b1c2d3e4f5a6b",
  "errors": [{"field": "price", "msg": "missing"}, {"field": "consideration", "msg": "missing"}]
}
```

| Status | `type` slug | When |
|---|---|---|
| 400 | `invalid-mapping` | Mapping is not valid JSON / MappingSpec, unknown field path, bad regex, bad bbox, no `template_name` for a new template |
| 401 | `unauthorized` | Missing / wrong credentials, unknown or disabled client (`WWW-Authenticate: Basic realm="bonds-parser"`) |
| 404 | `not-found` | Unknown template or route |
| 409 | `validation-not-accepted` | Approve with ERROR validation issues and `accept_validation_issues=false` (`errors` lists them) |
| 409 | `conflict` | Template id already exists (import), version clash |
| 413 | `payload-too-large` | File larger than `BONDS_MAX_UPLOAD_MB` |
| 415 | `unsupported-media-type` | File does not start with `%PDF-` |
| 422 | `approval-blocked` | Approve while required fields are missing or the market is `UNKNOWN` |
| 422 | `too-many-pages`, `encrypted-pdf`, `invalid-pdf` | More than `BONDS_MAX_PAGES` pages; password-protected; unreadable |
| 422 | `request-validation` | Request does not match the schema (missing `file`, bad `format`, …) |
| 429 | `auth-blocked` | The caller's IP is blocked after too many failed logins (`Retry-After` seconds) |
| 500 | `internal-error` | Unexpected error; no internals in the body, details in the server log by `request_id` |
| 503 | – (`/health` body) | Template folder or audit log not writable |

---

## 3. Shared objects

### 3.1 ParseResult

Returned by `/parse` (JSON) and `/templates/preview`, and inside `ApproveResult`.

| Key | Type | Meaning |
|---|---|---|
| `status` | enum | `PARSED`, `NEEDS_REVIEW`, `NEW_TEMPLATE`, `UNREADABLE`, `FAILED` (B§3) |
| `source` | object | `file` (name as uploaded), `pages`, `page_sizes` (`[[width, height], …]` in points), `sha256` |
| `market` | object | `market`, `issuer_country`, `slip_locale`, `market_confidence`, `profile_available`, `signals[]` |
| `template` | object | `template_id`, `name`, `version`, `score`, `threshold`, `matched`, `is_draft` |
| `deal` | object | The canonical deal; every key always present ([05 §1-§3](05_data_dictionary_and_outputs.md)) |
| `fields` | object | Field path → `{value, raw, method, confidence, label, page, bbox}` for every field with a value |
| `key_values` | list | Every extracted `{key, value, source, page, key_bbox, value_bbox, mapped_to}` |
| `missing_required` | list | Required field paths with no value |
| `validation` | list | `{code, severity, message, fields, expected, actual, tolerance}` for failed checks |
| `low_confidence` | list | Required fields below `BONDS_CONFIDENCE_THRESHOLD` |
| `unmapped` | list | `key_values` that map to no field and are not ignored by the template |

`bbox` = `[x0, top, x1, bottom]` in PDF points, origin top-left; scale with `page_sizes[page-1]`.

### 3.2 MappingSpec (the `mapping` form field)

```json
{
  "label_map": {"Contract Dt": "trade_date", "Paper": "security_name", "GST No": "_ignore"},
  "region_rules": [{"field": "counterparty", "page": 1, "bbox": [320, 140, 560, 158]}],
  "regex_rules": [{"field": "yield", "pattern": "at a yield of ([\\d.]+)%", "group": 1}],
  "constants": {"platform": "OTC"},
  "template_id": null,
  "template_name": "Broker XYZ G-Sec confirmation",
  "description": "XYZ Securities contract notes",
  "keywords": ["XYZ Securities"],
  "date_order": null,
  "accept_validation_issues": false,
  "note": "first onboarding"
}
```

| Key | Meaning |
|---|---|
| `label_map` | Slip label as printed → canonical field path (`GET /schema/fields`), `repo.<field>`, or `_ignore`. Keys are normalised (case, punctuation) when saved. |
| `region_rules` | Read the text inside a box: `page` (1-based) and `bbox` in PDF points |
| `regex_rules` | Regex over the slip text; `group` = capture group (default 1) |
| `constants` | Value used when the slip does not state the field itself |
| `template_id` | Existing template → the approval adds a **new version** |
| `template_name` | Name of a **new** template (required when `template_id` is null); its id is the slug of the name |
| `keywords` | Text that must appear on the slip for the template to match |
| `date_order` | `DMY` / `MDY` for ambiguous numeric dates on this layout |
| `accept_validation_issues` | Approve even though ERROR validation issues remain |
| `note` | Stored with the version |

Fields not given fall back to the base version (new versions) or to the built-in synonyms.

### 3.3 TemplateOut and ApproveResult

`TemplateOut`: `id, name, description, market, is_active, current_version, created_by, created_at,
updated_at, definition` (the current version's definition, B§6) and `versions[]` (each
`{version, definition, note, created_by, created_at}`, on `GET /templates/{id}`, `PUT`).

`ApproveResult`: `{template: TemplateOut, result: ParseResult}` — `result` is the slip parsed
again with the saved template (normally `PARSED`).

### 3.4 AuditEventOut

`seq, event_id, occurred_at, actor_type, actor_id, actor_name, action, entity_type, entity_id,
outcome, error, details, request_id, ip_address, user_agent, prev_hash, row_hash` (B§6,
[06](06_audit_trail.md)).

---

## 4. Endpoints

### 4.1 `GET /health`

Open (no credentials). 200 when the template folder and the audit log are writable, else 503.

```json
{"status": "ok", "templates": "ok", "audit": "ok", "version": "0.1.0", "time": "2026-10-02T08:45:59.338879Z"}
```

### 4.2 `POST /api/v1/parse`

Parse one PDF with the **active** templates. A layout that matches a template comes back `PARSED`
(or `NEEDS_REVIEW` when a check fails); an unknown layout comes back `NEW_TEMPLATE` with the
best-effort deal and every key/value.

| Param | In | Type | Default |
|---|---|---|---|
| `file` | form | PDF | – |
| `format` | query | `json` \| `xml` \| `xlsx` | `json` |

```bash
curl -u frontend:$SECRET -H "X-Actor-Name: Priya Shah" \
     -F "file=@samples/mock/01_gsec_outright_purchase.pdf;type=application/pdf" \
     "http://127.0.0.1:8000/api/v1/parse?format=json"
```

`json` → `ParseResult`. `xml` / `xlsx` → file download named after `deal_id` (or the file name);
the XML validates against `GET /api/v1/schema/xsd`. Errors: 401, 413, 415, 422, 429, 500.
Audit: `SLIP_PARSED` with `details = {sha256, size, pages, status, market, template_id,
template_version, match_score, format}` — never the file name or deal values; a rejected upload
is recorded with `outcome = FAILURE` and the error slug.

### 4.3 `POST /api/v1/templates/preview`

Parse the PDF with a **draft** template built from `mapping`. Nothing is saved; use it in a loop
while mapping a layout. `template.is_draft = true`; `status` is the status the slip would get.

```bash
curl -u frontend:$SECRET -F "file=@slip.pdf" \
     -F 'mapping={"label_map": {"Contract Dt": "trade_date", "Rate": "price"}}' \
     http://127.0.0.1:8000/api/v1/templates/preview
```

Errors: 400 (invalid mapping), 401, 413, 415, 422 (PDF), 429. Audit: `TEMPLATE_PREVIEWED`.

### 4.4 `POST /api/v1/templates`

Approve: save the mapping as a template (`template_name`) or as a new version of `template_id`.
The PDF is parsed again with the draft; the approval is refused with

- **422 `approval-blocked`** while required fields are missing or the market is `UNKNOWN`;
- **409 `validation-not-accepted`** when ERROR validation issues remain and
  `accept_validation_issues` is false.

Required fields that were only found by derivation, text search or fuzzy matching are recorded
as `accepted_derived`, so later slips in the layout reach the confidence threshold.

Response **201** `ApproveResult`. Audit: `TEMPLATE_CREATED` or `TEMPLATE_VERSION_ADDED`.

### 4.5 `GET /api/v1/templates`, `GET /api/v1/templates/{id}`

List (`is_active`, `market` filters) and detail (with `versions`). 404 for an unknown id.

### 4.6 `PUT /api/v1/templates/{id}`

Body `{"definition": TemplateDefinition, "note": "…"}` → version `current + 1` (no PDF needed).
The definition is validated like a mapping (400). Response 201 `TemplateOut` with versions.
Versions are never overwritten.

### 4.7 `PATCH /api/v1/templates/{id}`

Body with any of `name`, `description`, `is_active`. A disabled template is not used by `/parse`.
Audit: `TEMPLATE_ENABLED` / `TEMPLATE_DISABLED`, and `TEMPLATE_UPDATED` with `{changes: {field:
{old, new}}}` for name / description.

### 4.8 `POST /api/v1/templates/import`

Body `{"name": "…", "description": "…", "definition": TemplateDefinition}` — typically the
`definition` of `GET /templates/{id}` from another environment. Creates the template as version 1
under `definition.template_id`; 409 if that id exists (add a version with `PUT` instead).

### 4.9 `GET /api/v1/schema/fields`, `/schema/markets`, `/schema/xsd`

`fields`: `[{name, type, kind, enum, required_for, description}]` — every path a mapping may
target (market fields are detected, not mapped; `_seller_amount` etc. are working fields).
`markets`: active market profiles. `xsd`: the XSD of the XML output (also
[`schemas/parse_result.xsd`](schemas/parse_result.xsd)).

### 4.10 `GET /api/v1/audit`, `GET /api/v1/audit/verify`

Search: `action`, `actor_id`, `entity_id`, `from`, `to` (ISO prefixes, UTC), `limit` (≤ 500),
`offset` → `{items, total, limit, offset}`, newest first. Verify →
`{ok, checked, first_bad_seq, reason, last_hash}`.

---

## 5. Using Swagger UI

1. Create a client: `python -m app.cli add-client frontend` (the secret is printed once).
2. Open `/docs`, click **Authorize**, enter `frontend` and the secret.
3. `POST /api/v1/parse` → *Try it out* → choose a PDF → *Execute*. Set `format` to `xlsx` or `xml`
   to download a file.
4. For onboarding, use `POST /api/v1/templates/preview` with the same PDF and paste the mapping
   JSON into the `mapping` field.

---

## 6. Frontend integration guide

The frontend keeps the PDF the user selected (the API stores nothing) and sends it with each call.

| Screen | Calls |
|---|---|
| **Upload / inbox** | `POST /parse` per file; keep the results client-side; group by `status` |
| **Review** | Show the PDF (from the browser's own copy) next to `deal`; draw `fields[*].bbox` and `key_values[*].value_bbox` scaled by `page_sizes`; colour by `confidence` (≥ 0.90 green, ≥ 0.80 amber, else red); list `missing_required`, `validation`, `unmapped` |
| **Mapping dropdowns** | `GET /schema/fields` once |
| **Preview loop** | Each edit → `POST /templates/preview` with the PDF + current mapping; show the new `status`, `deal`, `missing_required` |
| **Approve** | `POST /templates`; on 409 show `errors[]` and offer "approve anyway" (`accept_validation_issues: true`); on 422 show the missing fields |
| **Templates** | `GET /templates`, `GET /templates/{id}`, `PATCH` to disable, `PUT` to edit |
| **Download** | `POST /parse?format=xlsx\|xml` |

Bounding-box scaling for a page rendered at `w × h` pixels:

```js
const [pw, ph] = result.source.page_sizes[page - 1];
const [x0, top, x1, bottom] = bbox;
const rect = { left: x0 / pw * w, top: top / ph * h, width: (x1 - x0) / pw * w, height: (bottom - top) / ph * h };
```

Send `X-Actor-Name` with the logged-in user's name and `X-Request-ID` per action so audit records
can be traced. Keep the client secret on the frontend's **server side** (BFF), never in browser
code ([07 §2](07_security.md)).

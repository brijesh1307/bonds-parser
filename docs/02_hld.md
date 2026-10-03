# 02 · High-Level Design

Architecture of the Bonds Deal Slip Parser `v0.1.0`. Decisions and names:
[`00_design_baseline.md`](00_design_baseline.md) (B§) and the [ADRs](adr/README.md).

---

## 1. Goals and non-goals

**Goals**
- Turn any bond deal slip PDF into one canonical deal (JSON / XML / Excel) with checked numbers.
- Onboard a new slip layout once (preview → approve); parse it automatically afterwards.
- **Store nothing from the slips** (ADR-0009).
- Be secure and auditable: client credentials, a tamper-evident audit log with metadata only.
- Stay simple to run: one process, no database, two small folders.

**Non-goals**: the frontend (separate project), user roles / SSO, storing or reporting on past
deals, OCR of scans (later), markets other than India beyond detection (later).

## 2. Architectural drivers

| Driver | Consequence |
|---|---|
| Accuracy on money values | Decimal end to end, validation of every amount, confidence per field, `PARSED` only when everything checks out |
| Data protection | Stateless: PDFs live in memory for one request; audit holds metadata only; PANs masked in audit |
| Many layouts, changing over time | Template-driven parsing with a strong generic fallback; templates are versioned files |
| Auditability | Append-only hash-chained log of every action |
| Small team, simple operations | No database, no queue; one container; config by environment variables |

## 3. System context

```
            ┌──────────────────────────┐        ┌──────────────────────────┐
            │ Frontend (separate)      │        │ Other systems / scripts  │
            │ review, mapping, approve │        │ batch parsing, downstream│
            └────────────┬─────────────┘        └────────────┬─────────────┘
                         │  HTTPS + HTTP Basic (client credentials)  │
                         └──────────────────┬───────────────────────┘
                                            ▼
                         ┌──────────────────────────────────────────┐
                         │      Bonds Deal Slip Parser API          │
                         │   (stateless for slips; PDFs in memory)  │
                         └───────┬──────────────┬───────────────┬───┘
                                 │              │               │
                     templates/*.json   data/clients.json   data/audit/audit.jsonl
                     (labels + rules)   (Argon2 hashes)     (metadata only)
```

## 4. Components

```
app/
├── api/        HTTP layer: routers (parse, templates, schema, audit, health), auth dependency,
│               request context, RFC 7807 errors
├── services/   parse_service (upload → engine → schema), template_service (preview, approve,
│               versions, import), template_store (JSON files), audit_service (JSON-lines log)
├── engine/     pure parser: extract → market → detect → mapping → normalize → derive →
│               validate → pipeline; market profiles (IN.json)
├── export/     JSON, XML (+ XSD), Excel writers
├── schemas/    Pydantic request/response models (generate the OpenAPI / Swagger contract)
├── auth.py     client store (Argon2), brute-force limiter
├── config.py   BONDS_* settings
└── cli.py      client management, audit verification, debug-extract
```

| Component | Responsibility | Must not |
|---|---|---|
| API | Authenticate, parse requests, call services, shape responses and errors, audit | Contain parsing logic |
| Services | Orchestrate a use case; read uploads safely; enforce approve rules | Store slip data |
| Engine | PDF → canonical deal + evidence, deterministic | Import FastAPI, files, services (enforced by a test) |
| Export | Render a result as JSON / XML / Excel; generate the XSD | Know about HTTP |
| Template store | Atomic, versioned JSON files | Hold PDFs or deal values |
| Audit log | Append, search, verify | Hold file names, deal values or secrets |

## 5. Main flows

**(a) Known layout → direct answer**
1. Client posts the PDF to `/api/v1/parse`; credentials checked; size and `%PDF-` checked.
2. Engine extracts key/values, detects the market, matches the best active template.
3. Labels are mapped (template first, then synonyms), values normalised, rules applied, missing
   fields derived, numbers validated.
4. All required fields present, checks pass, confidence ≥ 0.90 → `PARSED`.
5. Response in JSON / XML / Excel; one `SLIP_PARSED` audit record; the PDF is discarded.

**(b) New layout → template**
1. `/parse` returns `NEW_TEMPLATE`, the best-effort deal and every key/value with positions.
2. The frontend shows them; the user maps labels, adds rules or constants.
3. `/templates/preview` (PDF + mapping) returns the result that mapping would give; repeat.
4. `/templates` (PDF + mapping) checks required fields (422) and validation (409), saves
   template v1, and returns the slip parsed with it.
5. The next slip in the layout matches the template and comes back `PARSED`.

**(c) Layout drift** — a bank changes a label: the fingerprint score drops or a required field
goes missing → `NEW_TEMPLATE` / `NEEDS_REVIEW`; the user approves with `template_id` set →
version 2. Version 1 stays in the file.

## 6. Parsing strategy

- **Extraction** reads ruled tables (2-column, 4-column, merged-cell letters, grids with one or
  many value rows), text lines (`Label: value`, `Label :- value`, `A: x | B: y`) and prose
  patterns, each with page positions.
- **Market first** (ADR-0007): a weighted vote of ISIN prefix, currency, settlement system,
  platform, name style and number format picks the market profile that decides date order,
  units (lakh / crore) and defaults.
- **Template match**: Jaccard of the slip's label set against each active template (≥ 0.80) plus
  optional keywords.
- **Mapping chain**: template label map (1.00) → synonyms (0.95) → abbreviations (0.90) →
  compound labels (`SECURITY NAME/ISIN NUMBER`) → fuzzy (≤ 0.85) → text (0.80).
- **Derivation** fills implied fields (side and counterparty from `Our Buy from:-`, per-unit face
  value, stamp duty, repo leg 1 → top level, coupon / tenor from the security name, …).
- **Validation** recomputes principal, consideration, accrued, discount, quantity and repo legs.

## 7. Data architecture

| Data | Where | Lifetime |
|---|---|---|
| Slip PDF, extracted text, parse result | Request memory only | One request |
| Templates (labels, rules, constants, versions) | `BONDS_TEMPLATES_DIR/*.json` | Until deleted; versions never overwritten |
| API clients | `BONDS_CLIENTS_FILE` (Argon2 hashes) | Until disabled |
| Audit records (metadata) | `BONDS_AUDIT_FILE` (append-only) | Retention `BONDS_AUDIT_RETENTION_YEARS` |

## 8. Security architecture

HTTP Basic client credentials over HTTPS (TLS at a reverse proxy); secrets as Argon2 hashes;
constant-time checks; per-IP lockout after repeated failures; CORS allow-list; upload limits and
`%PDF-` check; no external calls with slip data (no LLM, no cloud OCR). Details:
[07](07_security.md).

## 9. Deployment

**Single host (MVP and small production)** — one container (`Dockerfile`, non-root, one Uvicorn
worker) behind a TLS reverse proxy; `templates/` and `data/` on persistent volumes; backups of
those two folders only.

```
 client ──HTTPS──► reverse proxy (TLS) ──HTTP──► api container :8000
                                                   ├── /srv/bonds/templates  (volume)
                                                   └── /srv/bonds/data       (volume)
```

**Scaling out** — the API holds no slip state, but the template, client and audit files assume a
single writer. More than one instance needs a shared store for those three (out of scope for
v0.1.0, ADR-0009).

## 10. Quality attributes

| Attribute | Approach |
|---|---|
| Performance | Pure-Python parse of a 1–5 page text PDF in well under a second; no I/O besides two small files |
| Availability | Stateless restarts; `/health` checks the two writable stores; container healthcheck |
| Observability | Access log with request id and client; audit log for business actions |
| Testability | Pure engine; golden files for 6 mock slips; API, auth and audit tests; 94 % coverage |
| Maintainability | Baseline-first docs, ADRs, strict typing, lint and CI gates |

## 11. System design principles applied

| Principle | How |
|---|---|
| Separation of concerns / layering | API → services → engine / export; the engine imports no web or storage code (tested) |
| Single responsibility | One module per pipeline step; one store per kind of state |
| Open / closed | New layouts are templates, new markets are profile files — no code change |
| Data minimisation | Slips are never stored; audit holds metadata only (ADR-0009) |
| Fail-safe defaults | Uncertain data is never `PARSED`: missing fields, failed checks, low confidence or unknown market → review |
| Defence in depth | Auth + lockout + upload checks + CORS + TLS + audit |
| Immutability | Template versions and audit records are append-only |
| Single source of truth | Baseline for names; the XSD is generated from the response models |
| Configuration over code | `BONDS_*` environment variables (12-factor) |
| KISS / YAGNI | Files, not a database; one process; HTTP Basic, not RBAC |

## 12. Technology choices

| Need | Choice | Rejected |
|---|---|---|
| API | FastAPI + Pydantic v2 (ADR-0001) | Flask, Django REST |
| PDF | pdfplumber (ADR-0002) | PyMuPDF (licence), camelot (tables only), cloud OCR (data leaves) |
| State | JSON files (ADR-0009) | SQLite / PostgreSQL (ADR-0004, superseded) |
| Auth | HTTP Basic + Argon2 (ADR-0005) | OAuth2 / RBAC (not needed yet) |
| Money | Decimal strings (ADR-0008) | Floats |

## 13. Limitations and evolution

Scans need OCR; password-protected and multi-deal PDFs are rejected / treated as one deal; only
the India profile is active; one writer per template folder. Evolution: OCR, `pikepdf`, more
market profiles, holiday calendar, metrics, shared stores if several instances are needed.

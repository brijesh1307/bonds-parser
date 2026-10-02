# 01 · Software Requirements Specification

> **Update 2026-10-02 — stateless design ([ADR-0009](adr/0009-stateless-api-no-slip-storage.md)).**
> Deal slips are never stored and there is no database. Wherever this document describes a `slips`
> table, `/api/v1/slips/*` endpoints, SQLite / PostgreSQL, uploads storage or slip statuses such as
> `APPROVED`, [`00_design_baseline.md`](00_design_baseline.md) is authoritative: templates, clients and
> the audit log are files, and preview / approve take the PDF + mapping in one request.

Names, values and decisions in this document follow `docs/00_design_baseline.md` (the baseline).
If this document disagrees with the baseline, the baseline wins. Section references such as
"B§7" point to baseline sections; "F§5" points to `docs/deal_slip_standard_formats.md`.

---

## 1. Purpose

Treasury back offices receive bond deal slips as PDFs from dealers, NDS-OM, CCIL, RBI E-Kuber,
exchanges (NSE/BSE RFQ), EBP, brokers and counterparties. Every source prints its own layout,
so today the data is keyed in by hand. The **Bonds Deal Slip Parser** removes that re-keying: it
reads a slip PDF, maps it to one canonical deal, validates the numbers and returns the deal as
JSON, XML or Excel through a REST API. It also flags what it is not sure about instead of
guessing.

This SRS states what the system must do (functional requirements), how well it must do it
(non-functional requirements), and the assumptions, constraints and risks behind both.

---

## 2. Scope

### 2.1 In scope

| Area | Included |
|---|---|
| API | FastAPI REST API under `/api/v1`, Swagger UI `/docs`, ReDoc `/redoc`, OpenAPI `/openapi.json`, `/health` |
| Parsing | Text-layer PDFs; key/value extraction from tables, grids, "Label: value" text and prose; market detection; mapping; normalisation; derivation; validation; status decision (B§3, B§4) |
| Markets | India (`IN`) fully parsed. Other markets detected and routed to review (`NEEDS_REVIEW`) |
| Deal types | `OUTRIGHT, PRIMARY_AUCTION, PRIMARY_PLACEMENT, REPO, REVERSE_REPO, TREPS_BORROW, TREPS_LEND, LAF` |
| Templates | Onboarding of new layouts (preview, approve), versioned storage, auto-match of known layouts, JSON import/export |
| Outputs | JSON, XML, Excel (`xlsx`) |
| Security | HTTP Basic client credentials in the header, Argon2-hashed secrets, CLI for client management |
| Audit | Append-only `audit_log` with SHA-256 hash chain, verify and search endpoints |
| Storage | SQLite (MVP), PostgreSQL (production); uploaded PDFs on disk |

### 2.2 Out of scope

| Item | Note |
|---|---|
| Frontend / UI | Separate project. This API provides everything the frontend needs (key/values with bbox, preview, approve) |
| RBAC / user roles | Not implemented. Every active client has the same rights |
| SSO / OAuth / LDAP | Not implemented. `X-Actor-Name` is informational only |
| Booking into core treasury / accounting systems | Integrating systems consume the API output; they own booking |
| Price / yield calculation as a pricing engine | Values are read from the slip and cross-checked, not independently priced |
| LLMs or any external call carrying slip data | Excluded by design (B§2 D6) |
| Excel, email-body and SWIFT MT515/518 inputs | PDF only. Other input formats are future work (assumption A-08) |

---

## 3. Stakeholders and users

| Stakeholder | Interest | How they use the system |
|---|---|---|
| Back-office operations | Fast, correct deal capture; fewer keying errors | Through the frontend: upload slips, work the review queue, map new layouts once, export deals |
| Frontend developer | A stable, well-documented API contract | Swagger/OpenAPI, key/values with page and bbox, preview/approve payload (B§7) |
| Integrating systems (treasury, settlement, reconciliation) | Machine-readable deals with reliable status | `POST /api/v1/parse` or `/api/v1/slips` + export; consume only `PARSED` / `APPROVED` automatically |
| Auditors / compliance | Evidence of who did what, when, and that records were not altered | `GET /api/v1/audit`, `/audit/verify`, `/slips/{id}/history`, audit export |
| IT operations | Simple deployment, monitoring, backups, credential control | `/health`, env vars (B§9), CLI (`init-db`, `add-client`, `list-clients`, `disable-client`, `rotate-secret`), logs |
| Product owner / treasury head | Coverage of slip sources, accuracy, go-live readiness | Accuracy reports, UAT sign-off |

---

## 4. Functional requirements

Priority: **MVP** = in the 2-hour MVP (B§11); **Later** = after MVP.

| ID | Requirement | Priority | Acceptance criterion |
|---|---|---|---|
| FR-01 | **Upload and parse.** `POST /api/v1/slips` accepts one PDF (multipart), stores it at `data/uploads/<slip_id>.pdf`, records SHA-256, runs the pipeline (B§4) and returns the slip with `status`, deal, fields, key_values and validation. | MVP | Uploading each of the 4 mock slips returns 201 with a `slip_id`, a status from B§3, and a row in `slips` plus `SLIP_UPLOADED` and `SLIP_PARSED` audit rows. |
| FR-02 | **Upload limits and duplicates.** Reject files > `BONDS_MAX_UPLOAD_MB` (413) and PDFs > `BONDS_MAX_PAGES` pages (422); reject non-PDF content (415). A file whose SHA-256 matches an existing slip is stored with `duplicate_of` set. | MVP | An 11 MB file returns 413; a 21-page PDF returns 422; re-uploading a mock slip returns a new slip with `duplicate_of` = first slip id. Errors are `application/problem+json`. |
| FR-03 | **Batch upload.** `POST /api/v1/slips/batch` accepts many PDFs and returns one result per file; one bad file does not fail the batch. | Later | Batch of 4 mocks + 1 corrupt file returns 5 results: 4 parsed, 1 `FAILED`/rejected with reason. |
| FR-04 | **One-shot parse.** `POST /api/v1/parse?format=json\|xml\|xlsx` parses a PDF and returns the deal in the requested format without storing the slip or PDF. | MVP | Each mock slip returns the expected deal in all three formats; `slips` row count is unchanged; the call is audited without the file content. |
| FR-05 | **Key/value return for unknown layouts.** When no template scores ≥ `BONDS_TEMPLATE_MATCH_THRESHOLD` (0.80), status is `NEW_TEMPLATE` and the response still contains the best-effort deal plus **all** extracted key/values with `key, value, source, page, key_bbox, value_bbox`. | MVP | A mock slip uploaded with no templates in the DB returns `NEW_TEMPLATE`, a non-empty `key_values` list, and every key/value has page and both bboxes. |
| FR-06 | **Preview.** `POST /api/v1/slips/{id}/preview` re-runs the pipeline with a draft mapping payload (B§7) and returns the resulting deal, fields and validation. Nothing is saved except the `SLIP_PREVIEWED` audit row. | MVP | Preview with a label_map changes the mapped fields in the response; `templates`, `template_versions` and `slips.status` are unchanged. |
| FR-07 | **Approve → template.** `POST /api/v1/slips/{id}/approve` with the mapping payload creates a new template (or a new version of `template_id`), stores `source_slip_id`, sets the slip to `APPROVED`, and records `approved_by`, `approved_actor_name`, `approved_at`. Approval is refused if validation fails unless `accept_validation_issues=true`. | MVP | After approving mock 01, `templates` has 1 row, `template_versions` has version 1, slip status is `APPROVED`, audit has `SLIP_APPROVED` and `TEMPLATE_CREATED`. Approving with failed validation and the flag false returns 409; approving with missing required fields returns 422. |
| FR-08 | **Template versioning.** Template versions are immutable. A change creates version n+1 and moves `current_version`; old versions stay readable. `UNIQUE(template_id, version)`. | MVP (create via approve); Later (`PUT` / `PATCH` / `import` / template history) | Approving a second mapping for the same `template_id` yields versions 1 and 2, both retrievable via `GET /api/v1/templates/{id}`; `TEMPLATE_VERSION_ADDED` is audited. |
| FR-09 | **Auto-parse known layouts.** On upload, the fingerprint (normalised label set) is compared by Jaccard with active templates; the best score ≥ threshold wins and its label_map, region_rules, regex_rules and constants are applied. `match_count` and `last_matched_at` update. | MVP | After approving mock 01, re-uploading mock 01 returns `PARSED` with `template_id`, `template_version`, `match_score` ≥ 0.80 and output equal to `samples/mock/expected/01_gsec_outright_purchase.json`. |
| FR-10 | **Market detection.** Before normalisation, a vote of signals (ISIN prefix, CUSIP/SEDOL, currency, settlement system, platform, name style, number format, BIC) sets `market`, `issuer_country`, `slip_locale`, `market_confidence`. Non-`IN` or uncertain market → `NEEDS_REVIEW` (never `PARSED`) until a market profile exists. | MVP | All 4 mocks detect `IN`. A synthetic slip with `US` ISIN and USD is returned as `NEEDS_REVIEW` with market `US`. `GET /api/v1/schema/markets` lists available profiles (Later). |
| FR-11 | **Normalisation.** Amounts (Indian/western grouping, lakh, crore), dates (day-first for `IN`), rates, ISIN, enums normalised per market profile. Money and rates are `Decimal`, serialised as strings; dates ISO `YYYY-MM-DD`. | MVP | `5.00 Cr` → `"50000000"`; `24-Sep-2026` → `2026-09-24`; no float appears in any JSON output. |
| FR-12 | **Validation.** Run all checks in F§5 (ISIN check digit, dates, principal, consideration, discount, quantity, repo legs, required fields per B§5) plus holiday/weekend settlement. Failures are listed in `_validation`; they never stop the parse. | MVP | Mocks 01–04 pass all checks. A mock with a corrupted ISIN check digit returns `NEEDS_REVIEW` with an ISIN entry in validation. |
| FR-13 | **Field confidence and status decision.** Each field carries `method` and confidence per B§4 table; status is set per B§3 using `BONDS_CONFIDENCE_THRESHOLD` (0.90). `accepted_derived` fields get 0.95 for that template. | MVP | A required field found only by `text` (0.80) makes the slip `NEEDS_REVIEW`; after it is listed in `accepted_derived`, the same slip is `PARSED`. |
| FR-14 | **Scanned slips.** A PDF with no text layer is marked `UNREADABLE` (MVP); OCR (Tesseract) extracts text (Later). | MVP (detect); Later (OCR) | An image-only PDF returns `UNREADABLE`, not `FAILED` or an empty `PARSED`. |
| FR-15 | **Export single slip.** `GET /api/v1/slips/{id}/export?format=json\|xml\|xlsx` returns the deal; XML via ElementTree, Excel via openpyxl. Status and validation are included so consumers can see flags. | MVP | Export of each mock in each format opens cleanly; values match the JSON output byte-for-byte as strings; `SLIP_EXPORTED` audited. |
| FR-16 | **Combined export.** `GET /api/v1/exports/deals?format=&status=&from=&to=` exports many deals in one file. XSD for XML (Later). | Later | Export with `status=PARSED` returns only parsed deals; `DEALS_EXPORTED` audited. |
| FR-17 | **Slip retrieval and review queue.** `GET /api/v1/slips` (filters `status, market, template_id, limit, offset`), `GET /api/v1/slips/{id}`, `GET /api/v1/slips/{id}/pdf`. | MVP | Filtering by `status=NEW_TEMPLATE` returns only those slips; PDF download returns the original bytes (SHA-256 matches); `SLIP_VIEWED` / `SLIP_PDF_DOWNLOADED` audited. |
| FR-18 | **Reparse and delete.** `POST /api/v1/slips/{id}/reparse` re-runs with current templates; `APPROVED` slips only with `force=true`. `DELETE /api/v1/slips/{id}` removes slip and PDF but keeps audit rows. | Later | Reparse of `APPROVED` without `force` returns 409; with `force` it re-evaluates and audits `SLIP_REPARSED`. Delete leaves `audit_log` intact and chain verification still passes. |
| FR-19 | **Templates read / manage.** `GET /api/v1/templates`, `GET /api/v1/templates/{id}` (MVP); `PUT` (new version), `PATCH` (enable/disable, rename), `POST /import`, `GET /{id}/history` (Later). | MVP / Later | Disabled template is not used for matching; each change produces the matching audit action (B§6). |
| FR-20 | **Audit trail.** Every business action and auth failure writes one `audit_log` row in the same DB transaction, with actor, action, entity, status before/after, changes, request id, IP, user agent, outcome, `prev_hash`, `row_hash`. UPDATE/DELETE on `audit_log` are blocked by triggers. | MVP | An `UPDATE audit_log` statement fails. Rolling back a failed action leaves no orphan audit row. Every action in B§6 "Audit actions" that the MVP implements appears after the API flow test. |
| FR-21 | **Audit query and verify.** `GET /api/v1/audit` (filters), `GET /api/v1/audit/verify` (recomputes the hash chain). Audit export csv/xlsx/json (Later). | MVP / Later | Verify returns OK on a clean DB and reports the first broken row after a row is altered directly in SQLite with triggers dropped. |
| FR-22 | **Header auth.** All endpoints except `/health` require `Authorization: Basic base64(client_id:client_secret)`. Secrets are Argon2-hashed. Missing/invalid credentials → 401 with `WWW-Authenticate`, audited as `AUTH_FAILED`. Inactive client → 401. | MVP | Calls without the header return 401; valid client returns 200 and updates `last_used_at`; no secret appears in logs or audit. |
| FR-23 | **Brute-force limiter.** After `BONDS_AUTH_MAX_FAILURES` failures per IP within `BONDS_AUTH_WINDOW_SECONDS`, the IP is blocked for `BONDS_AUTH_BLOCK_SECONDS` (429), audited as `AUTH_BLOCKED`. | Later | 11th bad attempt within 300 s returns 429 for 900 s. |
| FR-24 | **Request context headers.** `X-Actor-Name` (optional) is stored in audit as `actor_name`; `X-Request-ID` is accepted or generated and always echoed in the response. | MVP | Response always carries `X-Request-ID`; audit rows for that call share the same `request_id`. |
| FR-25 | **Client management CLI.** `python -m app.cli init-db \| add-client \| list-clients \| disable-client \| rotate-secret`. `add-client` / `rotate-secret` print the secret once. Actions audited with `actor_type=cli`. | MVP | New client can call the API; after `disable-client` it gets 401; after `rotate-secret` the old secret fails. `CLIENT_ADDED`, `CLIENT_DISABLED`, `CLIENT_SECRET_ROTATED` audited. |
| FR-26 | **Schema endpoints.** `GET /api/v1/schema/fields` returns canonical fields with type, required-by-deal-type and enum values (MVP). `GET /api/v1/schema/markets` returns available market profiles (Later). | MVP / Later | Field list matches B§5, including the `repo` sub-fields and all enums. |
| FR-27 | **History endpoints.** `GET /api/v1/slips/{id}/history` returns the slip's audit timeline (MVP). `GET /api/v1/templates/{id}/history` returns the template timeline with version diffs (Later). | MVP / Later | After upload → preview → approve → export, slip history lists those 4+ events in order with actor and timestamps. |
| FR-28 | **Errors.** All errors are RFC 7807 `application/problem+json` with `type, title, status, detail, instance` and the request id. | MVP | 401, 404, 413, 422 and 500 responses validate against the problem+json shape. |
| FR-29 | **Health.** `GET /health` is open and reports liveness plus DB connectivity. | MVP | Returns 200 when DB is reachable, 503 when not. |
| FR-30 | **Password-protected PDFs.** Detect and report encrypted PDFs; unlock with a supplied password via pikepdf. | Later | Encrypted PDF without password returns a clear 422 problem, not `FAILED`; with password it parses. |
| FR-31 | **Multi-deal PDFs.** Detect more than one deal in one PDF and split into one deal per slip record. | Later | A 2-deal CCIL grid returns 2 deals; MVP returns `NEEDS_REVIEW` with a multi-deal validation flag instead of a merged deal (assumption A-05). |

---

## 5. Non-functional requirements

| ID | Category | Requirement | Measure / target |
|---|---|---|---|
| NFR-01 | Performance | Parse latency for a 1–5 page text PDF, server side, excluding network upload. | ≤ 3 s p95; ≤ 1.5 s median, on a 2 vCPU / 4 GB host. One-shot and upload paths alike |
| NFR-02 | Throughput | Sustain a normal back-office day. | ≥ 2,000 slips/day with 4 workers; batch of 50 slips ≤ 2 min (Later) |
| NFR-03 | Accuracy – known layouts | Share of slips from onboarded templates that finish `PARSED` without human change. | ≥ 95 % auto-`PARSED`, measured on UAT and parallel-run slips |
| NFR-04 | Accuracy – safety | No wrong value is ever delivered without a flag. | 0 wrong values in `PARSED` output (a field is either correct, or the slip is `NEEDS_REVIEW`/has a `_validation` entry). Any breach is a Sev-1 defect |
| NFR-05 | Accuracy – regression | Mock and real golden slips never regress. | 100 % of golden tests pass on every commit (4 mocks in MVP; masked real slips added later) |
| NFR-06 | Security – transport | Credentials only over TLS in any non-local environment. | HTTPS terminated at reverse proxy or Uvicorn; HTTP disabled outside dev |
| NFR-07 | Security – credentials | Secrets hashed with Argon2; never logged, never returned after creation; constant-time comparison. | Code review + test that searches logs/audit for the secret |
| NFR-08 | Security – data handling | No slip data leaves the host; no LLM or third-party calls. | Egress blocked in prod; dependency review |
| NFR-09 | Security – input | Uploads validated for size, pages, MIME/magic bytes; parser runs with limits to avoid PDF bombs. | Fuzz / malformed-PDF tests return 4xx or `FAILED`, never crash the worker |
| NFR-10 | Security – assurance | Pass VAPT before go-live. | No open Critical/High findings |
| NFR-11 | Auditability | Every state change and auth failure is audited, tamper-evident and attributable. | Hash chain verifies; UPDATE/DELETE blocked; audit written in same transaction as the action |
| NFR-12 | Data retention | Audit rows retained for `BONDS_AUDIT_RETENTION_YEARS` (default 8). Slip PDFs and results retained per bank policy (assumption A-10). | Archive job keeps the chain verifiable across archived segments |
| NFR-13 | Data protection | Uploaded PDFs, DB and backups hold confidential trade data. | Storage access restricted to the service account; encryption at rest in prod (disk/DB level); real slips never committed (`samples/real/` git-ignored) |
| NFR-14 | Availability | Business-hours service for treasury operations. | 99.5 % during 08:00–20:00 IST on business days; RPO ≤ 24 h, RTO ≤ 4 h (Later, with backups) |
| NFR-15 | Observability | Structured logs with request id; health endpoint; metrics. | MVP: logs at `BONDS_LOG_LEVEL` with `X-Request-ID`; Later: structlog, Prometheus metrics (latency, status counts, template hit rate), Sentry |
| NFR-16 | Maintainability | Layered code (api / services / engine / export / db), typed (Pydantic v2), tested. | Engine unit coverage ≥ 80 %; new layout onboarded with no code change (template only) |
| NFR-17 | Extensibility | New markets added as profiles; new labels as synonyms. | Adding a market profile touches `engine/profiles/` and tests only |
| NFR-18 | Portability | Runs anywhere Python 3.11+ runs. | Windows and Linux dev; Docker image for prod (Later); SQLite ↔ PostgreSQL via `BONDS_DATABASE_URL` only |
| NFR-19 | Usability of API | Self-describing API for the frontend and integrators. | Every endpoint in Swagger `/docs` with tags, summaries, request/response examples and error shapes; `BONDS_DOCS_ENABLED=false` hides it in prod |
| NFR-20 | Correctness of numbers | Money and rates never lose precision. | `Decimal` end to end; strings in JSON/XML; Excel cells written as text or exact decimals |
| NFR-21 | Configuration | All tunables via `BONDS_*` env vars (B§9), documented in `.env.example`. | No hard-coded thresholds or paths |

---

## 6. Assumptions

| ID | Assumption |
|---|---|
| A-01 | Slips arrive as PDF with a text layer. Scanned slips are a minority until OCR is added. |
| A-02 | India (`IN`) is the only market that must be fully parsed at go-live. |
| A-03 | The frontend project handles user login and passes a person name in `X-Actor-Name`; the API trusts the client credential, not the name. |
| A-04 | One client credential per integrating system or frontend deployment; no per-user credentials. |
| A-05 | One PDF contains one deal in the MVP. Multi-deal PDFs are detected where possible and sent to review. |
| A-06 | Indian slips are day-first (`DD-MM-YYYY`). A date that is valid in both orders and has no other signal is treated day-first for `IN` and flagged if it breaks a date validation. |
| A-07 | Values printed on the slip are the source of truth; the system cross-checks but does not re-price. |
| A-08 | Excel, email-body and SWIFT inputs are not needed for go-live. |
| A-09 | A holiday calendar for Indian settlement (RBI/CCIL) is supplied as a data file and maintained by operations. |
| A-10 | Slip PDFs and results are retained for the same period as audit (8 years) unless the bank's records policy says otherwise. |
| A-11 | Masked real slips from each main source (NDS-OM, CCIL, E-Kuber, NSE/BSE RFQ, EBP, top brokers) will be available for onboarding and UAT. |
| A-12 | Production runs behind the bank's reverse proxy which terminates TLS. |

## 7. Constraints

| ID | Constraint |
|---|---|
| C-01 | Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2.x, pdfplumber, openpyxl, argon2-cffi (B§2, B§10). |
| C-02 | No LLM and no external service receives slip content (B§2 D6). |
| C-03 | Money and rates are `Decimal`; outputs use strings for them and ISO dates (B§2 D10, D11). |
| C-04 | Auth is HTTP Basic with client credentials; no RBAC, no SSO. |
| C-05 | Audit table is append-only with a SHA-256 hash chain, written in the same transaction. |
| C-06 | Business endpoints are under `/api/v1`; errors are RFC 7807. |
| C-07 | MVP is time-boxed to 2 hours with SQLite; Postgres and Alembic come after MVP. |
| C-08 | Real slips are never committed to git. |

## 8. Dependencies

| ID | Dependency | Impact if missing |
|---|---|---|
| DEP-01 | Masked real slips per source | Templates and accuracy targets cannot be proven |
| DEP-02 | Back-office SMEs for mapping and UAT | Onboarding and sign-off stall |
| DEP-03 | Frontend project (separate) | Ops cannot use preview/approve without calling the API directly |
| DEP-04 | Indian settlement holiday calendar | Holiday validation disabled or wrong |
| DEP-05 | PostgreSQL instance, file storage, reverse proxy with TLS | No production deployment |
| DEP-06 | Tesseract / ocrmypdf and pikepdf packages on the host | No OCR or password-PDF support |
| DEP-07 | Security team for VAPT | Go-live blocked |
| DEP-08 | Downstream systems' import format decisions (JSON/XML/Excel, XSD) | Integration rework |

## 9. Risks

This table is the project's risk register; `docs/12_project_plan.md` refers to it.

| ID | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R-01 | **Password-protected PDFs** (common in emailed counterparty confirmations) cannot be opened. | High | Medium | MVP: detect encryption and return a clear 422 problem. Later: pikepdf unlock with a password supplied per request or per source (FR-30). |
| R-02 | **Multi-deal PDFs** (CCIL obligation reports, broker day summaries) are merged into one wrong deal. | Medium | High | Detect repeated grid rows / multiple ISINs or deal ids → `NEEDS_REVIEW` with a flag; split into many deals after MVP (FR-31). |
| R-03 | **Scanned slips** have no text layer. | Medium | Medium | `UNREADABLE` status in MVP; OCR with Tesseract later; OCR output always capped below the confidence threshold until a template confirms it. |
| R-04 | **Other-country slips** (US/GB/DE/JP/INTL) are normalised with Indian rules (lakh/crore, day-first). | Low–Medium | High | Market detection runs **before** normalisation; non-`IN` or uncertain market → `NEEDS_REVIEW`; market profiles added later. |
| R-05 | **Date-order ambiguity** (`05/06/2026` = 5 Jun or 6 May). | Medium | High | Market profile decides order; cross-check with date validations (settlement ≥ trade, maturity > settlement, no weekend); template stores the layout's date format; ambiguous date lowers confidence → review. |
| R-06 | Wrong template matches a similar layout from another source. | Medium | High | Jaccard threshold 0.80 plus template `keywords`; validation still runs on template output; required-field confidence gate. |
| R-07 | Layout drift at a source (new label, moved column) silently breaks a template. | Medium | Medium | Missing required field → `NEEDS_REVIEW`; re-approve creates a new template version; monitor per-template `PARSED` rate. |
| R-08 | Rounding / precision errors in amounts. | Low | High | `Decimal` end to end; tolerance ±0.01 in checks; golden tests. |
| R-09 | Credential leakage (Basic auth secrets in logs, config or over HTTP). | Low | High | TLS only; secrets hashed; log scrubbing; rotation via CLI; brute-force limiter. |
| R-10 | Audit table tampered with directly in the DB. | Low | High | Triggers block UPDATE/DELETE; hash chain verify endpoint; restricted DB roles in Postgres; periodic verify job. |
| R-11 | Too few real slips to reach the 95 % target. | Medium | Medium | Mock generator for synthetic variants; start UAT with top-volume sources; track coverage per source. |
| R-12 | SQLite concurrency limits under load. | Medium | Low (MVP) | SQLite only for MVP/dev; PostgreSQL in production. |
| R-13 | Real slip data committed to git by mistake. | Low | High | `samples/real/` git-ignored; pre-commit check; masking guide in `samples/real/README.md`. |

---

## 10. Glossary

| Term | Meaning |
|---|---|
| **G-Sec** | Government of India dated security (e.g. `7.10% GS 2034`); ISIN starts `IN00`. Instrument type `GSEC`. |
| **SDL** | State Development Loan – bond issued by an Indian state government (e.g. `7.25% MH SDL 2033`). Type `SDL`. |
| **T-Bill** | Treasury Bill – short-term discount GoI security (91/182/364 days), e.g. `182 DTB 25032027`. Type `TBILL`. |
| **CMB** | Cash Management Bill – very short-term GoI discount bill. Type `CMB`. |
| **NCD** | Non-Convertible Debenture – corporate bond; ISIN starts `INE`. Type `CORPORATE_BOND` (or `PSU_BOND` for public-sector issuers). |
| **CP / CD** | Commercial Paper (issued by corporates) / Certificate of Deposit (issued by banks); money-market discount instruments, day count ACT/365. |
| **Outright** | Plain purchase or sale of a security (`deal_type=OUTRIGHT`, `buy_sell=BUY/SELL`). |
| **Primary auction** | RBI auction of G-Sec/SDL/T-Bill via E-Kuber; bids are competitive or non-competitive. |
| **Repo / reverse repo** | Sale (repo) or purchase (reverse repo) of a security with an agreement to reverse it later; economically a secured loan. Reverse repo = BUY on leg 1. |
| **Repo legs** | Leg 1 (first/near leg: start date, price, amount) and leg 2 (second/far leg: reversal date, price, amount). `repo_interest = leg2_amount − leg1_amount`. |
| **Haircut** | Margin deducted from collateral value in a repo. |
| **TREPS** | Tri-party repo on CCIL against a basket of securities (no single collateral ISIN). |
| **LAF / SDF / MSF** | RBI Liquidity Adjustment Facility operations: repo/reverse repo, Standing Deposit Facility, Marginal Standing Facility. |
| **DVP** | Delivery versus Payment. DVP-I: trade-by-trade gross (corporate bonds via NCL/ICCL); DVP-III: net of securities and funds (G-Sec via CCIL). |
| **NDS-OM** | RBI's Negotiated Dealing System – Order Matching, the screen platform for secondary G-Sec trading. |
| **CCIL** | Clearing Corporation of India Ltd – central counterparty for G-Sec, repo (CROMS) and TREPS; trades are "novated" to CCIL. |
| **CROMS** | CCIL's Collateralised Repo Order Matching System for market repo. |
| **E-Kuber** | RBI's core banking platform, used for primary auctions of government securities. |
| **NSE / BSE RFQ; NCL / ICCL** | Exchange request-for-quote platforms for corporate bonds; their clearing corporations. |
| **EBP** | Electronic Book Provider platform for primary private placement of corporate bonds. |
| **SGL / CSGL** | Subsidiary General Ledger / Constituent SGL – RBI securities accounts in which G-Secs are held. |
| **NSDL / CDSL** | Indian depositories holding corporate bonds, CP and CD in demat form. |
| **ISIN** | International Securities Identification Number (ISO 6166): 12 characters, 2-letter country prefix, check digit. |
| **CUSIP / SEDOL / Common Code** | US / UK / Euroclear-Clearstream security identifiers; used as market-detection signals. |
| **BIC** | Bank Identifier Code (SWIFT); its country letters are a market-detection signal. |
| **Face value (FV)** | Nominal amount of the holding; `face_value_per_unit × quantity` for bonds quoted per unit. |
| **Lakh / crore** | Indian units: 1 lakh = 1,00,000 (10^5); 1 crore = 1,00,00,000 (10^7). |
| **Coupon** | Annual interest rate on face value; paid semi-annually for G-Sec/SDL, often annually for NCDs. |
| **Day count** | Convention for counting days and year length in interest calculations: `30/360`, `ACT/ACT`, `ACT/365`, `ACT/364`, `ACT/360`. |
| **Accrued interest** | Coupon earned since the last coupon date up to settlement, paid by the buyer to the seller. |
| **Clean price / dirty price** | Clean = price per 100 excluding accrued interest (the quoted price). Dirty = clean + accrued interest. |
| **Principal amount** | `face_value × clean price / 100`. |
| **Consideration** | Net settlement amount: principal + accrued interest for outrights; amount payable for discount instruments. |
| **Yield / YTM** | Yield to maturity – the discount rate that equates cash flows to the dirty price. |
| **Discount instrument** | Issued below face value with no coupon (T-Bill, CMB, CP, CD); `discount_amount = face_value − consideration`. |
| **Tenor** | Days from issue/settlement to maturity (`tenor_days`). |
| **T+0 / T+1** | Settlement on trade day / next business day. |
| **HTM / AFS / HFT** | Held to Maturity / Available for Sale / Held for Trading – investment categories (`portfolio`). |
| **Deal slip** | Document recording a trade's terms, produced by a dealer, platform, broker or counterparty. |
| **Canonical deal** | The single output schema every slip maps to (B§5). |
| **Key/value (KV)** | A label and its value extracted from the PDF, with `source`, `page`, `key_bbox`, `value_bbox`. |
| **bbox** | Bounding box `[x0, top, x1, bottom]` in PDF points on a page. |
| **Template** | Saved, versioned mapping for one slip layout: fingerprint, label_map, region_rules, regex_rules, constants, ignore, accepted_derived (B§6). |
| **Fingerprint** | The set of normalised labels (plus keywords) that identifies a layout; compared by Jaccard similarity. |
| **Jaccard score** | `|A ∩ B| / |A ∪ B|` between two label sets; match threshold default 0.80. |
| **Confidence** | Per-field score from the mapping method (B§4); required fields need ≥ 0.90 for `PARSED`. |
| **Market profile** | Rules for one market's number format, date order, enums and calendar (`IN` in MVP). |
| **Hash chain** | Each audit row stores `prev_hash` and `row_hash = SHA-256(prev_hash + row content)`, so any change breaks the chain. |
| **Slip status** | `PARSED`, `NEEDS_REVIEW`, `NEW_TEMPLATE`, `APPROVED`, `UNREADABLE`, `FAILED` (B§3). |
| **Client** | An API consumer identified by `client_id` / `client_secret` (HTTP Basic). |
| **VAPT** | Vulnerability Assessment and Penetration Testing. |
| **UAT** | User Acceptance Testing by back-office operations. |
| **Parallel run** | Period where slips are both keyed manually and parsed, and results compared, before switching over. |

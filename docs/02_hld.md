# 02 · High-Level Design

> **Update 2026-10-02 — stateless design ([ADR-0009](adr/0009-stateless-api-no-slip-storage.md)).**
> Deal slips are never stored and there is no database. Wherever this document describes a `slips`
> table, `/api/v1/slips/*` endpoints, SQLite / PostgreSQL, uploads storage or slip statuses such as
> `APPROVED`, [`00_design_baseline.md`](00_design_baseline.md) is authoritative: templates, clients and
> the audit log are files, and preview / approve take the PDF + mapping in one request.

> Source of truth: [`00_design_baseline.md`](00_design_baseline.md). Names, statuses, thresholds,
> endpoints, tables and env vars below are taken from it verbatim. Anything the baseline does not
> cover is marked **Assumption**. Decisions are recorded in [`adr/`](adr/README.md).

---

## 1. Goals and non-goals

### Goals

| # | Goal |
|---|---|
| G1 | Turn a bond deal slip PDF into one **canonical deal** (`deal_slip_standard_formats.md` §3) and return it as JSON, XML or Excel. |
| G2 | Give a **direct answer** (`PARSED`) only when the result is trustworthy; otherwise say so (`NEEDS_REVIEW`, `NEW_TEMPLATE`, `UNREADABLE`, `FAILED`). |
| G3 | Onboard an unseen layout **once**: return all key/values with page positions, accept a mapping, save it as a versioned template, parse later slips automatically. |
| G4 | Detect the **market** (country) before normalising numbers and dates. MVP fully parses India (`IN`). |
| G5 | Authenticate every business call with client credentials in the HTTP header. |
| G6 | Record every action in a **tamper-evident**, append-only audit trail. |
| G7 | Expose a documented REST API (Swagger UI at `/docs`) that a separate frontend and scripts can use. |

### Non-goals

- The frontend (separate project).
- RBAC / user roles, SSO, per-user accounts (clients are systems, `X-Actor-Name` is informational only).
- LLMs or any external call carrying slip data.
- OCR, password-protected PDFs, multi-deal PDFs, non-India market profiles — **after MVP**.
- Booking deals into a treasury / settlement system (a future downstream consumer).

---

## 2. Architectural drivers

| Driver | What it means here | Main design responses |
|---|---|---|
| **Accuracy** | A wrong amount is worse than no amount. | Decimal strings (D10), per-field confidence, validation rules, `PARSED` only when every gate passes, fail-safe to `NEEDS_REVIEW`. |
| **Auditability** | Regulators / internal audit must see who did what, when, and prove it wasn't altered. | Append-only `audit_log`, SHA-256 hash chain, same-transaction writes, `/api/v1/audit/verify`. |
| **Extensibility** | New brokers/platforms/countries appear constantly. | Layouts = templates (data), markets = profiles, synonym map; no code change per new layout. |
| **Security** | Slips contain counterparty and position data. | HTTP Basic over HTTPS, Argon2, brute-force limiter, no external calls, restricted storage. |
| **Simplicity** | MVP in 2 hours; small team. | One process, SQLite, local files, pure-Python engine, no queue, no RBAC. |

---

## 3. System context

```
                  +---------------------------+      +---------------------------+
                  |  Frontend project         |      |  Integrating systems /    |
                  |  (separate repo; review   |      |  scripts (batch upload,   |
                  |   queue, mapping UI)      |      |  one-shot /parse, export) |
                  +-------------+-------------+      +-------------+-------------+
                                |  HTTPS + Authorization: Basic    |
                                +----------------+-----------------+
                                                 v
                         +-----------------------------------------------+
                         |        Bonds Deal Slip Parser (this API)      |
                         |  FastAPI, /api/v1, Swagger /docs, ReDoc /redoc |
                         +------+-----------------+-----------------+----+
                                |                 |                 :
                     SQL (SQLAlchemy)     file read/write           : (future)
                                v                 v                 v
                   +----------------+   +------------------+   +------------------+
                   | Database       |   | File storage     |   | OCR (Tesseract,  |
                   | SQLite (MVP) / |   | data/uploads/    |   |  local, no net)  |
                   | PostgreSQL     |   | (S3/NAS later)   |   +------------------+
                   +----------------+   +------------------+
                                                 :
                                                 : (future) pull JSON/XML/Excel via
                                                 v  /api/v1/exports/deals
                                   +-----------------------------+
                                   | Downstream treasury system  |
                                   | (booking / settlement)      |
                                   +-----------------------------+
```

- The API is the only component that touches the DB and file storage.
- Nothing leaves the trust boundary: no LLM, no SaaS OCR, no telemetry with slip content (D6).
- The downstream treasury system is out of scope; it would consume exports as another API client.

---

## 4. Container / component view

```
+------------------------------------------------------------------------------------------+
| API process (Uvicorn / FastAPI)                                                          |
|                                                                                          |
|  Middleware: request id (X-Request-ID) · CORS · RFC 7807 error handlers                  |
|  Auth (app/auth.py): HTTP Basic -> clients table -> Argon2 verify -> limiter             |
|                                                                                          |
|  +------------------- API layer (app/api/, Pydantic app/schemas/) --------------------+  |
|  | health | parse | slips | templates | schema | exports | audit        deps.py       |  |
|  +---------------------------------------+--------------------------------------------+  |
|                                          v                                               |
|  +------------------- Service layer (app/services/) ----------------------------------+  |
|  | slip_service  template_service  export_service  audit_service                      |  |
|  | (transactions, status transitions, orchestration, audit writes)                    |  |
|  +-----------+----------------------------+----------------------------+--------------+  |
|              v                            v                            v                 |
|  +---------------------------+  +--------------------+  +------------------------------+ |
|  | Parser engine (app/engine)|  | Exporters          |  | Persistence (app/db/)        | |
|  | PURE: no FastAPI, no DB   |  | (app/export/)      |  | SQLAlchemy models, session   | |
|  | extract, market, profiles,|  | json_export        |  | clients, slips, templates,   | |
|  | detect, mapping, normalize|  | xml_export         |  | template_versions, audit_log | |
|  | derive, validate, pipeline|  | excel_export       |  +--------------+---------------+ |
|  +---------------------------+  +--------------------+                 |                 |
+------------------------------------------------------------------------+-----------------+
                                                                         v
                                        DB (SQLite / PostgreSQL)   +   data/uploads/<slip_id>.pdf
```

| Component | Responsibility | Boundary / must not |
|---|---|---|
| **API layer** (`app/api/`, `app/schemas/`) | HTTP concerns: routing, request/response models, query params, content negotiation (`format=`), status codes, OpenAPI metadata. | No business rules, no SQL, no parsing logic. |
| **Auth** (`app/auth.py`) | Decode `Authorization: Basic`, look up `clients`, Argon2 verify, update `last_used_at`, brute-force limiter (`BONDS_AUTH_*`), emit `AUTH_FAILED` / `AUTH_BLOCKED`. | No authorisation beyond "active client" (no RBAC). |
| **Service layer** (`app/services/`) | Use cases: upload, preview, approve, reparse, delete, template versioning, exports. Owns the DB transaction and writes the audit row inside it. Enforces the status state machine. | No HTTP objects (Request/Response) passed in; no PDF parsing itself. |
| **Parser engine** (`app/engine/`) | Pipeline steps 2–10: bytes + candidate templates + thresholds → result (deal, fields with method/confidence/bbox, key_values, validation, status). Market profiles in `engine/profiles/`. | Pure functions: no FastAPI import, no DB session, no file writes, no network. Templates are passed in as plain data. |
| **Exporters** (`app/export/`) | Canonical deal(s) → JSON / XML (ElementTree) / Excel (openpyxl). Decimal as strings. | No lookups; serialise what they are given. |
| **Persistence** (`app/db/`) | Engine/session factory, ORM models, (later) Alembic migrations, audit triggers. | No business decisions. |
| **Audit** (`audit_service` + `audit_log` triggers) | Build the event, compute `row_hash = SHA-256(prev_hash + canonical row)`, insert; verify chain; search/export. | Never UPDATE/DELETE (DB triggers block it). |
| **CLI** (`app/cli.py`) | `init-db`, `add-client`, `list-clients`, `disable-client`, `rotate-secret`. Audited with `actor_type=cli`. | Not exposed over HTTP. |

---

## 5. Main data flows

### (a) Upload → `PARSED` (direct answer)

1. Client `POST /api/v1/slips` (multipart PDF) with `Authorization: Basic …`, optional `X-Actor-Name`, `X-Request-ID`.
2. Auth verifies the client; request id is generated if absent.
3. **Ingest**: size ≤ `BONDS_MAX_UPLOAD_MB` (else 413), pages ≤ `BONDS_MAX_PAGES` (else 422), compute SHA-256, duplicate check against `slips.sha256`, write `data/uploads/<slip_id>.pdf`.
4. **Extract**: pdfplumber → KeyValue candidates `{key, value, source, page, key_bbox, value_bbox}` from table, grid, text and prose sources.
5. **Market**: signal vote → `market=IN`, `issuer_country`, `slip_locale`, `market_confidence`.
6. **Detect**: fingerprint vs active templates (Jaccard); best score ≥ 0.80 → template matched.
7. **Map → Rules → Normalise (IN profile) → Derive → Validate**.
8. **Decide**: template matched, required fields present, validation passed, every required field confidence ≥ 0.90, market profile available → `PARSED`.
9. Service stores `slips` row (`result_json`, `template_id`, `template_version`, `match_score`), increments `templates.match_count`, writes `SLIP_UPLOADED` + `SLIP_PARSED` audit rows — all in one transaction.
10. Response: full result (deal, fields with confidence + bbox, key_values, validation). Client may call `GET /api/v1/slips/{id}/export?format=xlsx` (`SLIP_EXPORTED`).

### (b) New layout → `NEW_TEMPLATE` → preview → approve → template v1 → next slip `PARSED`

1. Upload as in (a) steps 1–5.
2. **Detect**: no template scores ≥ 0.80 → generic fallback only (synonym → abbreviation → fuzzy → text).
3. **Decide** → `NEW_TEMPLATE`. Response contains the best-effort deal **plus all key/values with page and bbox**.
4. Frontend shows the PDF (`GET /api/v1/slips/{id}/pdf`) with highlighted boxes; user maps labels, draws region rules, adds constants.
5. Frontend `POST /api/v1/slips/{id}/preview` with the mapping payload → engine re-runs with the draft as a temporary template; nothing saved except a `SLIP_PREVIEWED` audit row. Repeat until correct.
6. `POST /api/v1/slips/{id}/approve` with the mapping (`template_id: null`, `template_name`, `keywords`, `note`). If validation still fails, approval requires `accept_validation_issues: true`.
7. Service, in one transaction: creates `templates` row (slug id, `current_version=1`), `template_versions` v1 with `definition_json` (fingerprint = normalised labels + keywords, `source_slip_id`), sets slip `APPROVED` with `approved_mapping_json`, `approved_by`, `approved_actor_name`, `approved_at`; audit `TEMPLATE_CREATED` + `SLIP_APPROVED`.
8. Next slip in the same layout: Detect scores ≥ 0.80 against the new template → labels resolve by `template` method (confidence 1.00) → `PARSED`.

### (c) Template drift → `NEEDS_REVIEW` → v2

1. A counterparty changes its slip (renamed label, moved field). Fingerprint still scores ≥ 0.80, so the template matches.
2. A required field is now missing / low-confidence (e.g. mapped only by `fuzzy`) or validation fails → **Decide** → `NEEDS_REVIEW`. Response lists which fields and checks failed, plus `_unmapped` labels.
3. Reviewer previews a corrected mapping (`POST …/preview`, `SLIP_PREVIEWED`).
4. `POST …/approve` with `template_id` = the matched template → service adds `template_versions` **v2** (v1 is kept untouched), sets `templates.current_version=2`, slip `APPROVED`; audit `TEMPLATE_VERSION_ADDED` + `SLIP_APPROVED`.
5. Other `NEEDS_REVIEW` slips from the same layout can be re-run with `POST …/reparse` (`SLIP_REPARSED`) and move to `PARSED`. Each slip keeps the `template_version` it was parsed with.

### (d) One-shot `/parse`

1. Client `POST /api/v1/parse?format=json|xml|xlsx` with a PDF.
2. Auth, size/page limits, then the full engine pipeline against the active templates.
3. Result is returned directly in the requested format with the computed status.
4. **Nothing is stored**: no `slips` row, no PDF on disk. **Assumption:** an audit row (`SLIP_PARSED`, `entity_id` null, SHA-256 and file name in `details`) is still written so usage is traceable without keeping content.

---

## 6. Template strategy (high level)

- **Fingerprint** = set of normalised labels (+ optional keywords). Match = Jaccard(slip labels, template labels); best active template ≥ its `match_threshold` (default `BONDS_TEMPLATE_MATCH_THRESHOLD` = 0.80) wins.
- **Mapping precedence**: template `label_map` → built-in synonyms → abbreviation expansion → fuzzy; then `region_rules` (bbox), `regex_rules`, `constants`; `ignore` drops noise labels.
- **Confidence** follows the method table in the baseline (`template`/`region`/`constant` 1.00 … `text` 0.80); `accepted_derived` lifts reviewer-accepted fields to 0.95 for that template.
- **Versioning**: `templates` holds identity and `current_version`; `template_versions` holds immutable definitions, UNIQUE(`template_id`, `version`). New version = new row; never overwritten. Disable via `PATCH` (after MVP). JSON import/export for promotion between environments.
- **Generic fallback** means a slip is never "empty": even `NEW_TEMPLATE` returns a best-effort deal.

## 7. Market detection (high level)

- Runs **before** Detect/Normalise because number and date formats depend on it (`1,00,000` vs `100,000`; `03/04` day-first vs month-first).
- Signals vote: ISIN prefix, CUSIP/SEDOL presence, currency, settlement system (CCIL, DTC, CREST…), platform, name style (`GS 2034`, `SDL`), number grouping, BIC country → `market`, `issuer_country`, `slip_locale`, `market_confidence`.
- A **market profile** (`engine/profiles/`) supplies amount units (lakh/crore), date order, rate conventions, enums, holiday calendar. Adding a market = adding a profile.
- MVP: `IN` profile only. Other markets are detected and the slip goes to `NEEDS_REVIEW` ("market profile not available / market uncertain"). `GET /api/v1/schema/markets` lists available profiles.

---

## 8. Data architecture

```
clients 1 ──< slips (created_by, approved_by)            audit_log (append-only, hash-chained)
                │                                         references any entity by
                │ template_id, template_version           entity_type + entity_id (no FK)
                v
templates 1 ──< template_versions (source_slip_id ──> slips)
```

| Entity | Holds | Notes |
|---|---|---|
| `clients` | API credentials (`client_id`, Argon2 `secret_hash`), `is_active`, usage timestamps | Managed by CLI only. |
| `slips` | File metadata, `sha256`, `status`, `market`, template used + version + `match_score`, `result_json`, `approved_mapping_json`, `duplicate_of`, approval fields | One row per uploaded PDF. |
| `templates` | Identity (slug), name, `market`, `is_active`, `current_version`, match stats | Mutable header only. |
| `template_versions` | Immutable `definition_json` per version, `source_slip_id`, `note` | Never updated. |
| `audit_log` | Every action with actor, before/after status, changes, request id, IP, UA, outcome, `prev_hash`, `row_hash` | UPDATE/DELETE blocked by triggers. |

**What is stored where**

| Data | Location |
|---|---|
| Original PDFs | `data/uploads/<slip_id>.pdf` (`BONDS_DATA_DIR`); S3/NAS later |
| Structured data, results, templates, audit | DB (`BONDS_DATABASE_URL`; `data/bonds.db` in MVP) |
| Exports | Generated on request, streamed, not persisted |
| Secrets | Only Argon2 hashes in DB; plaintext shown once by CLI |

**Retention**

- Audit: `BONDS_AUDIT_RETENTION_YEARS` = 8, enforced by an archive job (after MVP). Rows are archived (exported + verified), never edited in place.
- `DELETE /api/v1/slips/{id}` removes the slip row and PDF; its audit rows are kept (`SLIP_DELETED`).
- **Assumption:** slip/PDF retention period is a bank policy input; no automatic purge in MVP.

---

## 9. Security architecture (summary)

| Control | Implementation |
|---|---|
| Transport | HTTPS terminated at reverse proxy in production (after MVP); Basic credentials are only safe over TLS. |
| Authentication | `Authorization: Basic base64(client_id:client_secret)` on every endpoint except `/health`. |
| Secret storage | Argon2 (`argon2-cffi`) hashes; rotation via CLI (`CLIENT_SECRET_ROTATED`). |
| Brute force | Per-IP failure counter: `BONDS_AUTH_MAX_FAILURES` in `BONDS_AUTH_WINDOW_SECONDS` → blocked `BONDS_AUTH_BLOCK_SECONDS` (429, `AUTH_BLOCKED`). |
| Input limits | `BONDS_MAX_UPLOAD_MB` (413), `BONDS_MAX_PAGES` (422), PDF-only content check. |
| Data egress | No external calls with slip data; no LLM. |
| Surface | `BONDS_DOCS_ENABLED=false` hides Swagger in production; CORS restricted to `BONDS_CORS_ORIGINS`. |
| Integrity | Hash-chained audit, DB triggers, `/api/v1/audit/verify`. |
| Data at rest | **Assumption:** disk/volume encryption for `data/` and DB (OS / cloud provider); DB and object store not publicly reachable. |

---

## 10. Deployment view

### MVP

```
  Developer / test client
          | HTTP (localhost)
          v
  +-----------------------------------+
  | uvicorn app.main:app (1 process)  |
  |   ./data/bonds.db   (SQLite)      |
  |   ./data/uploads/*.pdf            |
  +-----------------------------------+
```

### Production (after MVP)

```
   Frontend / integrating systems
                 | HTTPS
                 v
      +-----------------------+
      | Reverse proxy / LB    |  TLS termination, request size limit
      +-----------+-----------+
          +-------+-------+-----------------+
          v               v                 v
   +-----------+   +-----------+     +-----------+
   | API #1    |   | API #2    | ... | API #N    |   stateless containers
   +-----+-----+   +-----+-----+     +-----+-----+   (same image, env config)
         +---------------+-----------------+
         |                                 |
         v                                 v
 +------------------+           +------------------------+
 | PostgreSQL       |           | Shared object storage  |
 | (primary + HA)   |           | (S3 / NAS) for PDFs    |
 +--------+---------+           +-----------+------------+
          ^                                 ^
          |      +--------------------+     |
          +------| Background worker  |-----+    OCR, batch, audit archive (later)
                 +--------------------+
```

- API containers hold no local state; everything shared is in PostgreSQL or object storage.
- Limiter state: **Assumption:** in-process in MVP; moved to the DB or a shared cache when N > 1 so blocks apply across instances.
- Migrations: Alembic run as a release step before new containers start.

---

## 11. Quality attributes

### Scalability & performance
- Parsing is CPU-bound and per-request independent → scale horizontally by adding API containers.
- Typical slip is 1–3 pages; pdfplumber extraction dominates. Limits (`BONDS_MAX_UPLOAD_MB`, `BONDS_MAX_PAGES`) bound worst case.
- Template detection is O(active templates) set comparisons — cheap for hundreds of templates; **Assumption:** cache active template definitions per process, invalidated on version change.
- Batch and OCR move to the background worker so long jobs don't hold HTTP requests.
- Indexes on `slips.sha256`, `slips.status` support duplicate check and review queue.

### Availability & resilience
- Stateless APIs + PostgreSQL HA → no single API instance is critical.
- Any exception during processing → slip `FAILED`, audit row with `outcome=FAILURE` and `error`; the PDF is retained so `reparse` can retry.
- Audit and business change share one transaction: either both commit or neither.
- `/health` checks DB connectivity for load-balancer probes.
- Backups: DB + object storage (after MVP); audit chain verified after restore.

### Observability
- **Request id**: `X-Request-ID` accepted or generated, echoed in responses, stored in `audit_log.request_id`, included in every log line.
- **Logs**: structured (structlog after MVP), level `BONDS_LOG_LEVEL`; never log secrets or full slip content.
- **Metrics** (prometheus-client, after MVP): requests/latency by route, slips by status, template match rate, parse duration, auth failures.
- **Errors**: sentry-sdk after MVP.
- **Business observability**: review queue via `GET /api/v1/slips?status=NEEDS_REVIEW`; audit search.

### Error handling
- All errors are RFC 7807 `application/problem+json` (`type`, `title`, `status`, `detail`, `instance`) plus `request_id`.
- Typical codes: 400 bad input, 401 missing/invalid credentials, 404 unknown slip/template, 409 invalid state transition (e.g. reparse `APPROVED` without `force`), 413 too large, 415 not a PDF, 422 too many pages / validation of payload, 429 blocked, 500 internal. **Assumption:** 400/404/409/415 mapping; 401/413/422/429 as in the baseline.
- Parse problems are **not** HTTP errors: an unreadable or ambiguous slip is a successful request with status `UNREADABLE` / `NEEDS_REVIEW` / `NEW_TEMPLATE`.

---

## 12. System design principles applied

| Principle | How the design applies it |
|---|---|
| **Separation of concerns / layered architecture** | Four layers — API (HTTP), service (use cases, transactions, audit), engine (parsing), persistence/exporters — each in its own package (`app/api`, `app/services`, `app/engine`, `app/db`, `app/export`). Dependencies point downward only. |
| **Single Responsibility** | Each engine module does one pipeline step (`extract`, `market`, `detect`, `mapping`, `normalize`, `derive`, `validate`); each exporter one format; each service one aggregate (slip, template, export, audit). |
| **Open/Closed** | New layout = new template (data via approve/import), new market = new profile in `engine/profiles/`, new label = synonym entry. The pipeline code is not modified for any of them. |
| **Dependency inversion** | The engine depends on plain data (bytes, template definitions, thresholds), not on FastAPI or SQLAlchemy. Services adapt DB rows into engine inputs. The engine can run in a CLI, a worker or a test unchanged. |
| **Stateless services / horizontal scaling** | No session state in API processes; auth is per request; state lives in DB + file/object storage → N identical containers behind a proxy. |
| **Idempotency** | SHA-256 of every upload; re-uploading the same bytes is detected and linked via `slips.duplicate_of` instead of being treated as a new deal. `preview` has no side effects besides an audit row. Re-running `reparse` on the same inputs gives the same result (deterministic engine). |
| **Fail-safe defaults** | `PARSED` requires *all* gates; anything uncertain (low confidence, failed validation, unknown market, no template) falls to `NEEDS_REVIEW`/`NEW_TEMPLATE`. Approving with validation issues requires explicit `accept_validation_issues: true`. Re-parsing `APPROVED` requires `force=true`. Swagger can be turned off. |
| **Defense in depth** | TLS + Basic auth + Argon2 + limiter + input limits + CORS allow-list + no egress + audit hash chain + DB triggers + (assumed) encryption at rest. |
| **Least privilege** | Clients can only call the API — no DB access; client management is CLI-only. **Assumption (production):** the app's DB role has only INSERT/SELECT on `audit_log` (no UPDATE/DELETE/TRUNCATE), separate owner role for migrations. |
| **Immutability & versioning** | `template_versions` rows are never updated; each slip records the `template_version` used. `audit_log` is append-only and chained. Deleting a slip keeps its audit trail. |
| **Single source of truth** | `00_design_baseline.md` for decisions; one canonical deal schema for all layouts and outputs; `GET /api/v1/schema/fields` exposes it to clients. |
| **Configuration over code / 12-factor** | All tunables are `BONDS_*` env vars (thresholds, limits, DB URL, CORS, docs toggle, log level, retention); same build runs in dev and prod; logs to stdout. |
| **Observability by design** | Request id end-to-end, audit of every action (including failures and auth events), per-field method/confidence in results, `/health`, metrics later. |
| **Testability** | Pure engine → unit tests without HTTP/DB; golden tests compare `samples/mock/*.pdf` output with `samples/mock/expected/*.json`; API tests via httpx; audit chain and auth tests. |
| **KISS / YAGNI** | SQLite + local files for MVP, single process, no queue, no RBAC, no LLM, OCR deferred, XSD deferred. Upgrade paths (Postgres, S3, worker) are defined but not built early. |

---

## 13. Technology choices

| Concern | Choice | Rationale | Rejected alternatives |
|---|---|---|---|
| Web framework | FastAPI + Pydantic v2 + Uvicorn | Auto OpenAPI/Swagger, typed models, async file upload, fast to build ([ADR-0001](adr/0001-fastapi-for-api.md)) | Flask (manual OpenAPI), Django REST (heavy), Node/Java (team is Python) |
| PDF extraction | pdfplumber | Words with bbox, table detection, pure Python ([ADR-0002](adr/0002-pdfplumber-for-extraction.md)) | PyMuPDF (AGPL), Camelot/Tabula (tables only, Java/Ghostscript), cloud OCR (data egress) |
| Parsing approach | Templates + generic fallback | Deterministic, explainable, learns per layout ([ADR-0003](adr/0003-template-driven-parsing-with-generic-fallback.md)) | LLM extraction (egress, non-deterministic), ML layout models (training data), hard-coded parsers |
| Persistence | SQLAlchemy 2.x; SQLite → PostgreSQL | Zero-setup MVP, same code in prod ([ADR-0004](adr/0004-sqlalchemy-sqlite-then-postgres.md)) | Postgres from day one (setup cost), NoSQL (weak transactions for audit) |
| Auth | HTTP Basic client credentials, Argon2 | Simple for systems and scripts, Swagger-native ([ADR-0005](adr/0005-http-basic-client-credentials-no-rbac.md)) | OAuth2/JWT (needs IdP), API key header, mTLS (ops cost) |
| Audit | Append-only hash-chained table | Tamper evidence without extra infra ([ADR-0006](adr/0006-append-only-hash-chained-audit-log.md)) | Plain log files, external ledger/WORM service, event store |
| Markets | Detect before normalise | Correct number/date parsing per locale ([ADR-0007](adr/0007-market-detection-before-normalisation.md)) | Fixed India locale, per-template locale only |
| Money | `Decimal`, strings on the wire | No float rounding ([ADR-0008](adr/0008-decimal-strings-for-money.md)) | float, integer minor units |
| XML | ElementTree (XSD after MVP) | Stdlib, sufficient | lxml (extra native dep for MVP) |
| Excel | openpyxl | Pure Python, write xlsx with types | xlsxwriter (write-only, fine but one lib for read/write preferred), pandas (heavy) |
| Password hashing | argon2-cffi | Modern memory-hard KDF | bcrypt, PBKDF2 |
| Mock slips | reportlab | Generate deterministic test PDFs | Hand-made PDFs |

---

## 14. Trade-offs and known limitations

| Trade-off / limitation | Impact | Mitigation |
|---|---|---|
| Template onboarding needs a human once per layout | First slip of each layout is never `PARSED` | Generic fallback returns best-effort deal; mapping UI makes onboarding minutes. |
| Jaccard fingerprint on labels | Two layouts sharing most labels could collide; heavy label changes miss the template | Per-template `match_threshold`, `keywords`, drift flow (c). |
| Text-layer only in MVP | Scanned slips → `UNREADABLE` | OCR in background worker after MVP. |
| One deal per PDF | Multi-deal confirmations (CCIL grids) not split | Multi-deal PDFs after MVP. |
| India profile only | Other markets always `NEEDS_REVIEW` | Add US/GB/INTL profiles. |
| SQLite | Single writer, not for multi-instance | PostgreSQL in production (same SQLAlchemy code). |
| HTTP Basic, no RBAC | Every active client can do everything, incl. approve | Few trusted clients; `X-Actor-Name` + audit for accountability; RBAC can be added later. |
| Hash chain proves tampering, doesn't prevent a DB superuser rewriting the whole chain | Integrity depends on DB admin controls | Periodic export of chain head to external store (**Assumption**, future); least-privilege DB role. |
| Audit writes serialise on the chain head | Throughput ceiling on very high write rates | Acceptable at deal-slip volumes; partitioned chains later if needed. |

## 15. Future evolution

1. PostgreSQL + Alembic, Docker, CI, HTTPS, backups.
2. Batch upload, reparse, delete, template PUT/PATCH/import/history, combined deal export, audit export, limiter.
3. OCR (Tesseract / ocrmypdf) and password-protected PDFs (pikepdf) in a background worker.
4. Market profiles US / GB / DE / JP / INTL; SWIFT MT515/MT518 and email-body inputs.
5. Multi-deal PDFs; XSD for XML output.
6. Metrics, structured logs, Sentry; audit archive job honouring `BONDS_AUDIT_RETENTION_YEARS`.
7. Push integration to a downstream treasury system; optional RBAC if client population grows.

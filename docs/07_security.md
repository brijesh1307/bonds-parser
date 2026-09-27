# 07 · Security Design

Source of truth: [`00_design_baseline.md`](00_design_baseline.md) (D6, D8, D9, §9 env vars, §11 scope).
Anything not stated in the baseline is marked **(assumption)**. Endpoint details:
[`04_api_spec.md`](04_api_spec.md); audit design: [`06_audit_trail.md`](06_audit_trail.md).

**Assets protected:** deal data (counterparties, amounts, ISINs, portfolios, dealer names) — confidential
commercial / market-sensitive information; uploaded PDFs; templates (drive automated booking data);
client secrets; the audit trail (regulatory evidence).

**Trust boundaries:** API clients (frontend backend, integrations) ↔ reverse proxy (TLS) ↔ FastAPI app ↔
DB + file store. CLI runs on the server host with operator access.

---

## 1. Threat model (STRIDE)

| # | STRIDE | Threat | Mitigation | Status |
|---|---|---|---|---|
| S1 | Spoofing | Stolen / guessed client credentials used to call the API | HTTPS only; long random secrets; Argon2 hashes; brute-force limiter (429); `disable-client`, `rotate-secret`; `last_used_at` + `AUTH_FAILED` monitoring | MVP (limiter Later) |
| S2 | Spoofing | A user claims to be someone else via `X-Actor-Name` | Header is informational only; the authenticated `client_id` is always recorded as `actor_id`. Accepted limitation (§8) | Accepted |
| T1 | Tampering | Audit rows edited / deleted to hide an action | Append-only triggers (SQLite) / INSERT+SELECT grants (Postgres); SHA-256 hash chain; `/audit/verify`; external anchoring of last hash | MVP |
| T2 | Tampering | Malicious template (regex/region) corrupts parsed values or causes ReDoS | Templates only via authenticated API; versioned, never overwritten, audited with full definition; regex length cap + timeout; validation rules on every deal | MVP (+ timeout Later) |
| T3 | Tampering | PDF replaced on disk after upload | SHA-256 stored at ingest; re-hash on read/export (assumption); file store not writable by other users | MVP |
| T4 | Tampering | Man-in-the-middle alters requests/responses | TLS 1.2+ at proxy, HSTS | Prod |
| R1 | Repudiation | Client denies approving a mapping / exporting data | Every state-changing action + views/exports audited with `actor_id`, `actor_name`, IP, user agent, request ID, in the same transaction | MVP |
| I1 | Information disclosure | Slip data leaked via logs, errors or third-party services | No external calls / no LLM (D6); logs contain IDs only; RFC 7807 errors without stack traces; `Cache-Control: no-store` | MVP |
| I2 | Information disclosure | Disk / backup theft | Encryption at rest (disk / DB), restricted file permissions, encrypted backups | Prod |
| I3 | Information disclosure | Any client reads all slips (no per-client ownership) | Single-tenant deployment with trusted clients; accepted (see OWASP API1) | Accepted |
| I4 | Information disclosure | Swagger exposes the surface in prod | `BONDS_DOCS_ENABLED=false` in prod or restrict `/docs` at proxy | Prod |
| D1 | Denial of service | Huge / many-page / decompression-bomb PDFs | `BONDS_MAX_UPLOAD_MB=10` (413), `BONDS_MAX_PAGES=20` (422), parser timeout, proxy body-size limit | MVP (timeout Later) |
| D2 | Denial of service | Credential stuffing floods Argon2 CPU | Per-IP limiter → 429 before hashing; proxy rate limit | Later |
| D3 | Denial of service | Large exports / audit queries | `limit` max 200; export row caps; date filters | MVP / Later |
| E1 | Elevation of privilege | Malicious PDF content executes (JavaScript, embedded files, exploits in parser) | pdfplumber reads text only; nothing embedded is executed or extracted to disk; dependency patching (`pip-audit`); app runs as non-root | MVP |
| E2 | Elevation of privilege | Path traversal via uploaded filename | Filenames never used as paths: stored as `data/uploads/<slip_id>.pdf` (UUID) | MVP |
| E3 | Elevation of privilege | SQL injection via filters | SQLAlchemy bound parameters only; enums validated by Pydantic | MVP |

---

## 2. Authentication

### 2.1 Scheme

HTTP Basic (D8): `Authorization: Basic base64(client_id:client_secret)` on every request except
`/health`. There are **no roles**: every active client can call every endpoint (RBAC out of scope).

Failure → `401 application/problem+json` with header:

```http
WWW-Authenticate: Basic realm="bonds-parser", charset="UTF-8"
```

The response body is identical for "unknown client", "wrong secret", "disabled client" and "malformed
header" — no enumeration. The precise reason goes only to the audit row (`AUTH_FAILED`,
`details.reason` ∈ `MISSING_HEADER, MALFORMED, UNKNOWN_CLIENT, BAD_SECRET, CLIENT_DISABLED`; assumption).

### 2.2 Client lifecycle (CLI, `app/cli.py`)

Run on the server host by an operator; each command writes an audit row with `actor_type="cli"`,
`actor_id` = OS user (assumption).

| Command | Effect | Audit |
|---|---|---|
| `python -m app.cli init-db` | Creates tables, audit triggers, genesis state | — (assumption: schema creation precedes the log) |
| `python -m app.cli add-client <client_id> --description "Treasury UI backend"` | Generates secret, stores Argon2 hash, **prints the secret once** — it cannot be shown again | `CLIENT_ADDED` |
| `python -m app.cli list-clients` | `client_id`, description, `is_active`, `created_at`, `last_used_at`, `rotated_at` — never secrets or hashes | — |
| `python -m app.cli disable-client <client_id>` | `is_active=false`; next request gets 401 | `CLIENT_DISABLED` |
| `python -m app.cli rotate-secret <client_id>` | New secret printed once; old secret invalid immediately; `rotated_at` set | `CLIENT_SECRET_ROTATED` |

Secret generation (assumption): `secrets.token_urlsafe(32)` (256 bits). `client_id`: `^[a-z0-9][a-z0-9_-]{2,63}$`.
Rotation policy (assumption): every 90 days and immediately on staff change / suspected leak. For
zero-downtime rotation, add a second client, switch the consumer, disable the old one.

### 2.3 Secret hashing (Argon2)

`argon2-cffi` `PasswordHasher`, **Argon2id**, parameters (argon2-cffi defaults = RFC 9106 low-memory
profile; assumption):

| Parameter | Value |
|---|---|
| `time_cost` | 3 |
| `memory_cost` | 65 536 KiB (64 MiB) |
| `parallelism` | 4 |
| `hash_len` | 32 bytes |
| `salt_len` | 16 bytes (random per hash) |

Stored as the PHC string in `clients.secret_hash`. On successful verify, `check_needs_rehash()` →
re-hash with current parameters. Performance: ~50 ms per verify; a short-lived in-process cache of
successful verifications keyed by `HMAC-SHA256(server_key, client_id:secret)` (TTL ≤ 60 s, cleared on
disable/rotate) may be added if load requires (assumption, not MVP).

### 2.4 Constant-time checks

- Unknown `client_id` → still run `verify()` against a fixed dummy Argon2 hash so response time does
  not reveal whether the client exists.
- Argon2 verification compares digests in constant time; any direct string compare uses
  `hmac.compare_digest`.
- `is_active` is checked **after** the hash verify (same timing for disabled vs wrong secret).

### 2.5 `last_used_at`

Updated on successful auth, at most once per minute per client to avoid a write per request
(assumption). `list-clients` shows it; clients unused for 90 days should be disabled (review item).

### 2.6 Brute-force limiter (Later, baseline §9)

| Variable | Default | Meaning |
|---|---|---|
| `BONDS_AUTH_MAX_FAILURES` | `10` | Failed auths per IP within the window before block |
| `BONDS_AUTH_WINDOW_SECONDS` | `300` | Sliding window for counting failures |
| `BONDS_AUTH_BLOCK_SECONDS` | `900` | Block duration |

- Keyed by client IP (real IP from `X-Forwarded-For` only when the request comes from the configured
  proxy — `uvicorn --proxy-headers --forwarded-allow-ips=<proxy ip>`).
- While blocked: `429` problem+json `code=AUTH_BLOCKED` with `Retry-After`, **before** any Argon2 work.
- One `AUTH_BLOCKED` audit row when the block starts (not per rejected request); each failure before it
  writes `AUTH_FAILED`.
- MVP state is in-process memory; with several workers/instances use a shared store (e.g. Redis) or the
  proxy's rate limiting (assumption).

### 2.7 Where the frontend keeps the secret

A browser SPA cannot keep a secret — anyone can read it from DevTools. Recommended (assumption):
the frontend project has a small backend (BFF) that authenticates its users and calls this API with its
client credentials server-side, adding `X-Actor-Name`. If a pure SPA is used anyway, treat the
credential as shared by all its users, restrict CORS, and rotate on every staff change.

---

## 3. Transport

- **HTTPS mandatory in production.** TLS 1.2+ (prefer 1.3) terminated at the reverse proxy
  (nginx / load balancer); app listens on localhost / private network only. HTTP → 301 to HTTPS
  (HTTPS is a post-MVP item, baseline §11; dev runs HTTP on localhost).
- **HSTS** at the proxy: `Strict-Transport-Security: max-age=31536000; includeSubDomains`.
- Basic auth over plain HTTP is forbidden outside `localhost`.
- **CORS** allow-list from `BONDS_CORS_ORIGINS` (default `http://localhost:3000,http://localhost:5173`);
  never `*` (assumption for the rest of this list):
  - `allow_methods`: `GET, POST, PUT, PATCH, DELETE`
  - `allow_headers`: `Authorization, Content-Type, X-Actor-Name, X-Request-ID`
  - `expose_headers`: `X-Request-ID, Content-Disposition, Location, Retry-After`
  - `allow_credentials`: `false` (no cookies; auth is an explicit header)
  - Prod origins are the real frontend URLs over `https://`.

---

## 4. Input and file security

| Control | Rule | Response |
|---|---|---|
| Type | First bytes must be `%PDF-` (magic bytes); `Content-Type` / extension are not trusted | `415 UNSUPPORTED_MEDIA_TYPE` |
| Size | ≤ `BONDS_MAX_UPLOAD_MB` (10); enforced while streaming, plus proxy `client_max_body_size` | `413 FILE_TOO_LARGE` |
| Pages | ≤ `BONDS_MAX_PAGES` (20) | `422 TOO_MANY_PAGES` |
| Encrypted | Password-protected / encrypted PDFs rejected for now (pikepdf support later) | `422 PDF_ENCRYPTED` |
| Corrupt | Parser cannot open file | `422 PDF_CORRUPT` |
| Parse timeout | Per-slip wall-clock limit, e.g. 30 s, in a worker process that can be killed (assumption) | slip `FAILED`, audit error |
| Embedded content | JavaScript, forms actions, attachments, links are never executed, followed or extracted; only the text layer / word positions are read | — |
| Storage name | PDF saved as `data/uploads/<slip_id>.pdf` (UUID). The client filename is stored only as data (`file_name`, max 255 chars, control chars stripped) and sanitised in `Content-Disposition` | — |
| JSON bodies | Pydantic v2 strict models; unknown canonical fields rejected; body size cap (e.g. 1 MB, assumption) | `422` |
| Regex rules | Max 500 chars; compiled at save; run with a timeout / on bounded text to limit ReDoS (assumption) | `422` |
| bbox | 4 finite numbers, `x0<x1`, `top<bottom`, inside the page | `422` |
| Query params | Enums validated; `limit ≤ 200`; SQL via ORM bound parameters only | `422` |

---

## 5. Data protection

- **No external calls with slip data** (D6): no LLM, no cloud OCR, no telemetry containing slip content.
  Future OCR (Tesseract) runs locally.
- **Encryption at rest:** encrypted volume (BitLocker / LUKS / cloud disk encryption) for `BONDS_DATA_DIR`
  and backups; Postgres on encrypted storage with TLS for DB connections in prod. File permissions:
  data dir readable only by the service account (`0700`).
- **Secrets:** DB URL, proxy certs, any keys come from env vars or a vault (e.g. HashiCorp Vault, Azure
  Key Vault) — never committed. `.env.example` holds placeholders only. Client secrets exist only as
  Argon2 hashes.
- **Logs:** application logs contain request ID, client ID, route, status, duration, slip ID — **never**
  the `Authorization` header, secrets, PDF text or deal values. Audit `changes` hold deal field diffs
  by design (see `06_audit_trail.md` §5.6) and the audit table is protected like deal data.
- **Error responses:** no stack traces, SQL or file paths; `500` returns `request_id` only.
- **Retention and deletion (assumption, confirm with compliance):** slips + PDFs kept as long as the
  institution's trade-record retention (default aligned with `BONDS_AUDIT_RETENTION_YEARS=8`); deletion via
  `DELETE /slips/{id}` removes slip row and PDF, audit rows remain. Audit log archived, never edited
  (`06_audit_trail.md` §5.8). Backups follow the same retention and are encrypted.
- **Real samples:** real slips live only in `samples/real/` (git-ignored except README). Before being
  shared with developers they are masked: counterparty, dealer, account numbers (SGL, DP/client ID),
  and amounts replaced with consistent fake values; ISINs may be kept (public). Mock slips are
  fictitious (`tools/generate_mock_slips.py`).

---

## 6. Security headers

Set by middleware (assumption for exact values), also enforceable at the proxy:

| Header | Value |
|---|---|
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` (proxy, HTTPS only) |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Content-Security-Policy` | API responses: `default-src 'none'; frame-ancestors 'none'`. `/docs`, `/redoc`: relaxed policy allowing the Swagger/ReDoc assets (or self-host them) |
| `Referrer-Policy` | `no-referrer` |
| `Cache-Control` | `no-store` on all `/api/v1` responses |
| `Content-Disposition` | `attachment` for exports, `inline` for the PDF, sanitised filename |
| `Server` | removed / generic |

---

## 7. Dependency and code scanning

- Pin versions in `requirements.txt`; `pip-audit -r requirements.txt` in CI and before each release
  (fail on known vulnerabilities with a fix available).
- `bandit -r app` for Python security lint (assumption).
- Update pdfplumber / pdfminer.six promptly — the PDF parser is the main attack surface.
- Container image (after MVP): slim base, non-root user, image scan (e.g. Trivy).

---

## 8. OWASP API Security Top 10 (2023) mapping

| ID | Risk | How it is addressed | Status |
|---|---|---|---|
| API1 | Broken Object Level Authorization | No per-client ownership: any active client may access any slip/template. Deployment is single-tenant with a small set of trusted clients; `created_by` + audit record who touched what. Per-client scoping is a future option | **Accepted** |
| API2 | Broken Authentication | HTTP Basic over TLS, 256-bit secrets, Argon2id, constant-time, generic 401, limiter + 429, rotation/disable via CLI, `AUTH_FAILED` audit | Addressed |
| API3 | Broken Object Property Level Authorization | Response models are explicit Pydantic schemas (never ORM dumps): `secret_hash`, `storage_path` never returned; request models reject unknown properties; clients cannot set `status`, `approved_*`, `created_by` | Addressed |
| API4 | Unrestricted Resource Consumption | Upload size/page limits, parser timeout, `limit ≤ 200`, export caps, batch file cap, proxy rate limits | Addressed (partly Later) |
| API5 | Broken Function Level Authorization | No roles by design — every client may approve templates, delete slips, read audit. Mitigated by few trusted clients, full audit, `X-Actor-Name` | **Accepted** (RBAC future) |
| API6 | Unrestricted Access to Sensitive Business Flows | Approve requires required fields + validation (or explicit `accept_validation_issues`); template changes versioned and reversible (disable); all audited | Addressed |
| API7 | Server Side Request Forgery | The API never fetches URLs from input; PDF links not followed | Not applicable |
| API8 | Security Misconfiguration | Docs disabled in prod, CORS allow-list, security headers, no debug, generic errors, HTTPS/HSTS, go-live checklist | Addressed |
| API9 | Improper Inventory Management | Single versioned surface `/api/v1`, OpenAPI generated from code, endpoint list in `04_api_spec.md`; non-prod environments use separate clients and masked data | Addressed |
| API10 | Unsafe Consumption of APIs | No third-party APIs consumed. Imported templates (JSON) are treated as untrusted input and validated | Addressed |

### Known limitation: no per-person identity

Authentication identifies the **client application**, not the person. `X-Actor-Name` is free text set by
the client and is **not verified**; it is recorded for traceability only. Consequences: no
segregation of duties (maker/checker) enforced by this API, and non-repudiation is at client level.
Future options: SSO (OIDC/SAML) at the frontend with a signed user token (JWT) forwarded to the API,
and RBAC (e.g. `uploader`, `reviewer`, `template_admin`, `auditor`) with maker ≠ checker on approve.

---

## 9. Go-live security checklist

| # | Item | Done |
|---|---|---|
| 1 | HTTPS with valid certificate, TLS 1.2+, HTTP → HTTPS redirect, HSTS | ☐ |
| 2 | App bound to private interface only; proxy is the only entry point | ☐ |
| 3 | `BONDS_DOCS_ENABLED=false` (or `/docs` restricted by IP at proxy) | ☐ |
| 4 | `BONDS_CORS_ORIGINS` = production frontend origins only | ☐ |
| 5 | Production clients created fresh via CLI; dev/test clients not present; secrets delivered via vault | ☐ |
| 6 | Brute-force limiter enabled and tested (429 + `AUTH_BLOCKED` row) | ☐ |
| 7 | Postgres: app role has INSERT/SELECT only on `audit_log`; triggers present; `/audit/verify` returns `valid: true` | ☐ |
| 8 | Disk / DB / backup encryption on; data dir permissions `0700`; service runs as non-root | ☐ |
| 9 | Logs reviewed: no secrets, no `Authorization` header, no deal values | ☐ |
| 10 | Security headers present (check with `curl -I`) | ☐ |
| 11 | Upload limits verified (non-PDF → 415, > 10 MB → 413, > 20 pages → 422, encrypted → 422) | ☐ |
| 12 | `pip-audit` clean; `bandit` reviewed; dependencies pinned | ☐ |
| 13 | Backups + restore tested; audit hash anchor stored off-host | ☐ |
| 14 | Retention periods confirmed with compliance | ☐ |
| 15 | **VAPT** (vulnerability assessment + penetration test) by an independent team; high/critical findings fixed and re-tested | ☐ |
| 16 | Incident contacts and secret-rotation runbook documented | ☐ |

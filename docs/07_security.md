# 07 · Security Design

Security of the Bonds Deal Slip Parser `v0.1.0`. Decisions: B D8, D9, ADR-0005, ADR-0009.

---

## 1. Threat model (STRIDE)

| Threat | Example | Control |
|---|---|---|
| **S**poofing | Someone calls the API without being a known system | HTTP Basic client credentials; Argon2 hashes; constant-time checks; lockout after repeated failures |
| **T**ampering | Edit the audit log; swap a template to mis-map fields | Hash-chained audit with `/audit/verify`; template versions are append-only and every change is audited; templates reviewed in git |
| **R**epudiation | "We never approved that template" | `TEMPLATE_*` audit records with client id, `X-Actor-Name`, request id, IP |
| **I**nformation disclosure | Slip data or PANs leak from a server, log or backup | **Slips are never stored**; logs and audit hold no deal values; PANs masked in audit; TLS in transit |
| **D**enial of service | Huge or malicious PDFs | 10 MB / 20 page limits; `%PDF-` check; one request = one PDF in memory |
| **E**levation of privilege | A client does more than intended | Single role by design (no RBAC); separate clients per system so each can be disabled |

## 2. Authentication

### 2.1 Scheme

`Authorization: Basic base64(client_id:client_secret)` on every endpoint except `/health`. Missing
or wrong credentials → **401** with `WWW-Authenticate: Basic realm="bonds-parser"` and a generic
problem body (no hint whether the client exists).

### 2.2 Client lifecycle (`python -m app.cli`)

| Command | Effect | Audit |
|---|---|---|
| `add-client NAME [--description …]` | Creates the client; prints a 256-bit random secret **once** | `CLIENT_ADDED` |
| `list-clients` | Id, active, created, last used, rotated — never secrets or hashes | – |
| `rotate-secret NAME` | New secret (printed once); the old one stops working immediately | `CLIENT_SECRET_ROTATED` |
| `disable-client NAME` | All further calls with that client get 401 | `CLIENT_DISABLED` |

Clients live in `BONDS_CLIENTS_FILE` (default `./data/clients.json`, git-ignored, atomic writes).
Use one client per consuming system (frontend, batch job, …).

### 2.3 Secret storage

Argon2id (`argon2-cffi` defaults). The plain secret exists only in the CLI output. Verification
always performs one Argon2 check, using a dummy hash for unknown client ids, so response time does
not reveal whether an id exists.

### 2.4 Brute-force limiter

Per client IP: `BONDS_AUTH_MAX_FAILURES` (10) failures within `BONDS_AUTH_WINDOW_SECONDS` (300)
block that IP for `BONDS_AUTH_BLOCK_SECONDS` (900) → **429** with `Retry-After`, even with correct
credentials. Every failure is audited (`AUTH_FAILED`), the block as `AUTH_BLOCKED`. The counters are
in memory (one process per deployment, ADR-0009). Behind a reverse proxy, run Uvicorn with
`--proxy-headers` and a trusted proxy list so the real client IP is used.

### 2.5 Where the frontend keeps its secret

In the frontend's **server side** (backend-for-frontend or API gateway), never in browser
JavaScript — anyone using the browser could read it. The BFF adds the header and forwards
`X-Actor-Name` with the logged-in user.

## 3. Transport

HTTPS is mandatory outside localhost: terminate TLS at a reverse proxy (nginx, the company API
gateway) and enable HSTS there. The container publishes port 8000 on `127.0.0.1` only
(`docker-compose.yml`). CORS allows only `BONDS_CORS_ORIGINS`, without credentials.

## 4. Input and file security

| Control | Value |
|---|---|
| Size | `BONDS_MAX_UPLOAD_MB` (10) → 413, enforced while streaming |
| Type | First bytes must be `%PDF-` → 415 |
| Pages | `BONDS_MAX_PAGES` (20) → 422 |
| Encrypted / corrupt PDFs | 422 (`encrypted-pdf`, `invalid-pdf`) |
| Embedded content | Never executed: pdfplumber only reads text and positions |
| File names | Used only as a label in the response and the download name (sanitised); never as a path |
| Mapping payloads | Validated: field paths, regex compilation, box shape → 400 |

## 5. Data protection

- **No slip data at rest** (ADR-0009): the PDF and the result live only in request memory. There is
  nothing to encrypt, back up, retain or purge for slips.
- **Responses contain personal data**: names and the counterparty PAN (output in full by decision,
  B§5). Treat API responses, downloaded JSON / XML / Excel files and the frontend's copies as
  confidential.
- **Logs**: method, path, status, duration, client id, request id only — never credentials, PDF
  text or deal values.
- **Audit**: metadata only, no file names; PAN-shaped text masked (`ABCDE****F`).
- **Templates**: labels, rules and constants only; reviewed before commit. Never put client data
  in a constant.
- **No external calls** with slip data: no LLM, no cloud OCR, no telemetry.
- **Real samples** stay in `samples/real/` (git-ignored; a pre-commit hook blocks them).
- **Secrets**: client secrets only as Argon2 hashes; `.env` is git-ignored and blocked by a hook.

## 6. Security headers

Set at the reverse proxy: `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`,
`Referrer-Policy: no-referrer`, `Content-Security-Policy` for `/docs` if Swagger stays enabled.
Set `BONDS_DOCS_ENABLED=false` in production unless the docs are needed.

## 7. Dependency and code scanning

CI runs `pip-audit` (known vulnerabilities), `ruff` with the `S` (bandit) rules, and `mypy --strict`
on every push. The Docker image is non-root (uid 10001) with only the template and data folders
writable.

## 8. OWASP API Security Top 10 (2023)

| Risk | Status |
|---|---|
| API1 Broken object-level authorisation | Accepted: one role; every client can see all templates (no per-client data exists) |
| API2 Broken authentication | Argon2, constant time, lockout, no secrets in logs |
| API3 Broken object property level authorisation | Responses are fixed Pydantic models; no mass assignment |
| API4 Unrestricted resource consumption | Upload size / page limits; one PDF per request |
| API5 Broken function-level authorisation | Accepted: one role (RBAC is a future option) |
| API6 Unrestricted access to sensitive business flows | Lockout; audit of every approve and parse |
| API7 SSRF | No outbound calls |
| API8 Security misconfiguration | Docs toggle, CORS allow-list, non-root container, settings validated at start |
| API9 Improper inventory management | Single versioned API (`/api/v1`), OpenAPI generated from code |
| API10 Unsafe consumption of APIs | No third-party APIs consumed |

**Known limitation:** clients identify systems, not people. `X-Actor-Name` is informational. If
person-level accountability is needed, add SSO / RBAC in front (gateway) or in the API later.

## 9. Go-live security checklist

- [ ] TLS reverse proxy with HSTS; port 8000 not reachable from outside
- [ ] `BONDS_DOCS_ENABLED=false` (or docs behind the gateway)
- [ ] `BONDS_CORS_ORIGINS` set to the real frontend origin(s)
- [ ] Production clients created; demo / test clients disabled; secrets in the consumers' vaults
- [ ] Frontend calls through its server side (BFF), not from the browser
- [ ] `data/` and `templates/` on encrypted volumes with backups; audit file append-only at OS level
- [ ] Daily `verify-audit`, with the last hash recorded outside the server
- [ ] `pip-audit` clean; image rebuilt with current base image
- [ ] VAPT done; no open critical / high findings

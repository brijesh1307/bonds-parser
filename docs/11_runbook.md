# 11 · Runbook: Setup, Usage and Operations

How to install, run, use and operate the Bonds Deal Slip Parser `v0.1.0`. The service is stateless
for slips; its only state is `templates/` and `data/` (clients and audit log).

---

## 1. Local setup

### 1.1 Prerequisites

Python 3.11+ and Git. Docker optional.

### 1.2 Install

```bash
git clone https://github.com/brijesh1307/bonds-parser.git
cd bonds-parser
python -m venv .venv
.venv\Scripts\activate                    # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt           # requirements-dev.txt for tests and linters
```

### 1.3 Configure

Copy `.env.example` to `.env` only if you need non-default values (all `BONDS_*` variables are
optional). Create an API client:

```bash
python -m app.cli add-client frontend     # prints the secret ONCE
```

### 1.4 Run

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/docs → **Authorize** → `frontend` + secret.

**Docker**

```bash
docker compose up --build
docker compose exec api python -m app.cli add-client frontend
```

## 2. Everyday usage

```bash
export CRED=frontend:$SECRET
curl -u $CRED -F "file=@slip.pdf" "http://127.0.0.1:8000/api/v1/parse"                    # JSON
curl -u $CRED -F "file=@slip.pdf" -o deal.xlsx "http://127.0.0.1:8000/api/v1/parse?format=xlsx"
curl -u $CRED -F "file=@slip.pdf" -o deal.xml  "http://127.0.0.1:8000/api/v1/parse?format=xml"
curl -u $CRED http://127.0.0.1:8000/api/v1/templates                                         # layouts known
python -m app.cli debug-extract slip.pdf                                                      # what the parser reads
python tools/demo.py                                                                          # end-to-end demo
```

Onboarding a new layout: [`08_template_onboarding_guide.md`](08_template_onboarding_guide.md).

## 3. Configuration reference

| Variable | Default | Purpose |
|---|---|---|
| `BONDS_DATA_DIR` | `./data` | Folder for clients and audit |
| `BONDS_TEMPLATES_DIR` | `./templates` | Approved templates |
| `BONDS_CLIENTS_FILE` | `<data>/clients.json` | API clients (Argon2 hashes) |
| `BONDS_AUDIT_FILE` | `<data>/audit/audit.jsonl` | Audit log |
| `BONDS_MAX_UPLOAD_MB` | `10` | Larger uploads → 413 |
| `BONDS_MAX_PAGES` | `20` | Longer PDFs → 422 |
| `BONDS_CONFIDENCE_THRESHOLD` | `0.90` | Required-field confidence for `PARSED` |
| `BONDS_TEMPLATE_MATCH_THRESHOLD` | `0.80` | Default template match score |
| `BONDS_CORS_ORIGINS` | `http://localhost:3000,http://localhost:5173` | Allowed frontend origins |
| `BONDS_DOCS_ENABLED` | `true` | Swagger / ReDoc / OpenAPI on or off |
| `BONDS_AUTH_MAX_FAILURES` / `_WINDOW_SECONDS` / `_BLOCK_SECONDS` | `10` / `300` / `900` | Login lockout |
| `BONDS_LOG_LEVEL` | `INFO` | Log level |
| `BONDS_AUDIT_RETENTION_YEARS` | `8` | Audit retention policy |

An invalid value stops the service at start with a clear message.

## 4. Production deployment

```
client ─HTTPS─► reverse proxy (TLS, HSTS, real client IP) ─► api container :8000 (one worker)
                                                              ├── templates/  (volume)
                                                              └── data/       (volume)
```

- Run **one** API process per `templates/` + `data/` pair (file stores, ADR-0009).
- `--proxy-headers` with the proxy's address trusted, so the lockout sees real client IPs.
- `BONDS_DOCS_ENABLED=false`, `BONDS_CORS_ORIGINS` = the real frontend origin.
- Volumes on encrypted storage; the audit file append-only at OS level where possible.
- Security checklist: [`07_security.md`](07_security.md) §9.

## 5. Operations

### 5.1 Health

`GET /health` → 200 `{"status": "ok", "templates": "ok", "audit": "ok", …}`; 503 when either
store is not writable (disk full, permissions). The container has a built-in healthcheck.

### 5.2 Logs

One access-log line per request: method, path, status, duration, client id, request id. Find a
user's problem by the `X-Request-ID` they saw (it is also in the audit record).

### 5.3 Backups and restore

Back up **only** `templates/` and `data/` (there is no slip data). Restore = put both folders back
and restart; then run `python -m app.cli verify-audit`.

### 5.4 Client secrets

Rotate: `python -m app.cli rotate-secret NAME` (the old secret stops immediately; update the
consumer). Leaver / incident: `disable-client NAME`. Review `list-clients` monthly.

### 5.5 Audit verification

Daily `python -m app.cli verify-audit` (exit code 1 = broken chain) and record the last hash
(`GET /api/v1/audit/verify` → `last_hash`) outside the server.

### 5.6 Audit retention and archive

When the file grows large or at year end: stop the service, move `audit.jsonl` to the archive
(keep it for `BONDS_AUDIT_RETENTION_YEARS`), note its last hash, start the service (a new file
starts a new chain). Keep the archived file read-only.

### 5.7 Upgrading

`git pull` → `pip install -r requirements.txt` (or rebuild the image) → restart. Templates and the
audit format are forward-compatible within `v0.x`; read the release notes for changes.

## 6. Troubleshooting

| Symptom | Cause | Action |
|---|---|---|
| 401 on every call | Wrong / rotated / disabled client | `list-clients`; rotate and update the consumer |
| 429 | Too many failed logins from that IP | Wait `Retry-After` or restart the service (counters are in memory); fix the consumer's secret |
| 413 / 415 / 422 | File too big / not a PDF / encrypted, corrupt or too many pages | Check the file; password-protected PDFs are not supported yet |
| `UNREADABLE` | Scanned slip (no text layer) | OCR is not available yet; enter manually |
| `NEW_TEMPLATE` for a known layout | Template disabled, or layout changed | `GET /templates`; re-approve as a new version |
| `NEEDS_REVIEW` | Missing field, failed check, low confidence | Read `missing_required`, `validation`, `low_confidence` in the response |
| `/health` 503 | Disk full or permissions on `templates/` / `data/` | Fix storage; restart |
| `verify-audit` broken | The audit file was changed | Treat as a security incident (§7) |

## 7. Incident response

1. Contain: disable affected clients (`disable-client`), restrict network access at the proxy.
2. Preserve: copy `data/audit/audit.jsonl` and the access logs read-only.
3. Assess: `verify-audit`; `GET /api/v1/audit` for the time window, `AUTH_FAILED` / `AUTH_BLOCKED`.
4. Recover: rotate secrets, restore templates from git / backup if tampered, restart.
5. Review: write up the incident; update `07_security.md` if a control was missing.

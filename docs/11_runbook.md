# 11 · Runbook: Setup, Usage and Operations

> **Update 2026-10-02 — stateless design ([ADR-0009](adr/0009-stateless-api-no-slip-storage.md)).**
> Deal slips are never stored and there is no database. Wherever this document describes a `slips`
> table, `/api/v1/slips/*` endpoints, SQLite / PostgreSQL, uploads storage or slip statuses such as
> `APPROVED`, [`00_design_baseline.md`](00_design_baseline.md) is authoritative: templates, clients and
> the audit log are files, and preview / approve take the PDF + mapping in one request.

For developers running the Bonds Deal Slip Parser locally and for the people operating it.
Names, env vars and endpoints follow `docs/00_design_baseline.md`.

> Items marked **(assumption)** (exact response bodies, ops tooling, schedules) are not fixed by the
> baseline.

---

## 1. Local setup

### 1.1 Prerequisites

| Need | Version / note |
|---|---|
| Python | 3.11+ (`python --version`) |
| OS | Windows 10/11 or Linux (macOS works the same as Linux) |
| Git | any recent |
| curl | Windows: use `curl.exe` in PowerShell (plain `curl` is an alias for `Invoke-WebRequest`) |
| Disk | ~100 MB + uploads |

### 1.2 Install

```bash
git clone <repo-url> bonds-parser
cd bonds-parser
python -m venv .venv
```

| Windows (PowerShell) | Linux |
|---|---|
| `.venv\Scripts\activate` | `source .venv/bin/activate` |

```bash
pip install -r requirements.txt
```

> Development tools (ruff, mypy, pytest-cov, hypothesis, pip-audit) are in `requirements-dev.txt`:
> `pip install -r requirements-dev.txt`.

### 1.3 Configure and initialise

```bash
cp .env.example .env              # Windows: copy .env.example .env
python -m app.cli init-db         # creates data/bonds.db, tables, audit triggers, data/uploads/
python -m app.cli add-client frontend
```

`add-client` prints the **client secret once**. Store it in your password manager or the
frontend's secret store. Only the Argon2 hash is kept, so the secret cannot be shown again (rotate
it instead, §5.4). Audit row: `CLIENT_ADDED`.

### 1.4 Run

```bash
uvicorn app.main:app --reload
```

1. Open <http://127.0.0.1:8000/docs> (Swagger UI; ReDoc at `/redoc`).
2. Click **Authorize** and enter username `frontend` and the secret as the password.
3. Open **Parse → `POST /api/v1/parse`**, choose `format=json`, upload
   `samples/mock/01_gsec_outright_purchase.pdf`, and click **Execute**.
4. Compare the result with `samples/mock/expected/01_gsec_outright_purchase.json`
   (`deal_id` `GS/NDSOM/2026/004571`, `consideration` `"52264305.56"`).

---

## 2. Everyday usage (curl)

```bash
export API=http://127.0.0.1:8000/api/v1
export CRED='frontend:<secret>'
```

The multipart field name `file` (and `files` for batch) is an **(assumption)**. Check it in Swagger.

| Task | Command |
|---|---|
| Health (no auth) | `curl -s http://127.0.0.1:8000/health` |
| One-shot parse → JSON (nothing stored) | `curl -s -u "$CRED" -F "file=@samples/mock/01_gsec_outright_purchase.pdf" "$API/parse?format=json"` |
| One-shot parse → XML | `curl -s -u "$CRED" -F "file=@samples/mock/02_corporate_ncd_outright_sale.pdf" "$API/parse?format=xml" -o deal.xml` |
| One-shot parse → Excel | `curl -s -u "$CRED" -F "file=@samples/mock/03_tbill_primary_auction_allotment.pdf" "$API/parse?format=xlsx" -o deal.xlsx` |
| Upload + store + parse | `curl -s -u "$CRED" -H "X-Actor-Name: Asha Rao" -F "file=@samples/mock/04_market_repo_reverse_repo.pdf" "$API/slips"` |
| Review queue | `curl -s -u "$CRED" "$API/slips?status=NEEDS_REVIEW&limit=50"` |
| New layouts to onboard | `curl -s -u "$CRED" "$API/slips?status=NEW_TEMPLATE"` |
| One slip, full result | `curl -s -u "$CRED" "$API/slips/<id>"` |
| Export one slip | `curl -s -u "$CRED" "$API/slips/<id>/export?format=xlsx" -o <id>.xlsx` |
| Export many deals (after MVP) | `curl -s -u "$CRED" "$API/exports/deals?format=xlsx&status=APPROVED&from=2026-09-01&to=2026-09-30" -o deals.xlsx` |
| Slip audit timeline | `curl -s -u "$CRED" "$API/slips/<id>/history"` |
| Verify audit chain | `curl -s -u "$CRED" "$API/audit/verify"` |

Onboarding a new layout (preview / approve) is covered in `docs/08_template_onboarding_guide.md`.

Other routine commands:

```bash
python tools/generate_mock_slips.py      # regenerate samples/mock/*.pdf + expected/*.json
pytest                                   # run the test suite (docs/10_testing_strategy.md)
python -m app.cli list-clients
```

---

## 3. Configuration reference

All settings are env vars (or `.env`), read by `app/config.py`.

| Variable | Default | Purpose | Production guidance |
|---|---|---|---|
| `BONDS_DATA_DIR` | `./data` | DB file + uploads | Persistent volume, backed up |
| `BONDS_DATABASE_URL` | `sqlite:///./data/bonds.db` | SQLAlchemy URL | `postgresql+psycopg://bonds_app:<pw>@db:5432/bonds` |
| `BONDS_MAX_UPLOAD_MB` | `10` | Larger files → 413 | Keep ≤ reverse proxy body limit |
| `BONDS_MAX_PAGES` | `20` | Longer PDFs → 422 | |
| `BONDS_CONFIDENCE_THRESHOLD` | `0.90` | Required-field confidence for `PARSED` | Do not lower without business sign-off |
| `BONDS_TEMPLATE_MATCH_THRESHOLD` | `0.80` | Default Jaccard score for a template match | |
| `BONDS_CORS_ORIGINS` | `http://localhost:3000,http://localhost:5173` | Frontend origins | Exact production frontend origin(s) only |
| `BONDS_DOCS_ENABLED` | `true` | `false` hides Swagger / ReDoc | `false` in production (or restrict at the proxy) |
| `BONDS_AUTH_MAX_FAILURES` | `10` | Failed auths per IP per window before a block | |
| `BONDS_AUTH_WINDOW_SECONDS` | `300` | Window for counting failures | |
| `BONDS_AUTH_BLOCK_SECONDS` | `900` | Block duration (429) | |
| `BONDS_LOG_LEVEL` | `INFO` | Logging level | `INFO`; `DEBUG` only briefly |
| `BONDS_AUDIT_RETENTION_YEARS` | `8` | Retention for the archive job | Confirm with compliance |

---

## 4. Production deployment outline

```
 Frontend ──HTTPS──► Reverse proxy (nginx / IIS / ALB)  TLS 1.2+, body limit, X-Forwarded-For
                          │ http (private network)
                          ▼
                   gunicorn -k uvicorn.workers.UvicornWorker  (N workers)
                          │
            ┌─────────────┴──────────────┐
            ▼                            ▼
      PostgreSQL (bonds)          File storage: BONDS_DATA_DIR/uploads (NAS / S3 later)
```

| Item | Guidance |
|---|---|
| TLS | Terminate at the reverse proxy. HTTP Basic is only acceptable over HTTPS. Redirect 80 → 443; HSTS |
| Body limit | Proxy `client_max_body_size` ≈ `BONDS_MAX_UPLOAD_MB` + 1 MB |
| App server | Linux: `gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 4 -b 127.0.0.1:8000 --timeout 60`. Windows: `uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4`. Never `--reload` |
| Client IP | Run uvicorn with `--proxy-headers --forwarded-allow-ips=<proxy ip>` so the auth limiter and audit `ip_address` see the real client |
| Database | PostgreSQL (D4). The app role owns no DDL at runtime. Revoke `UPDATE` / `DELETE` on `audit_log` from the app role **in addition to** the triggers. Alembic migrations after MVP |
| SQLite in prod | Not supported beyond a single-user pilot (single writer, see §6) |
| File storage | `BONDS_DATA_DIR` on a persistent, backed-up volume; readable only by the service account; encrypted at rest |
| Secrets | `BONDS_DATABASE_URL` and client secrets come from the platform secret store / env, never from a committed `.env` |
| Docs | `BONDS_DOCS_ENABLED=false`, or expose `/docs` only on the internal network |
| Service | systemd unit / Windows service (NSSM) with auto-restart. Docker + `docker-compose.yml` after MVP |
| Time | Server clock on NTP. Audit timestamps are UTC |

---

## 5. Operations

### 5.1 Health checks

`GET /health` (no auth) checks liveness plus a DB query. Point the load balancer and monitoring at
it every 30 s, and alert after 3 consecutive failures. Example body (**assumption**):
`{"status": "ok", "db": "ok", "version": "0.1.0"}`. Anything else, or a non-200, means unhealthy.

### 5.2 Logs

- Uvicorn / gunicorn log to stdout / stderr. Collect them with journald / Windows Event Log / the
  platform log shipper.
- Each request logs `request_id` (`X-Request-ID`), method, path, status and duration. Use the
  request id to join logs to `audit_log.request_id`.
- **Slip contents (amounts, counterparties) must not be logged.** Log ids and statuses only.
  The audit log and the DB are the records.
- Retain logs 90 days (**assumption**). The audit log is the long-term record.

### 5.3 Backups and restore

Back up **the DB and `data/uploads/` together**. Slips reference files by `storage_path`.

| What | SQLite (MVP / pilot) | PostgreSQL (prod) |
|---|---|---|
| DB backup | `python -c "import sqlite3; s=sqlite3.connect('data/bonds.db'); d=sqlite3.connect('backup/bonds-$(date +%F).db'); s.backup(d)"` (online-safe; do not just copy the file while the app runs) | `pg_dump -Fc -d bonds -f bonds-$(date +%F).dump` |
| Files | `rsync -a data/uploads/ backup/uploads/` (Windows: `robocopy data\uploads backup\uploads /MIR`) | same, or object-store versioning |
| Frequency (**assumption**) | Daily full; keep 35 daily + 12 monthly | Daily full + WAL archiving (PITR) |
| Where | Off-host, encrypted | Off-host, encrypted |

**Restore:**

1. Stop the app.
2. Restore the DB (`copy` the SQLite file / `pg_restore -c -d bonds <dump>`).
3. Restore `data/uploads/` from the same point in time.
4. Start the app and call `GET /api/v1/audit/verify`. It must return OK.
5. Spot-check `GET /slips/{id}/pdf` for a few recent slips.

Test a restore quarterly (**assumption**) and record the result.

### 5.4 Client secret rotation

```bash
python -m app.cli rotate-secret frontend   # prints new secret once; rotated_at set; CLIENT_SECRET_ROTATED
```

1. Rotate. The old secret stops working immediately (**assumption**: no overlap window).
2. Update the frontend's secret store and restart or redeploy it.
3. Confirm `last_used_at` moves for the client (`list-clients`) and that the logs show no 401s.
4. Rotate every 90 days (**assumption**), and immediately if a leak is suspected.

To cut a client off: `python -m app.cli disable-client <client_id>` (`CLIENT_DISABLED`).

### 5.5 Audit verification schedule

- Run `GET /api/v1/audit/verify` **daily** (cron / Task Scheduler with a dedicated `ops` client),
  and after every restore or upgrade.
- Alert on any result other than OK. Treat it as a **security incident** (§8).
- Monthly: export the audit log for the month (`GET /api/v1/audit/export?format=json&from=&to=`,
  after MVP) to write-once storage, and record the last `row_hash` as an external anchor.

### 5.6 Retention and archive

- `BONDS_AUDIT_RETENTION_YEARS` (default **8**) sets how long audit data is kept.
- Audit rows are never updated or deleted in place (triggers). Archiving (after MVP,
  **assumption**) exports rows older than the retention boundary to write-once storage, with their
  hashes and the anchor of the last archived row. Removing them needs a controlled, audited
  maintenance procedure that records a chain checkpoint, so verification restarts from the anchor.
- Slips and PDFs: `DELETE /api/v1/slips/{id}` (after MVP) removes the slip and PDF but keeps its
  audit rows. Apply the business retention policy for deal records.

### 5.7 Upgrading

1. Read the release notes. Back up (§5.3).
2. `git pull` / deploy the new build; `pip install -r requirements.txt`.
3. Migrations: before Alembic (MVP), schema changes need `init-db` on a fresh DB or a manual
   script. After MVP: `alembic upgrade head`.
4. Restart the workers. Check `GET /health`, `GET /api/v1/audit/verify` and one test parse of a
   mock slip.
5. Re-run any template regression fixtures against production templates before switching traffic
   (**assumption**).
6. Roll back by restoring the previous build plus the pre-upgrade backup. Never downgrade the
   schema by hand.

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `401` on every call | Missing / wrong `Authorization` header, wrong secret, disabled client, or the proxy strips the header | Check with `curl -v -u id:secret`. Run `list-clients` for `is_active`. Rotate if unsure. Check the proxy forwards `Authorization` |
| `401` in Swagger only | Not authorised in the UI | Click **Authorize** again (Swagger forgets on reload) |
| `429` | Too many failed auths from one IP (`BONDS_AUTH_MAX_FAILURES` in `BONDS_AUTH_WINDOW_SECONDS`) | Wait `BONDS_AUTH_BLOCK_SECONDS` (900 s). Fix the client's secret. Check `AUTH_BLOCKED` audit rows for an attack |
| `413` | File > `BONDS_MAX_UPLOAD_MB` (or the proxy limit) | Ask for a smaller PDF, or raise both limits together |
| `422` "encrypted" | Password-protected PDF | Ask for an unprotected PDF (pikepdf support after MVP) |
| `422` "too many pages" | > `BONDS_MAX_PAGES` | Split the PDF; multi-deal PDFs are after MVP |
| `422` other | Bad query param / not a PDF / malformed PDF | Read the problem `detail` |
| Status `UNREADABLE` | Scanned image, no text layer | Ask for the system-generated PDF; OCR is after MVP |
| Status `FAILED` | Internal error | `GET /slips/{id}/history` plus logs by `request_id`. Report it with the (masked) slip |
| Slow parsing | Large or many-page PDFs, huge tables, many active templates, debug logging, too few workers | Check page count / size. Set `BONDS_LOG_LEVEL=INFO`. Add workers. Disable unused templates. Profile with `pytest -m perf` |
| `database is locked` (SQLite) | Several workers / processes writing at once | Use 1 worker on SQLite, or enable WAL (`PRAGMA journal_mode=WAL`, **assumption**). Move to PostgreSQL for real use |
| `/audit/verify` fails | Chain broken: tampering, manual DB edit, partial restore, or a bug | **Do not fix the data.** Open an incident (§8) |
| Swagger 404 | `BONDS_DOCS_ENABLED=false` | Expected in production |
| CORS error in the browser | Frontend origin not in `BONDS_CORS_ORIGINS` | Add the exact origin (scheme + host + port) and restart |
| `ModuleNotFoundError: app` | Not run from the repo root / venv not active | `cd` to the repo root; activate `.venv` |

---

## 7. Monitoring (recommended, **assumption**)

| Metric / check | Alert when |
|---|---|
| `/health` | 3 consecutive failures |
| 5xx rate | > 1% over 5 min |
| `FAILED` slips | Any |
| `AUTH_FAILED` / `AUTH_BLOCKED` | Spike vs baseline |
| Review queue size (`NEEDS_REVIEW` + `NEW_TEMPLATE`) | Older than 1 business day |
| Audit verify | Anything but OK |
| Disk free on `BONDS_DATA_DIR` | < 20% |
| Backup job | Missed or failed |

`prometheus-client` / `sentry-sdk` are after MVP (baseline §10).

---

## 8. Incident response

Triggers: audit verify failure, suspected secret leak, unexpected `AUTH_BLOCKED` volume, data
exposure, wrong deals delivered downstream, or a prolonged outage.

1. **Declare.** Name an incident lead. Open a ticket. Record the time (UTC) and the trigger.
2. **Contain.**
   - Secret leak: `disable-client` / `rotate-secret` the affected client(s).
   - Tampering suspected: stop the app or block write traffic at the proxy. Keep the DB read-only.
   - Wrong outputs: disable the template involved (`PATCH /templates/{id}` `is_active: false`,
     or stop processing), and tell downstream consumers to hold.
3. **Preserve evidence.** Take a copy of the DB and `data/uploads/` **before** any fix. Export the
   audit log (`/audit/export` or a DB dump) plus the application and proxy logs for the window.
   Hash the copies.
4. **Assess.** For verify failures, the first broken `id` shows where the chain breaks. Compare with
   the last good backup and the monthly anchor to find what changed. Use `request_id`, `actor_id`
   and `ip_address` to trace the calls. List the affected slips and deals.
5. **Recover.** Restore from the last verified backup if needed (§5.3), re-apply legitimate changes
   (re-upload / re-approve; this creates new audit rows), rotate all secrets if access was involved,
   and confirm that `audit/verify` returns OK.
6. **Notify.** Business owners and compliance / security, as policy requires (data-protection and
   regulator timelines where applicable).
7. **Review.** Within 5 business days (**assumption**) write a blameless postmortem: timeline, root
   cause, impact, actions (e.g. a new test, a new alert). Link it in the ticket.

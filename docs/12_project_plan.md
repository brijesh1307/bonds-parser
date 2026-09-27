# 12 · Project Plan

Scope, names and the MVP/after-MVP split come from `docs/00_design_baseline.md` (B§11).
Requirement IDs (FR-, NFR-) and risk IDs (R-) refer to `docs/01_requirements.md`.
Owners are role placeholders; estimates are in ideal engineer-hours (h) or days (d).

---

## 1. Phases at a glance

```
Phase 0  MVP (2 h)              engine + templates + exports + API/auth/audit + tests
Phase 1  Storage                Postgres + Alembic, remaining endpoints
Phase 2  Hardening              OCR, password PDFs, multi-deal, brute-force limiter
Phase 3  Market profiles        US / GB / INTL (+ DE / JP as needed), schema/markets
Phase 4  Operations             Docker, CI, logging/metrics, backups, HTTPS
Phase 5  Go-live                UAT, parallel run, VAPT, cut-over
```

---

## 2. The 2-hour MVP

Goal: a working API that parses the 4 mock slips end to end, onboards a new layout via
preview/approve, returns JSON/XML/Excel, authenticates by header and audits every action.
Scope is exactly B§11 "MVP"; anything else is deferred.

### 2.1 Time-boxed blocks

| Block | Time | Focus | Output |
|---|---|---|---|
| 1 | 0:00–0:40 (40 min) | Engine | `app/engine/*`: fields, extract, market (`IN` + detection), detect, mapping, normalize, derive, validate, pipeline |
| 2 | 0:40–1:05 (25 min) | Templates + exports | DB models (`templates`, `template_versions`), template service, `app/export/*` JSON/XML/Excel |
| 3 | 1:05–1:35 (30 min) | API + auth + audit | FastAPI app, routers, HTTP Basic + Argon2, CLI, `audit_log` with hash chain + triggers |
| 4 | 1:35–2:00 (25 min) | Tests + README + demo | Golden tests on 4 mocks, API flow test, README quick start, demo script |

### 2.2 MVP task list

| ID | Task | Est. | Depends on | Done when |
|---|---|---|---|---|
| M1.1 | Project skeleton: `app/` layout (B§8), `config.py` with `BONDS_*` settings, `.env.example` | 5 min | – | App imports; settings load defaults from B§9 |
| M1.2 | `engine/fields.py`: canonical fields, enums, required-by-deal-type, built-in synonyms (F§4) | 5 min | M1.1 | Field list matches B§5 |
| M1.3 | `engine/extract.py`: pdfplumber words/tables → KeyValue `{key, value, source, page, key_bbox, value_bbox}` for table, grid, text, prose | 10 min | M1.1 | All 4 mocks yield KVs covering every expected field; image-only PDF → `UNREADABLE` |
| M1.4 | `engine/market.py` + `profiles/IN.json`: signal vote; India number/date/enum rules | 5 min | M1.3 | 4 mocks → `IN`; US ISIN + USD → not `IN` |
| M1.5 | `engine/detect.py` + `mapping.py`: fingerprint, Jaccard, template → synonym → abbreviation → fuzzy, with method + confidence | 5 min | M1.2, M1.3 | Confidence values match B§4 table |
| M1.6 | `engine/normalize.py`, `derive.py`, `validate.py`, `pipeline.py`: status per B§3 | 10 min | M1.4, M1.5 | Pipeline on each mock matches `samples/mock/expected/*.json`; validations pass |
| M2.1 | `db/database.py`, `db/models.py` for all B§6 tables; `cli init-db` | 7 min | M1.1 | `init-db` creates `data/bonds.db` with 5 tables |
| M2.2 | `template_service`: create/version from mapping payload, list, get; `accepted_derived` | 8 min | M2.1, M1.6 | Approve twice → versions 1 and 2, UNIQUE enforced |
| M2.3 | `export/json_export.py`, `xml_export.py`, `excel_export.py` (Decimal as strings) | 10 min | M1.6 | Each mock exports in 3 formats; files open; no floats |
| M3.1 | `audit_service`: append row with `prev_hash`/`row_hash` in same transaction; SQLite triggers block UPDATE/DELETE; verify | 8 min | M2.1 | UPDATE on `audit_log` raises; verify OK |
| M3.2 | `auth.py` + `cli.py`: Basic auth, Argon2, `add-client`, `list-clients`, `disable-client`, `rotate-secret`; `AUTH_FAILED` audited | 7 min | M2.1, M3.1 | No header → 401; valid → 200 |
| M3.3 | `main.py` + routers: health, parse, slips (upload, list, get, pdf, preview, approve, export, history), templates (list, get), schema/fields, audit (list, verify); RFC 7807 handlers; `X-Request-ID`, `X-Actor-Name`; CORS | 15 min | M1.6, M2.2, M2.3, M3.1, M3.2 | All MVP endpoints visible and callable in `/docs` |
| M4.1 | Golden tests: 4 mocks vs expected JSON; engine unit tests (ISIN, amounts, dates) | 10 min | M1.6 | `pytest tests/golden tests/unit` green |
| M4.2 | API flow test: auth → upload (`NEW_TEMPLATE`) → preview → approve → re-upload (`PARSED`) → export ×3 → history → audit verify | 8 min | M3.3 | `pytest tests/api tests/audit tests/auth` green |
| M4.3 | README quick start (install, `init-db`, `add-client`, run, curl examples) | 4 min | M3.3 | A new developer runs the demo from README alone |
| M4.4 | Demo run-through and fix list | 3 min | M4.2 | Demo script completes without manual DB edits |

MVP is **done** when §7 Definition of Done holds for every M-task and the demo in M4.4 passes.

---

## 3. Work breakdown after MVP

| ID | Task / sub-task | Owner | Est. | Depends on | Done when |
|---|---|---|---|---|---|
| **P1** | **Storage & remaining endpoints** | Backend dev | **4 d** | MVP | |
| P1.1 | Alembic baseline migration from `models.py`; SQLite + Postgres | Backend dev | 0.5 d | MVP | `alembic upgrade head` builds both DBs identically |
| P1.2 | Postgres `audit_log` triggers + restricted DB role (no UPDATE/DELETE grant) | Backend dev | 0.5 d | P1.1 | Tamper test fails on Postgres as on SQLite |
| P1.3 | `psycopg` driver, connection pool, `BONDS_DATABASE_URL` switch | Backend dev | 0.25 d | P1.1 | Full test suite green on Postgres |
| P1.4 | Endpoints: batch upload, reparse (`force`), delete | Backend dev | 0.75 d | P1.3 | FR-03, FR-18 acceptance met |
| P1.5 | Template `PUT`, `PATCH`, `import`, `history` with version diffs | Backend dev | 1 d | P1.3 | FR-08, FR-19, FR-27 acceptance met |
| P1.6 | `exports/deals`, audit export (csv/xlsx/json), XSD for XML | Backend dev | 1 d | P1.3 | FR-16, FR-21 acceptance met; XML validates against XSD |
| **P2** | **Hardening** | Backend dev | **6 d** | P1 | |
| P2.1 | Brute-force limiter (`BONDS_AUTH_*`), `AUTH_BLOCKED` | Backend dev | 0.5 d | MVP | FR-23 acceptance met |
| P2.2 | OCR path (ocrmypdf / Tesseract) for `UNREADABLE`; OCR fields capped below threshold | Backend dev | 2 d | MVP | Scanned mock parses to `NEEDS_REVIEW` with correct values |
| P2.3 | Password-protected PDFs via pikepdf | Backend dev | 1 d | MVP | FR-30 acceptance met (R-01) |
| P2.4 | Multi-deal detection and split | Backend dev | 1.5 d | P1.3 | FR-31 acceptance met (R-02) |
| P2.5 | Holiday calendar loader (Indian settlement), date-ambiguity rules | Backend dev + Ops SME | 0.5 d | MVP | Holiday settlement flagged; ambiguous dates lower confidence (R-05) |
| P2.6 | Malformed/huge PDF fuzz tests, parser time/memory limits | QA | 0.5 d | MVP | NFR-09 met |
| **P3** | **Market profiles** | Backend dev | **4 d** | P2.5 | |
| P3.1 | Profile interface + `GET /api/v1/schema/markets` | Backend dev | 0.5 d | MVP | Lists `IN` and new profiles |
| P3.2 | US profile (CUSIP, month-first dates, UST, 30/360 & ACT/ACT) | Backend dev | 1 d | P3.1 | US mock slip → `PARSED` with template |
| P3.3 | GB profile (SEDOL, GILT) and INTL (Eurobond, Common Code) | Backend dev | 1.5 d | P3.1 | GB and INTL mocks → `PARSED` with template |
| P3.4 | DE / JP profiles (BUND, JGB) if slips exist | Backend dev | 1 d | P3.1 | Only if DEP-01 provides slips |
| **P4** | **Operations** | DevOps | **4 d** | P1 | |
| P4.1 | Dockerfile + docker-compose (API + Postgres) | DevOps | 0.5 d | P1.3 | `docker compose up` serves `/health` 200 |
| P4.2 | CI: lint, type check, tests on SQLite + Postgres, dependency audit | DevOps | 1 d | P1.3 | Every push runs pipeline; main is protected |
| P4.3 | structlog JSON logs with request id; Prometheus metrics; Sentry | DevOps + Backend dev | 1 d | MVP | Dashboards show latency p95, status counts, template hit rate |
| P4.4 | Backups (DB + `data/uploads`), restore drill, audit archive job (`BONDS_AUDIT_RETENTION_YEARS`) | DevOps | 1 d | P4.1 | Restore tested; chain verifies after restore (NFR-12, NFR-14) |
| P4.5 | HTTPS via reverse proxy; `BONDS_DOCS_ENABLED=false` in prod; CORS to frontend origin | DevOps | 0.5 d | P4.1 | TLS-only access verified (NFR-06) |
| **P5** | **Go-live** | Project lead | **~4–5 weeks elapsed** | P1–P4 | |
| P5.1 | Collect and mask real slips per source (`samples/real/`) | Ops SME | 3 d | – | ≥ 5 slips per top source (A-11) |
| P5.2 | Onboard templates for top sources; add masked golden tests | Ops SME + Backend dev | 5 d | P5.1, P1 | Each source has an approved template and a golden test |
| P5.3 | Performance test (NFR-01, NFR-02) | QA | 1 d | P4.1 | p95 ≤ 3 s for 1–5 page PDFs |
| P5.4 | UAT with back-office ops (via frontend or Swagger) | QA + Ops SME | 5 d | P5.2, frontend | UAT sign-off; no open Sev-1/Sev-2 |
| P5.5 | Parallel run: manual keying and parser side by side, daily diff | Ops SME | 10 business days | P5.4 | ≥ 95 % auto-`PARSED` on known templates; 0 wrong unflagged values (NFR-03, NFR-04) |
| P5.6 | VAPT and fixes | Security | 5 d | P4.5 | No open Critical/High (NFR-10) |
| P5.7 | Runbook, client credential hand-over, cut-over | Project lead + DevOps | 1 d | P5.5, P5.6 | §8 go-live checklist complete |

Effort summary (engineering, excluding elapsed waiting): P1 4 d, P2 6 d, P3 3–4 d, P4 4 d,
P5 about 30 d of mixed roles across ~4–5 weeks elapsed (parallel run dominates).

---

## 4. Milestones

| ID | Milestone | Exit criterion | Target (relative) |
|---|---|---|---|
| MS-0 | MVP demo | M4.4 passes; 4 mocks parse; preview/approve/export/audit work | Day 0 + 2 h |
| MS-1 | Production storage | P1 done; tests green on Postgres | +1 week |
| MS-2 | Hardened parser | P2 done; OCR, password PDFs, multi-deal, limiter | +2.5 weeks |
| MS-3 | Deployable | P4 done; Docker, CI, logs/metrics, backups, HTTPS | +3 weeks (parallel with P2) |
| MS-4 | UAT sign-off | P5.4 signed | +5 weeks |
| MS-5 | Parallel run passed | P5.5 targets met | +7 weeks |
| MS-6 | Go-live | VAPT clear, §8 checklist complete | +8 weeks |
| MS-7 | Additional markets | P3 done (not a go-live blocker) | After go-live, as demand requires |

Assumption: one backend developer and one DevOps engineer, part-time QA and Ops SME. The
baseline does not fix team size or dates.

---

## 5. Responsibilities (RACI)

R = Responsible, A = Accountable, C = Consulted, I = Informed.

| Activity | Project lead | Backend dev | DevOps | QA | Ops SME (back office) | Frontend dev | Security | Auditor / compliance |
|---|---|---|---|---|---|---|---|---|
| Requirements & scope | A | C | C | C | R | C | C | C |
| Design baseline changes | A | R | C | I | C | C | I | I |
| Engine, templates, exports | A | R | I | C | C | I | I | I |
| API contract / Swagger | A | R | I | C | I | C | I | I |
| Auth & client management | A | R | R | C | I | I | C | I |
| Audit trail design | A | R | C | C | I | I | C | C |
| Template onboarding (real slips) | A | C | I | C | R | I | I | I |
| Infrastructure, CI, backups | A | C | R | I | I | I | C | I |
| Testing (unit, golden, perf) | A | R | C | R | C | I | I | I |
| UAT & parallel run | A | C | I | R | R | C | I | I |
| VAPT | A | C | C | I | I | I | R | I |
| Go-live decision | A | C | C | C | C | I | C | C |
| Client credential issue / rotation | I | I | R | I | I | I | C | I |
| Audit evidence review | I | C | C | I | I | I | I | R/A |

---

## 6. Risk register

The risk register is `docs/01_requirements.md` §9 (R-01 … R-13). It is reviewed at each
milestone. Plan tasks that mitigate risks:

| Risk | Mitigating task(s) |
|---|---|
| R-01 Password-protected PDFs | P2.3 |
| R-02 Multi-deal PDFs | P2.4 |
| R-03 Scanned slips | M1.3 (`UNREADABLE`), P2.2 |
| R-04 Other-country slips | M1.4, P3 |
| R-05 Date-order ambiguity | M1.6, P2.5 |
| R-06 / R-07 Wrong template, layout drift | M1.5, P4.3 (template hit-rate metric), P5.2 |
| R-08 Precision | M2.3, M4.1 |
| R-09 Credential leakage | M3.2, P2.1, P4.5 |
| R-10 Audit tampering | M3.1, P1.2, P4.4 |
| R-11 Few real slips | P5.1 |
| R-12 SQLite limits | P1 |
| R-13 Real data in git | P4.2 (pre-commit check) |

---

## 7. Definition of Done

A task or feature is done when all of the following hold:

1. Behaviour matches the baseline names and values (statuses, endpoints, tables, env vars, fields).
2. Acceptance criterion of the linked FR/NFR is met and covered by an automated test.
3. All tests pass (unit, golden, API, audit, auth); golden output for the 4 mocks unchanged unless intentionally updated.
4. Money and rates are `Decimal` / strings; dates ISO; errors RFC 7807.
5. Every state change writes an audit row in the same transaction; `GET /api/v1/audit/verify` passes.
6. Endpoint visible in Swagger with summary, tag, request/response example and error responses.
7. New settings are `BONDS_*` env vars documented in `.env.example`.
8. No secrets or real slip data in code, logs, tests or git.
9. Code reviewed by a second person (after MVP) and merged via CI.
10. Docs updated where behaviour or contract changed.

---

## 8. Go-live checklist

| # | Item | Owner | Check |
|---|---|---|---|
| 1 | PostgreSQL provisioned; `alembic upgrade head` applied; `audit_log` triggers and restricted role in place | DevOps | [ ] |
| 2 | `BONDS_DATABASE_URL`, `BONDS_DATA_DIR`, thresholds and limits set per B§9; `BONDS_DOCS_ENABLED` decided | DevOps | [ ] |
| 3 | HTTPS only via reverse proxy; HTTP blocked; CORS set to frontend origin(s) | DevOps | [ ] |
| 4 | Production clients created via CLI; secrets delivered securely; test clients disabled | DevOps | [ ] |
| 5 | Brute-force limiter enabled and tested | Backend dev | [ ] |
| 6 | Templates for all go-live sources approved and exported as JSON backup | Ops SME | [ ] |
| 7 | Golden tests include masked real slips for each go-live source; all green | QA | [ ] |
| 8 | Performance: p95 ≤ 3 s for 1–5 page text PDFs | QA | [ ] |
| 9 | Parallel run: ≥ 95 % auto-`PARSED` on known templates; 0 wrong unflagged values | Ops SME | [ ] |
| 10 | UAT signed off | Project lead | [ ] |
| 11 | VAPT: no open Critical/High findings | Security | [ ] |
| 12 | Backups scheduled; restore drill done; `audit/verify` passes after restore | DevOps | [ ] |
| 13 | Logs, metrics, alerts and Sentry live; `/health` monitored | DevOps | [ ] |
| 14 | Retention: `BONDS_AUDIT_RETENTION_YEARS` set; archive job scheduled | DevOps | [ ] |
| 15 | Holiday calendar loaded for the current and next year | Ops SME | [ ] |
| 16 | Runbook: start/stop, rotate secret, onboard template, handle `NEEDS_REVIEW` / `UNREADABLE` / `FAILED` | Project lead | [ ] |
| 17 | Downstream systems consume only `PARSED` / `APPROVED` automatically | Integration owner | [ ] |
| 18 | Rollback plan: manual keying remains available for the first 2 weeks | Project lead | [ ] |
| 19 | Go/no-go meeting held and decision recorded | Project lead | [ ] |

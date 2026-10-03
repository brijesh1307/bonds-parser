"""Generate the master task plan (docs/14_task_breakdown.md + docs/task_breakdown.csv).

The task data lives here once; both outputs are generated so they never disagree.
Estimates are ideal hours for one developer working by hand.

Usage:  python tools/build_task_plan.py
"""

from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MD_OUT = ROOT / "docs" / "14_task_breakdown.md"
CSV_OUT = ROOT / "docs" / "task_breakdown.csv"

D = 8  # hours per day

# Phase: (id, title, release, maps_to (12_project_plan), goal, tasks)
# Task:  (id, title, refs, depends, done_when, subtasks)
# Sub:   (id, title, refs, hours)
PHASES = [
    ("PH0", "Project setup & tooling", "v0.0.1", "M1.1", "A clean, protected repo with lint, types, tests and CI running before any feature code.", [
        ("T0.1", "Repository hygiene", "13§4, 13§11", "-", "`master` protected; every task in this plan is an issue on the board", [
            ("0.1.1", "Commit docs/config; merge `bonds-parser` → `master`; add remote; protect `master` (CI + 1 review)", "13§4.1", 0.5),
            ("0.1.2", "Import `docs/task_breakdown.csv` into the tracker; add labels and board columns", "13§9", 0.5),
            ("0.1.3", "PR template and CODEOWNERS", "13§4.3", 0.25),
        ]),
        ("T0.2", "Developer tooling", "13§3, 10§12", "T0.1", "`pre-commit run --all-files` passes", [
            ("0.2.1", "`pyproject.toml`: ruff, mypy, pytest settings", "13§5 It0", 0.5),
            ("0.2.2", "`.pre-commit-config.yaml`: ruff, ruff-format, mypy, block files under `samples/real/`", "10§12, R-13", 0.25),
            ("0.2.3", "Install `requirements-dev.txt` in `.venv`; verify versions", "11§1.2", 0.25),
        ]),
        ("T0.3", "Application skeleton", "B§8, 03§1", "T0.2", "`import app.main` works; settings show B§9 defaults", [
            ("0.3.1", "Package tree `app/{api,schemas,services,engine,engine/profiles,export,db}`", "B§8", 0.25),
            ("0.3.2", "`app/config.py`: `Settings.from_env()` with all `BONDS_*` defaults", "03§3.2, B§9, NFR-21", 0.5),
            ("0.3.3", "`app/errors.py`: exception hierarchy mapped to HTTP codes", "03§8, B§7", 0.5),
            ("0.3.4", "`tests/test_architecture.py`: engine imports no FastAPI/SQLAlchemy/services/db", "13§1, NFR-16", 0.25),
        ]),
        ("T0.4", "Continuous integration", "13§7, 10§11", "T0.3", "CI green on every push", [
            ("0.4.1", "`.github/workflows/ci.yml`: ruff → mypy → pytest → pip-audit", "13§7", 0.5),
        ]),
    ]),
    ("PH1", "Walking skeleton", "v0.0.2", "M1.2, M1.3 (part)", "Mock slip 01 goes PDF → `POST /api/v1/parse` → JSON through every layer.", [
        ("T1.1", "Canonical fields", "B§5, 05§1, 03§2.1", "T0.3", "`\"Ticket No\"` resolves to `deal_id`; field list equals B§5", [
            ("1.1.1", "Enums: DealType, InstrumentType, BuySell, DayCount, BidType, Market, SlipStatus, Method", "03§2.1", 0.5),
            ("1.1.2", "`FIELDS`, `REPO_FIELDS` with value types; required fields by deal type", "B§5", 0.5),
            ("1.1.3", "Label synonym map and `norm_label` (full and parentheses-stripped keys)", "deal_slip_standard_formats§4, 03§4.4", 1),
            ("1.1.4", "Unit tests for label lookups", "10§3", 0.5),
        ]),
        ("T1.2", "Engine data structures", "03§2.2", "T1.1", "Dataclasses importable and typed (mypy clean)", [
            ("1.2.1", "KeyValue, ExtractedDocument, FieldValue, TemplateDefinition, MarketProfile, EngineResult", "03§2.2", 0.75),
        ]),
        ("T1.3", "Normalisers (core)", "03§4.10–4.11, 05§8, FR-11", "T1.1", "`\"5,00,00,000.00\"` → 50000000.00; `\"24-Sep-2026\"` → 2026-09-24", [
            ("1.3.1", "`parse_date`: DMY numeric, month names, ISO", "03§4.10", 0.75),
            ("1.3.2", "`parse_amount`: Indian grouping, Cr / Lakh / Mn in value or label", "03§4.11, B§4", 0.75),
            ("1.3.3", "`parse_rate`, `parse_int`, `find_isin` + ISO 6166 check digit", "03§3.9", 0.5),
            ("1.3.4", "Table-driven unit tests + hypothesis property tests", "10§3, 10§4", 1),
        ]),
        ("T1.4", "Extraction: label|value tables", "03§4.1, FR-01, FR-14", "T1.2", "Mock 01 yields all 24 pairs with page + bbox", [
            ("1.4.1", "Open PDF, page sizes, text-layer check → `UNREADABLE`", "03§3.9, 03 A5", 0.5),
            ("1.4.2", "`find_tables` + cell bboxes → KeyValue(source=table)", "03§4.1", 1),
            ("1.4.3", "Test on mock 01", "10§3", 0.5),
        ]),
        ("T1.5", "Minimal pipeline + golden harness", "03§3.9, 10§5.1", "T1.3, T1.4", "Golden test for mock 01 green", [
            ("1.5.1", "Map by synonym (method `synonym`, confidence 0.95)", "B§4", 0.5),
            ("1.5.2", "Build deal, required-field check, provisional status", "B§3", 0.5),
            ("1.5.3", "Decimal-aware golden comparison; enable mock 01", "10§5.1", 0.75),
        ]),
        ("T1.6", "First endpoints", "04§4.1–4.2, FR-04, FR-29", "T1.5", "`/health` and `/api/v1/parse` work in Swagger", [
            ("1.6.1", "`app/main.py`: app factory, Swagger metadata, `GET /health`", "03§3.1", 0.5),
            ("1.6.2", "`POST /api/v1/parse` (JSON) + `export/json_export.py` (Decimal → string)", "04§4.2, 05§5.11", 1),
            ("1.6.3", "TestClient tests", "10§6", 0.25),
        ]),
        ("T1.7", "Debug tool", "13§3", "T1.4", "`python -m app.cli debug-extract <pdf>` prints every KeyValue", [
            ("1.7.1", "`cli debug-extract`", "13§3", 0.5),
        ]),
    ]),
    ("PH2", "Parser engine complete", "v0.0.3", "M1.3–M1.6", "All 4 mock slips match their expected JSON; market, validation and status work.", [
        ("T2.1", "Extraction: all layouts", "03§4.1–4.4", "T1.4", "Mocks 02, 03, 04 yield every expected pair", [
            ("2.1.1", "Table classifier: label|value, 4/6-column label|value, grid (`label_like` rule)", "03§4.1", 1),
            ("2.1.2", "Grids with one or many value rows (key = row label + header)", "03§4.3", 1),
            ("2.1.3", "ISIN overflow repair in grid cells", "03§4.2", 0.5),
            ("2.1.4", "Text outside tables: `Label: value`, `|` split, 10 pt gap pairs, signature-row skip", "03§4.4", 1.5),
            ("2.1.5", "Tests on mocks 02–04", "10§3", 0.5),
        ]),
        ("T2.2", "Label lookup: full chain", "03§4.5, 03§4.8, B§4", "T1.5", "Confidence values match the B§4 table", [
            ("2.2.1", "Abbreviation expansion (confidence 0.90)", "03§4.5", 0.5),
            ("2.2.2", "Fuzzy match (difflib ≥ 0.85, labels ≥ 4 chars)", "03§4.5", 0.5),
            ("2.2.3", "Candidate resolution order", "03§4.8", 0.5),
        ]),
        ("T2.3", "Market detection", "09§3–4, 03§4.9, FR-10", "T2.1", "4 mocks → `IN`; synthetic US slip → `NEEDS_REVIEW`", [
            ("2.3.1", "Collect signals: ISIN prefix, identifiers, currency, settlement system, platform, name style, number format, BIC", "09§3", 1.5),
            ("2.3.2", "Weighted vote, 0.70 cut-off, `issuer_country`, `slip_locale`", "09§3.1, B§4", 0.5),
            ("2.3.3", "`profiles/IN.json` + profile loader", "09§4.1", 0.75),
            ("2.3.4", "Tests", "10§3", 0.5),
        ]),
        ("T2.4", "Normalisers: enums", "FR-11, 03§3.9", "T1.3", "Every enum normaliser has passing unit tests", [
            ("2.4.1", "deal_type, buy_sell, instrument_type, platform, settlement_mode", "03§3.9", 1),
            ("2.4.2", "day_count, frequency, bid_type, broker (\"Direct\" → null)", "03§3.9", 0.5),
            ("2.4.3", "Unit tests", "10§3", 0.5),
        ]),
        ("T2.5", "Derivation rules", "03§4.12", "T2.4", "Goldens 03 and 04 green", [
            ("2.5.1", "Coupon / tenor from security name; currency", "03§4.12", 0.5),
            ("2.5.2", "Repo leg 1 → top-level fields; repo_days from dates", "03§4.12", 0.5),
            ("2.5.3", "buy_sell default by deal type; platform from text; auction counterparty; sign-off dealer", "03§4.12", 1),
        ]),
        ("T2.6", "Validation checks", "03§4.13, deal_slip_standard_formats§5, FR-12", "T2.5", "One failing-case test per check passes", [
            ("2.6.1", "ISIN check digit, date order, weekend settlement", "03§4.13", 0.5),
            ("2.6.2", "Principal, consideration, discount, quantity", "03§4.13", 0.75),
            ("2.6.3", "Repo legs with precision-scaled tolerance", "03§4.13, 03 A10", 0.5),
            ("2.6.4", "Tests", "10§3", 0.75),
        ]),
        ("T2.7", "Confidence and status decision", "B§3–4, 03§4.14–4.15, FR-13", "T2.6, T2.3", "All 4 goldens green (04 `maturity_date` exception)", [
            ("2.7.1", "Confidence per method; `low_confidence` list", "03§4.14", 0.5),
            ("2.7.2", "Status decision + table test", "03§4.15", 0.5),
            ("2.7.3", "Enable all goldens; document the 04 exception", "10§5.2", 0.5),
        ]),
    ]),
    ("PH3", "Templates (file-based; no slip storage)", "v0.1.0", "M2.1, M2.2, M3.3 (part)", "Upload → `NEW_TEMPLATE` → preview → approve → next slip of that layout `PARSED`.", [
        ("T3.1", "Database", "03§7, B§6", "T0.3", "`init-db` creates `data/bonds.db` with 5 tables", [
            ("3.1.1", "`db/database.py`: engine, session, SQLite pragmas", "03§7.2", 0.5),
            ("3.1.2", "`db/models.py`: clients, slips, templates, template_versions, audit_log", "03§7.1", 1.5),
            ("3.1.3", "`cli init-db`", "03§3.4", 0.25),
        ]),
        ("T3.2", "Templates in the engine", "03§4.6–4.7, FR-09", "T2.7", "Same labels score 1.0; region rule reads bbox text", [
            ("3.2.1", "Fingerprint, Jaccard, keyword gate, threshold 0.80", "03§4.6–4.7", 1),
            ("3.2.2", "Apply label_map, region_rules, regex_rules, constants, ignore, accepted_derived, market / date_order pin", "B§6, 08", 2),
            ("3.2.3", "Unit tests", "10§3", 1),
        ]),
        ("T3.3", "Template service", "03§3.8, FR-07, FR-08, FR-19", "T3.1, T3.2", "Approve twice → versions 1 and 2; UNIQUE enforced", [
            ("3.3.1", "`build_definition(spec, base)`", "03§3.8", 1),
            ("3.3.2", "Create v1 / add version; active-definition cache", "03§3.8", 1),
            ("3.3.3", "List / get", "04§4.14–4.15", 0.5),
        ]),
        ("T3.4", "Slip service", "03§3.8, 03§6.1–6.3, 03§9", "T3.3", "Service tests for upload, list, preview, approve pass", [
            ("3.4.1", "Receive upload: stream, SHA-256, 413 size, 415 magic bytes, 422 pages / encrypted", "07§4, FR-02", 1.5),
            ("3.4.2", "Create slip: pipeline outside transaction, store result, `duplicate_of`", "03§9.1–9.2", 1),
            ("3.4.3", "Get / list with filters and pagination", "04§2.4, FR-17", 0.75),
            ("3.4.4", "Preview with draft template (nothing stored)", "FR-06, 03§6.2", 0.75),
            ("3.4.5", "Approve: 422 missing, 409 validation, 409 state; template create / version; `APPROVED`", "FR-07, 03§6.3", 1.5),
        ]),
        ("T3.5", "Slip, template and schema API", "04§3, 04§4.3–4.9, 4.14–4.15, 4.20", "T3.4", "Flow test green", [
            ("3.5.1", "`schemas/*` Pydantic models", "03§3.7, 05§5", 1.5),
            ("3.5.2", "Routers: slips (upload, list, get, pdf, preview, approve), templates (list, get), schema/fields", "04§4", 1.5),
            ("3.5.3", "RFC 7807 error handlers", "04§2.6, FR-28", 0.5),
            ("3.5.4", "Flow test with a generated unknown-layout PDF", "10§6", 1),
        ]),
    ]),
    ("PH4", "Authentication & audit", "v0.1.0", "M3.1, M3.2", "Every endpoint except `/health` needs header credentials; every action is in a tamper-evident audit log.", [
        ("T4.1", "Audit service", "06§5, 03§4.16, FR-20", "T3.1", "UPDATE/DELETE on `audit_log` fail; verify OK; 06§6 test vector reproduces", [
            ("4.1.1", "Canonical JSON + SHA-256 chain (genesis = 64 zeros)", "06§5.3, 03§4.16", 1),
            ("4.1.2", "`record()` in the caller's transaction; `record_standalone()` for failures", "06§5.2", 0.75),
            ("4.1.3", "SQLite triggers blocking UPDATE / DELETE", "03§7.3", 0.5),
            ("4.1.4", "`verify()`", "06§5.4", 0.5),
            ("4.1.5", "Tests incl. test vector", "10§7", 0.75),
        ]),
        ("T4.2", "Audit wired into services", "06§3.1", "T4.1, T3.5", "Each action writes exactly one row", [
            ("4.2.1", "Slip events: UPLOADED, PARSED (incl. one-shot /parse), VIEWED, PDF_DOWNLOADED, PREVIEWED, APPROVED (field diff), EXPORTED", "06§3.1, B§7", 1),
            ("4.2.2", "Template events: CREATED, VERSION_ADDED", "06§3.1", 0.5),
        ]),
        ("T4.3", "Header authentication", "07§2, 03§3.3, FR-22", "T3.1, T4.1", "No header / bad secret / disabled → 401; valid → 200", [
            ("4.3.1", "HTTP Basic, Argon2 verify, constant-time, `last_used_at`, `WWW-Authenticate`", "07§2.1–2.5", 1),
            ("4.3.2", "`AUTH_FAILED` audit", "06§3.1", 0.25),
            ("4.3.3", "Auth dependency on every router except `/health`; Swagger security scheme", "04§5", 0.5),
            ("4.3.4", "Tests + route-coverage test", "10§8", 0.75),
        ]),
        ("T4.4", "Client management CLI", "07§2.2, FR-25", "T4.3", "Secret printed once; disabled client gets 401", [
            ("4.4.1", "`add-client`, `list-clients`, `disable-client`, `rotate-secret` + CLIENT_* audit", "07§2.2", 1),
        ]),
        ("T4.5", "Request context", "04§2.2, FR-24", "T4.3", "`X-Request-ID` echoed; actor name in audit; CORS allow-list", [
            ("4.5.1", "Request-ID middleware, `X-Actor-Name`, CORS, access log", "03§3.1, 03§9.4", 0.75),
        ]),
        ("T4.6", "Audit and history endpoints", "04§4.12, 4.23–4.24, FR-21, FR-27", "T4.2", "Flow test history shows the full timeline", [
            ("4.6.1", "`GET /audit` (filters), `GET /audit/verify`, `GET /slips/{id}/history`", "04§4.12, 4.23–4.24", 1),
        ]),
    ]),
    ("PH5", "Outputs & MVP release", "v0.1.0", "M2.3, M4.1–M4.4", "JSON, XML and Excel for every slip; documented Swagger; demo passes. **MVP (MS-0).**", [
        ("T5.1", "Exporters", "05§6–7, FR-15", "T3.5", "4 mocks × 3 formats open correctly; no floats", [
            ("5.1.1", "`xml_export`: `null=\"true\"`, `<item>` lists, tag sanitising", "05§6.1", 1),
            ("5.1.2", "`excel_export`: Deals / Key Values / Validation / Fields sheets, real dates and numbers, formula-injection guard", "05§7", 1.5),
            ("5.1.3", "`format=` on `/parse` and `/slips/{id}/export`; content types and file names", "04§4.2, 04§4.11", 0.5),
            ("5.1.4", "Tests", "10§6", 0.5),
        ]),
        ("T5.2", "Swagger polish", "04§5, NFR-19", "T5.1, T4.6", "Every MVP endpoint has tag, summary, examples, error responses", [
            ("5.2.1", "Tags, summaries, examples, error responses, Authorize", "04§5", 1),
        ]),
        ("T5.3", "README quick start", "11§1–2", "T5.2", "A new developer runs the demo from the README alone", [
            ("5.3.1", "Install, init-db, add-client, run, curl examples", "11§1–2", 0.5),
        ]),
        ("T5.4", "Demo and release", "12§2, 13§5 It5", "T5.3", "Demo runs clean; `v0.1.0` tagged", [
            ("5.4.1", "`tools/demo.py`: parse 4 mocks, onboard unknown layout, export, verify audit", "12 M4.4", 0.75),
            ("5.4.2", "CHANGELOG, tag `v0.1.0`, iteration review", "13§7", 0.5),
        ]),
    ]),
    ("PH6", "Storage & remaining endpoints", "v0.2.0", "P1", "Production database and every Later endpoint.", [
        ("T6.1", "PostgreSQL + migrations", "B§D4, 03§7, 12 P1.1–P1.3", "PH5", "Suite green on SQLite and PostgreSQL", [
            ("6.1.1", "Alembic baseline migration", "12 P1.1", 0.5 * D),
            ("6.1.2", "Postgres audit triggers + restricted role (INSERT/SELECT only)", "06§5.1, 12 P1.2", 0.5 * D),
            ("6.1.3", "psycopg driver, pool, `BONDS_DATABASE_URL` switch", "12 P1.3", 0.25 * D),
        ]),
        ("T6.2", "Slip endpoints (Later)", "04§4.4, 4.10, 4.13, FR-03, FR-18", "T6.1", "FR-03 and FR-18 acceptance met", [
            ("6.2.1", "`POST /slips/batch`", "04§4.4", 0.25 * D),
            ("6.2.2", "`POST /slips/{id}/reparse?force=` (409 without force on APPROVED)", "04§4.10", 0.25 * D),
            ("6.2.3", "`DELETE /slips/{id}` (audit row kept)", "04§4.13, 06§5.7", 0.25 * D),
        ]),
        ("T6.3", "Template endpoints (Later)", "04§4.16–4.19, FR-19, FR-27", "T6.1", "FR-19 and FR-27 acceptance met", [
            ("6.3.1", "`PUT` (new version), `PATCH` (enable/disable/rename, TEMPLATE_UPDATED)", "04§4.16–4.17", 0.5 * D),
            ("6.3.2", "`POST /templates/import`, `GET /templates/{id}/history` with version diffs", "04§4.18–4.19", 0.5 * D),
        ]),
        ("T6.4", "Combined exports", "04§4.22, 4.25, FR-16", "T6.1", "XML validates against XSD", [
            ("6.4.1", "`GET /exports/deals` (DEALS_EXPORTED)", "04§4.22", 0.5 * D),
            ("6.4.2", "`GET /audit/export` csv/xlsx/json (AUDIT_EXPORTED)", "04§4.25", 0.25 * D),
            ("6.4.3", "XSD for the XML output", "05§6", 0.25 * D),
        ]),
    ]),
    ("PH7", "Operations", "v0.3.0", "P4", "Deployable, observable and recoverable. Can run in parallel with PH8.", [
        ("T7.1", "Containers", "11§4, 12 P4.1", "T6.1", "`docker compose up` serves `/health` 200", [
            ("7.1.1", "Dockerfile (non-root, slim) + docker-compose (API + Postgres)", "11§4", 0.5 * D),
        ]),
        ("T7.2", "CI/CD", "13§7, 12 P4.2", "T7.1", "Merge to main deploys to Dev; tags deploy UAT/Prod with approval", [
            ("7.2.1", "Postgres test matrix; image build; deploy to Dev + smoke test", "13§7", 0.5 * D),
            ("7.2.2", "Tag pipeline: UAT / Prod with manual approval", "13§7", 0.5 * D),
        ]),
        ("T7.3", "Observability", "11§5.2, 11§7, NFR-15", "T7.1", "Dashboards: p95 latency, status counts, template hit rate", [
            ("7.3.1", "structlog JSON logs with request id", "03§9.4", 0.25 * D),
            ("7.3.2", "Prometheus metrics + Sentry", "11§7", 0.75 * D),
        ]),
        ("T7.4", "Backups, retention", "11§5.3–5.6, 06§5.8, NFR-12", "T7.1", "Restore drill passes; audit verifies after restore", [
            ("7.4.1", "DB + uploads backup, restore drill", "11§5.3", 0.5 * D),
            ("7.4.2", "Audit archive job (AUDIT_ARCHIVED), scheduled `audit/verify`", "06§5.8, 11§5.5", 0.5 * D),
        ]),
        ("T7.5", "Transport security", "07§3, 07§6, NFR-06", "T7.1", "TLS-only access verified", [
            ("7.5.1", "Reverse proxy TLS, HSTS, security headers, CORS to frontend, docs disabled in prod", "07§3, 07§6", 0.5 * D),
        ]),
    ]),
    ("PH8", "Hardening", "v0.4.0", "P2", "Scans, locked PDFs, multi-deal PDFs, brute force and performance handled.", [
        ("T8.1", "Brute-force limiter", "07§2.6, FR-23", "PH5", "FR-23 acceptance met", [
            ("8.1.1", "Per-IP failure window, 429 + Retry-After, AUTH_BLOCKED", "07§2.6", 0.5 * D),
        ]),
        ("T8.2", "OCR for scanned slips", "FR-14, R-03", "PH5", "Scanned mock → `NEEDS_REVIEW` with correct values", [
            ("8.2.1", "ocrmypdf / Tesseract path for `UNREADABLE`", "FR-14", 1.5 * D),
            ("8.2.2", "OCR confidence capped below threshold; tests", "B§4", 0.5 * D),
        ]),
        ("T8.3", "Password-protected PDFs", "FR-30, R-01", "PH5", "FR-30 acceptance met", [
            ("8.3.1", "pikepdf decrypt with per-sender passwords held securely", "FR-30, 07§5", 1 * D),
        ]),
        ("T8.4", "Multi-deal PDFs", "FR-31, R-02, 05§5.10", "T6.1", "FR-31 acceptance met", [
            ("8.4.1", "Detect repeating deal rows; split into one deal per row", "FR-31", 1 * D),
            ("8.4.2", "API returns a list of deals per file; tests", "05§5.10", 0.5 * D),
        ]),
        ("T8.5", "Holiday calendar & date ambiguity", "09§5, R-05", "PH5", "Holiday settlement flagged; ambiguous dates lower confidence", [
            ("8.5.1", "Indian holiday calendar loader + validation warning", "09§5", 0.5 * D),
        ]),
        ("T8.6", "Robustness & performance", "NFR-01, NFR-02, NFR-09, 10§9", "PH5", "p95 ≤ 3 s; malformed PDFs rejected safely", [
            ("8.6.1", "Fuzz malformed / huge PDFs; parser time and memory limits", "NFR-09", 0.5 * D),
            ("8.6.2", "Performance test", "10§9", 0.5 * D),
        ]),
    ]),
    ("PH9", "Real formats, UAT & go-live", "v1.0.0", "P5", "Your real slip formats parse automatically; ops, security and audit sign off.", [
        ("T9.1", "Real slip collection", "samples/real/README, 12 P5.1", "-", "≥ 5 masked slips per top source", [
            ("9.1.1", "Collect and mask real slips per source", "12 P5.1", 3 * D),
        ]),
        ("T9.2", "Template onboarding", "08, 10§5.4, 12 P5.2", "T9.1, PH5", "Each source has an approved template and a replica regression test", [
            ("9.2.1", "Onboard templates via preview / approve", "08", 3 * D),
            ("9.2.2", "Synthetic replica fixtures + expected JSON per template", "10§5.4", 2 * D),
        ]),
        ("T9.3", "User acceptance", "12 P5.4", "T9.2", "UAT sign-off; no open Sev-1/Sev-2", [
            ("9.3.1", "UAT with back-office ops (frontend or Swagger)", "12 P5.4", 5 * D),
        ]),
        ("T9.4", "Parallel run", "NFR-03, NFR-04, 12 P5.5", "T9.3", "≥ 95 % auto-PARSED on known templates; 0 wrong unflagged values", [
            ("9.4.1", "10 business days manual keying vs parser, daily diff", "12 P5.5", 10 * D),
        ]),
        ("T9.5", "Security assurance", "07§9, NFR-10", "PH7", "No open Critical/High findings", [
            ("9.5.1", "VAPT and fixes", "07§9", 5 * D),
        ]),
        ("T9.6", "Go-live", "12§8, 11", "T9.4, T9.5", "Go-live checklist complete; `v1.0.0` tagged", [
            ("9.6.1", "Runbook hand-over, production clients, cut-over, go/no-go", "12§8, 11", 1 * D),
        ]),
    ]),
    ("PH10", "Additional market profiles", "v1.1.0", "P3", "US, UK and Eurobond slips parse with their own rules.", [
        ("T10.1", "Profile interface", "09§4, FR-26", "PH5", "`GET /schema/markets` lists profiles", [
            ("10.1.1", "Profile loader interface + `GET /api/v1/schema/markets`", "04§4.21", 0.5 * D),
        ]),
        ("T10.2", "US profile", "09§4.2", "T10.1", "US mock → `PARSED` with template", [
            ("10.2.1", "CUSIP check digit, month-first dates, 32nds prices (99-16+), UST, ACT/ACT", "09§4.2", 1 * D),
        ]),
        ("T10.3", "UK and Eurobond profiles", "09§4.3–4.4", "T10.1", "GB and INTL mocks → `PARSED` with template", [
            ("10.3.1", "GB: SEDOL, GILT, ACT/ACT", "09§4.3", 0.75 * D),
            ("10.3.2", "INTL: Eurobond, Common Code, 30E/360", "09§4.4", 0.75 * D),
        ]),
        ("T10.4", "Further markets on demand", "09§7", "T10.1", "Only when real slips exist", [
            ("10.4.1", "DE / JP profiles (BUND, JGB) using the 09§7 checklist", "09§7", 1 * D),
        ]),
    ]),
]


# --------------------------------------------------------------------------- status (update as work lands)

# Sub-task id -> (status, note). Status: done | partial | todo | dropped. Unlisted sub-tasks are done.
STATUS_AS_OF = "2026-10-02 (v0.1.0)"
STATUS: dict[str, tuple[str, str]] = {
    "0.1.1": ("partial", "pushed to GitHub master; local master merge and branch protection open (owner)"),
    "0.1.2": ("todo", "owner: import the CSV into the tracker"),
    "0.1.3": ("partial", "PR template done; CODEOWNERS needs GitHub usernames"),
    "3.1.1": ("dropped", "no database (ADR-0009): templates, clients and audit are files"),
    "3.1.2": ("dropped", "no database (ADR-0009)"),
    "3.1.3": ("dropped", "no database (ADR-0009)"),
    "3.4.2": ("dropped", "slips are never stored (ADR-0009)"),
    "3.4.3": ("dropped", "no stored slips to list (ADR-0009)"),
    "4.1.3": ("done", "append-only JSON-lines file + hash chain instead of SQLite triggers"),
    "6.1.1": ("dropped", "no database (ADR-0009)"),
    "6.1.2": ("dropped", "no database (ADR-0009)"),
    "6.1.3": ("dropped", "no database (ADR-0009)"),
    "6.2.1": ("dropped", "no stored slips (ADR-0009)"),
    "6.2.2": ("dropped", "no stored slips (ADR-0009)"),
    "6.2.3": ("dropped", "no stored slips (ADR-0009)"),
    "6.3.2": ("done", "import + GET /templates/{id}/history with per-version diffs"),
    "6.4.1": ("dropped", "no stored deals to export (ADR-0009)"),
    "6.4.2": ("done", "GET /api/v1/audit/export csv / xlsx / json"),
    "7.2.1": ("partial", "CI builds and runs the image; registry push and deploy open"),
    "7.2.2": ("todo", "needs hosting decision"),
    "7.3.1": ("partial", "access log with request id; JSON logging open"),
    "7.3.2": ("partial", "Prometheus /metrics done; Sentry / error tracking open"),
    "7.4.1": ("todo", "backup templates/ and data/audit/ (no slip data to back up)"),
    "7.4.2": ("partial", "verify-audit CLI and /audit/verify done; archive job open"),
    "7.5.1": ("partial", "CORS and docs toggle done; TLS reverse proxy open"),
    "8.1.1": ("done", "pulled forward into PH4"),
    **{sid: ("todo", "") for sid in ("8.2.1", "8.2.2", "8.3.1", "8.4.1", "8.4.2", "8.5.1", "8.6.1")},
    "8.6.2": ("done", "tests/perf + tools/benchmark.py: p95 0.44 s on the mock slips (target 3 s)"),
    **{sid: ("todo", "") for sid in ("9.1.1", "9.2.1", "9.2.2", "9.3.1", "9.4.1", "9.5.1", "9.6.1")},
    **{sid: ("todo", "") for sid in ("10.1.1", "10.2.1", "10.3.1", "10.3.2", "10.4.1")},
}
ICON = {"done": "✅", "partial": "🟡", "todo": "⬜", "dropped": "⛔"}
WEIGHT = {"done": 1.0, "partial": 0.5, "todo": 0.0}


def status_of(sid: str) -> tuple[str, str]:
    return STATUS.get(sid, ("done", ""))


def progress(subs: list[tuple[str, str, str, float]]) -> tuple[float, str]:
    """Share of effort done, ignoring dropped sub-tasks; and a phase-level status."""
    live = [(s[3], status_of(s[0])[0]) for s in subs if status_of(s[0])[0] != "dropped"]
    if not live:
        return 1.0, "dropped"
    total = sum(h for h, _ in live)
    share = sum(h * WEIGHT[st] for h, st in live) / total
    return share, "done" if share == 1.0 else "todo" if share == 0.0 else "partial"


def fmt_h(h: float) -> str:
    return f"{h:g} h" if h < D else f"{round(h / D, 1):g} d"


def main() -> None:
    rows = []
    md = [
        "# 14 · Task Breakdown (master plan)",
        "",
        "> **Update 2026-10-02 — stateless design ([ADR-0009](adr/0009-stateless-api-no-slip-storage.md)).**",
        "> Deal slips are never stored and there is no database; tasks that needed SQLite, PostgreSQL,",
        "> Alembic, a `slips` table or `/api/v1/slips/*` are marked ⛔ dropped.",
        "",
        "Every phase, task and sub-task needed to build and ship the system, each linked to the",
        "document section and requirement it implements. Generated by `tools/build_task_plan.py`",
        "(edit the script, not this file); the same data is in `docs/task_breakdown.csv` for import",
        "into Jira / GitHub Projects.",
        "",
        "**Reference keys:** `B§` = `00_design_baseline.md`; `03§4.2` = `03_lld.md` §4.2; `04§4.9` =",
        "`04_api_spec.md` §4.9 (same pattern for 05–13); `FR-`/`NFR-`/`R-` = `01_requirements.md`;",
        "`12 P1.1` = task in `12_project_plan.md`. Estimates are ideal hours for one developer coding",
        "by hand (1 d = 8 h).",
        "",
        f"**Status as of {STATUS_AS_OF}:** ✅ done · 🟡 partly done · ⬜ to do · ⛔ dropped (stateless design).",
        "Progress is the share of estimated effort done, ignoring dropped sub-tasks.",
        "",
        "## Summary",
        "",
        "| Phase | Title | Release | Status | Progress | Sub-tasks ✅ / 🟡 / ⬜ / ⛔ | Effort |",
        "|---|---|---|---|---|---|---|",
    ]
    grand = mvp = 0.0
    for pid, title, rel, _maps, _goal, tasks in PHASES:
        subs = [s for t in tasks for s in t[5]]
        hours = sum(s[3] for s in subs)
        grand += hours
        if pid in {"PH0", "PH1", "PH2", "PH3", "PH4", "PH5"}:
            mvp += hours
        share, st = progress(subs)
        counts = [sum(1 for s in subs if status_of(s[0])[0] == k) for k in ("done", "partial", "todo", "dropped")]
        md.append(f"| {pid} | {title} | `{rel}` | {ICON[st]} | {share:.0%} | {' / '.join(map(str, counts))} "
                  f"| {fmt_h(hours)} |")
    md += ["", f"**MVP (PH0–PH5): {fmt_h(mvp)}** of development. **All phases: {fmt_h(grand)}** of work "
               "(PH9 includes elapsed activities such as UAT and the parallel run, done mostly by ops and QA).", ""]
    md += ["Order: PH0 → PH1 → PH2 → PH3 → PH4 → PH5 (MVP) → PH6 → PH7 ∥ PH8 → PH9 (go-live) → PH10.", ""]

    for pid, title, rel, maps, goal, tasks in PHASES:
        subs_all = [s for t in tasks for s in t[5]]
        hours = sum(s[3] for s in subs_all)
        share, st = progress(subs_all)
        md += ["---", "", f"## {pid} · {title} — `{rel}` · {fmt_h(hours)} · {ICON[st]} {share:.0%}", "",
               f"**Goal:** {goal}  ", f"**Maps to:** `12_project_plan.md` {maps}", ""]
        rows.append([pid, "", "Phase", title, "", "", f"{hours:g}", "", goal, rel, st, f"{share:.0%}"])
        for tid, ttitle, trefs, deps, done, subs in tasks:
            th = sum(s[3] for s in subs)
            tshare, tst = progress(subs)
            md += [f"### {tid} · {ttitle} ({fmt_h(th)}) {ICON[tst]}", "",
                   f"Refs: {trefs} · Depends on: {deps} · **Done when:** {done}", "",
                   "| ID | Sub-task | Refs | Est. | Status |", "|---|---|---|---|---|"]
            rows.append([tid, pid, "Task", ttitle, trefs, deps, f"{th:g}", "", done, rel, tst, f"{tshare:.0%}"])
            for sid, stitle, srefs, sh in subs:
                sst, note = status_of(sid)
                md.append(f"| {sid} | {stitle} | {srefs} | {fmt_h(sh)} | {ICON[sst]}{' ' + note if note else ''} |")
                rows.append([sid, tid, "Sub-task", stitle.replace("`", ""), srefs, "", f"{sh:g}", "", note, rel, sst, ""])
            md.append("")

    md += ["---", "", "## How to use this plan", "",
           "1. Import `docs/task_breakdown.csv` into your tracker (one issue per row; `Parent` gives the hierarchy).",
           "2. Work phase by phase; inside a phase follow the task dependencies.",
           "3. Mark a sub-task done only when its tests pass; close a task only when its **Done when** holds and",
           "   the Definition of Done in `12_project_plan.md` §7 is met.",
           "4. End each phase with its release tag and an iteration review (`13_development_plan.md` §9).",
           "5. To change the plan or a status, edit `tools/build_task_plan.py` (`PHASES`, `STATUS`) and run it again.",
           ""]

    MD_OUT.write_text("\n".join(md), encoding="utf-8")
    with CSV_OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ID", "Parent", "Type", "Title", "Refs", "Depends on", "Estimate (h)", "Owner", "Done when / note",
                    "Release", "Status", "Progress"])
        w.writerows(rows)
    print(f"wrote {MD_OUT.relative_to(ROOT)} and {CSV_OUT.relative_to(ROOT)}: "
          f"{len(rows)} rows, MVP {fmt_h(mvp)}, total {fmt_h(grand)}")


if __name__ == "__main__":
    main()

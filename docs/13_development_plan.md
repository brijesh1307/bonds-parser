# 13 · Development Plan

How the system is **built**: environments, tooling, branching, coding standards, the build order
(iterations), testing and CI/CD gates, reviews and release. *What* is built and *when* is in
`12_project_plan.md`; names and values come from `00_design_baseline.md` (B§).

---

## 1. Approach

| Principle | In practice |
|---|---|
| **Walking skeleton first** | Day 1 ends with one mock slip going PDF → `/api/v1/parse` → JSON through every layer. Each later iteration widens that path; nothing is built in isolation for days. |
| **Vertical slices** | Every iteration delivers something callable in Swagger, with tests — not "all models, then all services, then all routes". |
| **Golden files drive the engine** | `samples/mock/expected/*.json` are the acceptance tests. Write/enable the golden test first, then make it pass. |
| **Baseline is the contract** | Any change to a status, endpoint, table, field or env var starts with a PR to `00_design_baseline.md` (+ ADR if it's a decision). |
| **Engine stays pure** | `app/engine/*` never imports FastAPI, SQLAlchemy or `app/services`. A test enforces it. |
| **Small PRs** | ≤ ~400 changed lines, one task ID each, merged the same or next day. |

### Realistic effort

The 2-hour MVP in `12_project_plan.md` §2 assumes AI-assisted development with the design already
done. A developer writing it by hand should plan **about 8 working days** for the same scope
(§5 below). Both paths use the same iterations and done-criteria.

---

## 2. Environments

| Env | Purpose | Database | Files | Auth clients | Swagger | Deployed by |
|---|---|---|---|---|---|---|
| **Local** | Development | SQLite `data/bonds.db` | `data/uploads/` | `dev` (created via CLI) | on | developer |
| **Dev** | Shared integration with the frontend project | PostgreSQL | shared volume | `frontend-dev` | on | CI on merge to `main` |
| **UAT** | Ops testing with masked real slips, parallel run | PostgreSQL | shared volume | `frontend-uat`, per-system clients | on (internal) | CI on release tag, manual approve |
| **Prod** | Live | PostgreSQL (restricted audit role) | encrypted storage | production clients only | `BONDS_DOCS_ENABLED=false` (decision) | CI on release tag, manual approve |

Rules: no real slips in Local or Dev; templates move between environments only with
`POST /api/v1/templates/import` (exported JSON reviewed in a PR); every environment has its own
client secrets.

---

## 3. Developer setup

```bash
python -m venv .venv
.venv\Scripts\activate                 # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt    # runtime + ruff, mypy, pytest-cov, hypothesis, pip-audit
copy .env.example .env                 # Linux/macOS: cp .env.example .env
pre-commit install                     # after Iteration 0 adds .pre-commit-config.yaml
python -m app.cli init-db
python -m app.cli add-client dev       # prints the secret once
uvicorn app.main:app --reload          # http://127.0.0.1:8000/docs
```

### Everyday commands

| Task | Command |
|---|---|
| Run API | `uvicorn app.main:app --reload` |
| All tests | `pytest` |
| Fast loop (engine only) | `pytest tests/unit tests/golden -q` |
| Coverage | `pytest --cov=app --cov-report=term-missing` |
| Lint + format | `ruff check . --fix` and `ruff format .` |
| Types | `mypy app` |
| Dependency audit | `pip-audit -r requirements.txt` |
| Regenerate mock slips | `python tools/generate_mock_slips.py` |
| Inspect what pdfplumber sees | `python -m app.cli debug-extract samples/mock/01_gsec_outright_purchase.pdf` (Iteration 1) |

`debug-extract` prints every KeyValue with source, page and bbox. It is the main tool for
diagnosing a slip that parses wrongly.

---

## 4. Repository workflow

### 4.1 Branching — trunk-based

- `main` is always releasable and protected: CI green + 1 approval required, squash merge.
- Short-lived branches from `main`, deleted after merge:
  `feat/M1.3-extract-grids`, `fix/isin-overflow`, `docs/baseline-duplicates`, `chore/ci-postgres`.
- Branch name and PR title carry the task ID from `12_project_plan.md` (M…, P…).
- Releases are tags on `main`: `v0.1.0` (MVP), `v0.2.0` (P1), … `v1.0.0` (go-live).

> This repository's default branch is `master` (no remote yet). Work so far is on the worktree
> branch `bonds-parser`; merge it into `master` and add a GitHub remote to enable branch protection
> and CI. Read `main` below as `master` until the default branch is renamed.

### 4.2 Commits

Conventional Commits: `feat(engine): parse horizontal grids (M1.3)`, `fix(api): 409 on approve
with validation issues`, `docs: add ADR-0009`, `test(golden): add mock 04`, `chore(ci): …`.
The type drives the changelog.

### 4.3 Pull request checklist (`.github/pull_request_template.md`)

- [ ] Task ID in title; linked issue
- [ ] Tests added/updated; `pytest` green locally
- [ ] Golden outputs unchanged, or the change is intentional and explained
- [ ] No floats for money/rates; dates ISO; errors RFC 7807
- [ ] State change writes an audit row in the same transaction
- [ ] New endpoint has tag, summary, examples and error responses in Swagger
- [ ] New setting is a `BONDS_*` env var in `.env.example`
- [ ] Baseline / docs updated if a contract changed
- [ ] No secrets, no real slip data

---

## 5. Build order (iterations)

Each step lists the tests to write **first**. Estimates are for one developer coding by hand.

### Iteration 0 — Tooling (0.5 d) · `v0.0.1`

| Step | Files | Done when |
|---|---|---|
| 0.1 | `pyproject.toml` (ruff, mypy, pytest config), `.pre-commit-config.yaml` (ruff, ruff-format, mypy, end-of-file, block files under `samples/real/`) | `pre-commit run --all-files` passes |
| 0.2 | `app/` package skeleton per B§8 with empty modules; `app/config.py` (`Settings.from_env`) | `python -c "import app.main"` works; settings show B§9 defaults |
| 0.3 | `.github/workflows/ci.yml`: ruff → mypy → pytest → pip-audit | CI green on an empty test suite |
| 0.4 | `tests/test_architecture.py`: `app/engine` imports no `fastapi`, `sqlalchemy`, `app.api`, `app.services`, `app.db` | test green |

### Iteration 1 — Walking skeleton (1 d) · `v0.0.2`

Goal: mock 01 (G-Sec, 2-column table) parses end to end through the API.

| Step | Files | Tests first | Done when |
|---|---|---|---|
| 1.1 | `engine/fields.py` (fields, enums, required, synonyms) | synonym lookups (`"Ticket No"` → `deal_id`) | green |
| 1.2 | `engine/normalize.py` (dates, Indian amounts, rates, ISIN) | table-driven unit tests + hypothesis for amounts | green |
| 1.3 | `engine/extract.py` — label\|value tables only | KV list for mock 01 | all 24 rows extracted with bbox |
| 1.4 | `engine/mapping.py` (synonyms only), `engine/pipeline.py` (map → normalise → decide, no templates) | `tests/golden/test_mocks.py` enabled for 01 only | 01 matches expected |
| 1.5 | `app/main.py`, `api/health.py`, `api/parse.py` (JSON only, no auth yet), `export/json_export.py` | TestClient: `POST /api/v1/parse` with mock 01 → 200 | visible and working in `/docs` |
| 1.6 | `app/cli.py debug-extract` | – | prints KVs |

### Iteration 2 — Engine complete (2 d) · `v0.0.3`

| Step | Files | Tests first | Done when |
|---|---|---|---|
| 2.1 | `extract.py`: grids (single / multi value rows), 4-column label/value, ISIN overflow repair, text lines (colon, `\|`, gap), signature-row skip | golden 02, 04 | KVs complete |
| 2.2 | `engine/market.py` + `profiles/IN.json` | IN mocks → `IN`; synthetic US ISIN + USD → not `IN`, status `NEEDS_REVIEW` | green |
| 2.3 | `mapping.py`: abbreviation expansion, fuzzy, method + confidence per B§4 | confidence table test | green |
| 2.4 | `engine/derive.py` (coupon/tenor from name, repo leg → top level, buy/sell, platform, auction counterparty, sign-off dealer) | golden 03 | green |
| 2.5 | `engine/validate.py` (all checks + tolerances from LLD) | one failing case per check | green |
| 2.6 | `pipeline.py` status decision per B§3 | table test of status inputs → status | all 4 goldens green (04 `maturity_date` exception documented) |

### Iteration 3 — Persistence, slips, templates (2 d) · `v0.0.4`

| Step | Files | Tests first | Done when |
|---|---|---|---|
| 3.1 | `db/database.py`, `db/models.py` (all B§6 tables), `cli init-db` | tables exist | `data/bonds.db` created |
| 3.2 | `engine/detect.py` (fingerprint, Jaccard, keywords), template rules in pipeline (label_map, region, regex, constants, `accepted_derived`) | unit: same labels → 1.0; region rule reads bbox text | green |
| 3.3 | `services/template_service.py` (build definition, create, new version) | approve twice → v1, v2 | green |
| 3.4 | `services/slip_service.py`: upload (limits, magic bytes, sha256, `duplicate_of`), get, list, preview, approve (422 / 409 rules) | API tests | green |
| 3.5 | `api/slips.py`, `api/templates.py` (list/get), `api/schema.py`; `schemas/*`; RFC 7807 handlers | **flow test**: unknown-layout PDF (generated in test) → `NEW_TEMPLATE` → preview → approve → 2nd slip → `PARSED` | green |

### Iteration 4 — Auth and audit (1.5 d) · `v0.0.5`

| Step | Files | Tests first | Done when |
|---|---|---|---|
| 4.1 | `services/audit_service.py` (canonical JSON, hash chain, same transaction), SQLite triggers | UPDATE/DELETE on `audit_log` raise; verify OK; test vector from `06_audit_trail.md` reproduces | green |
| 4.2 | Audit calls in every state-changing service method | each action writes exactly one row | green |
| 4.3 | `app/auth.py` (Basic, Argon2, `WWW-Authenticate`), `api/deps.py`; `cli add-client / list-clients / disable-client / rotate-secret` | 401 no header / bad secret / disabled; 200 valid; `AUTH_FAILED` audited | green |
| 4.4 | Protect all routers except `/health`; `X-Request-ID`, `X-Actor-Name`; CORS | test: every route in `app.routes` except `/health`, `/docs` requires auth | green |
| 4.5 | `api/audit.py` (list, verify), `GET /slips/{id}/history` | history of the flow test | green |

### Iteration 5 — Outputs and MVP release (1 d) · `v0.1.0` = **MVP**

| Step | Files | Done when |
|---|---|---|
| 5.1 | `export/xml_export.py`, `export/excel_export.py`; `format=` on `/parse` and `/slips/{id}/export` | each mock opens in all 3 formats; no floats |
| 5.2 | Swagger polish: tags, summaries, examples, error responses, Authorize | every MVP endpoint documented |
| 5.3 | `README.md` quick start | a new developer runs the demo from README alone |
| 5.4 | Demo script `tools/demo.py` (or curl script): parse 4 mocks, onboard unknown layout, export, verify audit | runs clean |
| 5.5 | Tag `v0.1.0`, CHANGELOG | CI green on tag |

**MVP total ≈ 8 developer-days.**

### Iterations 6+ — after MVP

Map directly to `12_project_plan.md` §3, one iteration per phase, each ending in a tagged release:

| Iteration | Phase | Release |
|---|---|---|
| 6 | P1 Storage & remaining endpoints (Alembic, Postgres, batch/reparse/delete, template PUT/PATCH/import/history, exports/deals, audit export, XSD) | `v0.2.0` |
| 7 | P4 Operations (Docker, CI Postgres matrix, logs/metrics, backups, HTTPS) — can run in parallel with 8 | `v0.3.0` |
| 8 | P2 Hardening (limiter, OCR, password PDFs, multi-deal, holidays, fuzz) | `v0.4.0` |
| 9 | P5 Real templates + UAT + parallel run + VAPT | `v1.0.0` go-live |
| 10 | P3 Market profiles (US / GB / INTL) | `v1.1.0` |

---

## 6. Testing in the development loop

Full strategy: `10_testing_strategy.md`. What a developer does each day:

| When | Run | Gate |
|---|---|---|
| While coding the engine | `pytest tests/unit tests/golden -q` (seconds) | green |
| Before pushing | `pre-commit run --all-files` and `pytest` | green |
| In CI on every PR | lint, types, all tests, coverage, pip-audit | **engine coverage ≥ 90 %, overall ≥ 80 %**, no known-vulnerable dependency |
| After P1 | same suite on SQLite **and** PostgreSQL | both green |

**Golden-file rule:** a PR that changes any `samples/mock/expected/*.json` or template fixture must
say why in the description, and the reviewer checks the diff field by field.

**Onboarding a real format** (from P5): masked slip stays in `samples/real/` (git-ignored); a
**synthetic replica** with the same labels and layout goes to `tests/fixtures/templates/<id>/`
with `expected.json`, and the approved template JSON goes next to it.

---

## 7. CI/CD pipeline

```
PR opened / updated
  └─ lint (ruff) → types (mypy) → unit + golden → API/audit/auth tests → coverage gate → pip-audit
merge to main
  └─ same checks → build Docker image (from P4) → deploy to Dev → smoke test (/health, parse mock 01)
tag vX.Y.Z
  └─ build + sign image → deploy UAT (manual approval) → smoke test
  └─ deploy Prod (manual approval, change ticket) → smoke test → audit/verify
```

Versioning: SemVer. A breaking change to a `/api/v1` response is not allowed. It needs `/api/v2`
or an additive change. CHANGELOG generated from Conventional Commits.

---

## 8. Code review checklist

| Area | Reviewer checks |
|---|---|
| Correctness | Golden outputs; edge cases (missing fields, empty tables, two dates on a line) |
| Money | `Decimal` end to end; quantisation matches the slip; no `float()` |
| Layering | Engine pure; routers thin; business rules in services |
| Transactions | Business write + audit row in one transaction; no DB transaction held during PDF parsing |
| Errors | Mapped to the B§7 status table; RFC 7807 body; no stack traces or slip values in responses |
| Security | Auth dependency on the route; file checks before parsing; nothing sensitive logged |
| API contract | Pydantic models only; Swagger examples; no breaking change |
| Tests | New behaviour tested at the right level; no real data |

---

## 9. Task tracking

- One issue per task ID (`M1.3`, `P2.2`, …) with the "done when" from `12_project_plan.md` as its
  acceptance criterion.
- Labels: `engine`, `api`, `db`, `auth`, `audit`, `export`, `docs`, `ops`, `template`, `bug`.
- Board columns: Backlog → Ready → In progress → In review → Done (merged + deployed to Dev).
- Iteration review at the end of each iteration: demo in Swagger, tag release, update
  `12_project_plan.md` milestones.

---

## 10. Development risks

| Risk | Mitigation |
|---|---|
| pdfplumber output differs from expectation (merged cells, overflow, split lines) | `debug-extract` first; add the case as a unit test before fixing |
| Engine heuristics overfit the 4 mocks | Flow test uses a generated unknown layout; add real-format replicas early in P5 |
| Scope creep into frontend / RBAC | Out of scope per B§1; park in backlog |
| Docs drift from code | Baseline-first rule; PR checklist item; review docs at each iteration review |
| SQLite locking in API tests | One Uvicorn worker locally; tests use a temp DB per test module |
| Long parse times block requests | No DB transaction during parsing; batch/OCR move to a worker in P2 |

---

## 11. Day-one checklist

1. Merge `bonds-parser` into `master`; add a GitHub remote; protect `master`.
2. Create issues for M1.1 – M4.4 (or Iterations 0–5 steps).
3. Complete Iteration 0 (tooling + CI) before any feature code.
4. Put 1–3 masked real slips in `samples/real/` so real layouts are known early.
5. Confirm the open decisions: downstream consumer of the output; password-protected or
   multi-deal PDFs in your slips; production hosting.

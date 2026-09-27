# Bonds Deal Slip Parser – Documentation

A Python REST API (FastAPI + Swagger) that parses bond deal slip PDFs into one canonical deal
and returns it as JSON, XML or Excel. New slip layouts are onboarded once through the API and
then parse automatically. Every action is audited. Access uses client credentials in the header.

## Reading order

| If you are… | Read |
|---|---|
| New to the project | `00` → `01` → `02` → `12` → `14` |
| Building the backend | `14` → `13` → `00` → `02` → `03` → `05` → `04` → `10` |
| Building the frontend (separate project) | `04` (frontend integration section) → `05` → `08` |
| Onboarding a new slip format | `08` → `deal_slip_standard_formats` → `09` |
| Running / operating it | `11` → `07` → `06` |
| Auditor / security reviewer | `07` → `06` → `adr/` |

## Documents

| # | Document | What it answers | Type |
|---|---|---|---|
| 00 | [Design baseline](00_design_baseline.md) | The agreed names, statuses, endpoints, tables, env vars. **Single source of truth.** | Reference |
| — | [Deal slip standard formats](deal_slip_standard_formats.md) | What bond deal slips in the market look like, the canonical fields, label synonyms, validation rules | Domain reference |
| 01 | [Requirements](01_requirements.md) | What the system must do (functional + non-functional), scope, risks, glossary | SRS |
| 02 | [High-Level Design](02_hld.md) | Architecture, components, data flows, deployment, **system design principles applied** | HLD |
| 03 | [Low-Level Design](03_lld.md) | Every module, class, function signature, algorithm, DB schema, sequence diagrams, state machine | LLD |
| 04 | [API specification](04_api_spec.md) | Every endpoint: purpose, parameters, examples, errors; Swagger usage; frontend integration | API |
| 05 | [Data dictionary & outputs](05_data_dictionary_and_outputs.md) | Every output field; JSON / XML / Excel formats with full examples | Reference |
| 06 | [Audit trail](06_audit_trail.md) | Audit table, event catalogue, hash chain, tamper detection, auditor queries | Design |
| 07 | [Security](07_security.md) | Threat model, header auth, file safety, data protection, OWASP mapping, go-live checklist | Design |
| 08 | [Template onboarding guide](08_template_onboarding_guide.md) | Step-by-step: take a new slip format from `NEW_TEMPLATE` to auto-`PARSED` | How-to |
| 09 | [Market profiles](09_market_profiles.md) | How other countries' slips are detected and parsed (IN, US, GB, INTL) | Design |
| 10 | [Testing strategy](10_testing_strategy.md) | Test levels, golden files, API/audit/security tests, CI | Plan |
| 11 | [Runbook](11_runbook.md) | Install, configure, run, use with curl/Swagger, deploy, back up, troubleshoot | How-to |
| 12 | [Project plan](12_project_plan.md) | Tasks and sub-tasks, 2-hour MVP list, roadmap, milestones, go-live checklist | Plan |
| 13 | [Development plan](13_development_plan.md) | How to build it: environments, setup, branching, build order (iterations), CI/CD, reviews | Plan |
| 14 | [Task breakdown](14_task_breakdown.md) | **Master plan:** every phase, task and sub-task with doc references, estimates, dependencies and done-criteria (CSV: `task_breakdown.csv`) | Plan |
| ADR | [Architecture decisions](adr/README.md) | Why each key technology/design choice was made and what was rejected | Decisions |

## Project files and what they are for

| Path | Purpose | When it exists |
|---|---|---|
| `app/main.py` | FastAPI app, Swagger metadata, CORS, routers, error handlers | MVP |
| `app/config.py` | Reads `BONDS_*` env vars | MVP |
| `app/auth.py` | HTTP Basic check, Argon2, brute-force limiter | MVP |
| `app/cli.py` | `init-db`, `add-client`, `list-clients`, `disable-client`, `rotate-secret` | MVP |
| `app/api/` | One router per tag: health, parse, slips, templates, schema, exports, audit | MVP (some endpoints later) |
| `app/schemas/` | Pydantic request/response models (these generate Swagger) | MVP |
| `app/services/` | Business logic: slips, templates, exports, audit | MVP |
| `app/engine/` | The parser: extract, market, detect, mapping, normalize, derive, validate, pipeline | MVP |
| `app/engine/profiles/` | Market profiles (`IN.json` in MVP; `US.json`, `GB.json`, `INTL.json` later) | MVP / later |
| `app/export/` | JSON, XML, Excel writers | MVP |
| `app/db/` | SQLAlchemy engine/session, models, (later) Alembic migrations | MVP |
| `tests/` | Engine, golden-file, API, audit and auth tests | MVP |
| `docs/` | This documentation | Now |
| `samples/mock/` | 4 mock slips + expected JSON (committed) | Now |
| `samples/real/` | Masked real slips (git-ignored) | When you add them |
| `tools/generate_mock_slips.py` | Regenerates mock slips + expected JSON | Now |
| `.env.example` | All configuration variables with defaults — copy to `.env` | Now |
| `requirements.txt` | Python dependencies | Now |
| `data/` | SQLite DB + uploaded PDFs (git-ignored, created at runtime) | Runtime |
| `README.md` | Quick start | MVP |
| `Dockerfile`, `docker-compose.yml` | Container build / local stack | After MVP |

## How to use these documents

1. **Change a decision** → edit `00_design_baseline.md` first, add or update an ADR, then update
   the documents that reference it. Never let two documents disagree.
2. **Start building** → follow `12_project_plan.md` (2-hour MVP list), using `03_lld.md` for
   signatures and `04_api_spec.md` for request/response shapes.
3. **Add a new slip format** → `08_template_onboarding_guide.md`; add its sample + expected JSON
   as a regression test (`10_testing_strategy.md`).
4. **Add a new country** → `09_market_profiles.md` checklist.
5. **Go live** → checklists in `07_security.md`, `11_runbook.md` and `12_project_plan.md`.

# ADR-0001: FastAPI for the REST API

**Status:** Accepted

## Context

The parser is consumed by a separate frontend project and by integrating scripts. It needs a
documented REST API (Swagger UI), typed request/response models for a large canonical deal schema,
multipart PDF upload, file downloads (PDF, Excel, XML) and consistent error responses. The team works
in Python, and the PDF tooling (pdfplumber, openpyxl) is Python. The MVP must be built in about two hours.

## Decision

Use **Python 3.11+, FastAPI, Pydantic v2 and Uvicorn** (baseline D1).

- Swagger UI at `/docs`, ReDoc at `/redoc`, OpenAPI at `/openapi.json` (D2); Swagger can be hidden with `BONDS_DOCS_ENABLED=false`.
- Business endpoints under `/api/v1`; `/health`, `/docs`, `/redoc` at root (D13).
- Errors as RFC 7807 `application/problem+json` via global exception handlers (D14).
- Routers stay thin; logic lives in `app/services/` and the pure `app/engine/`.

## Consequences

**Positive**
- OpenAPI and Swagger generated from code; the frontend can generate a client.
- Pydantic validates payloads (mapping, template definitions) and serialises `Decimal` as strings.
- Built-in `HTTPBasic` security scheme shows an "Authorize" button in Swagger.
- Dependency injection (`deps.py`) for DB session, current client, request id.
- Fast enough; horizontally scalable with multiple Uvicorn workers/containers.

**Negative**
- Parsing is CPU-bound; sync endpoints run in a thread pool, so heavy load needs more processes, not async.
- Pydantic v2 / FastAPI version coupling must be managed on upgrades.
- Framework-specific decorators; mitigated by keeping the engine framework-free.

## Alternatives considered

| Option | Why not |
|---|---|
| Flask + flask-smorest / apispec | OpenAPI and validation are add-ons; more boilerplate. |
| Django + DRF | Heavy for an API-only service; ORM/admin not needed. |
| Litestar | Capable, but smaller ecosystem and team familiarity. |
| Node.js / Java (Spring) | Team and PDF tooling are Python. |

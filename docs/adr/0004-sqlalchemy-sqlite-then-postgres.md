# ADR-0004: SQLAlchemy with SQLite for MVP, PostgreSQL in production

**Status:** Superseded by [ADR-0009](0009-stateless-api-no-slip-storage.md) (2026-10-02)

## Context

We store clients, slips, templates with versions and an append-only audit log. We need transactions
(business change and audit row commit together), unique constraints, triggers (block audit
UPDATE/DELETE) and JSON columns. The MVP must run with zero setup; production must support several
API instances writing concurrently.

## Decision

Use **SQLAlchemy 2.x** (baseline D4):

- MVP: SQLite at `data/bonds.db` (`BONDS_DATABASE_URL` default `sqlite:///./data/bonds.db`).
- Production: PostgreSQL via `psycopg[binary]`, same models; Alembic migrations after MVP.
- Tables: `clients`, `slips`, `templates`, `template_versions`, `audit_log` (baseline §6).
- PDFs are stored as files (`data/uploads/<slip_id>.pdf`, S3/NAS later), not in the DB (D5).
- Only portable SQL features in models; audit triggers written per dialect.

## Consequences

**Positive**
- `init-db` and run; no DB server for development or demos.
- Switch to PostgreSQL is a config change (`BONDS_DATABASE_URL`) plus migrations.
- Real ACID transactions for the audit-in-same-transaction rule.

**Negative**
- SQLite is single-writer; unsuitable for multiple API instances.
- Dialect differences (JSON, triggers, UUID, timezone handling) must be tested on both.
- No Alembic in MVP → schema changes before production mean recreating the MVP DB.

## Alternatives considered

| Option | Why not |
|---|---|
| PostgreSQL from day one | Setup overhead for MVP; kept for production. |
| MongoDB / document store | Weaker multi-document transactions; relational constraints needed. |
| Raw SQL / sqlite3 | More code, no portability to PostgreSQL. |
| Storing PDFs as BLOBs | Bloats DB and backups; object storage scales better. |

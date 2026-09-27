# ADR-0006: Append-only, hash-chained audit log

**Status:** Accepted

## Context

Deal data feeds treasury operations; auditors need to see who uploaded, parsed, previewed,
approved, exported or deleted what, and when — and to be able to prove the trail was not edited.
The audit must never disagree with the business data (no "approved without audit row").

## Decision

Keep an **append-only `audit_log` table with a SHA-256 hash chain** (baseline D9):

- Columns per baseline §6 (`event_id`, `occurred_at`, `actor_type`, `actor_id`, `actor_name`, `action`, `entity_type`, `entity_id`, `status_before`, `status_after`, `changes`, `details`, `request_id`, `ip_address`, `user_agent`, `outcome`, `error`, `prev_hash`, `row_hash`).
- `row_hash = SHA-256(prev_hash + canonical serialisation of the row)`; first row uses a fixed genesis value (**Assumption:** 64 zeros).
- Written by `audit_service` **in the same DB transaction** as the action it records.
- DB triggers block UPDATE and DELETE on `audit_log`.
- Failures are audited too (`outcome=FAILURE`, `error`), including `AUTH_FAILED` / `AUTH_BLOCKED`.
- `GET /api/v1/audit/verify` recomputes the chain; `GET /api/v1/audit` searches; `/api/v1/audit/export` downloads (after MVP).
- Deleting a slip keeps its audit rows. Retention `BONDS_AUDIT_RETENTION_YEARS` (8) via archive job.

## Consequences

**Positive**
- Any edit, deletion or reordering of rows breaks the chain and is detected by `verify`.
- Business state and audit are always consistent (single transaction).
- Self-contained: no extra infrastructure.

**Negative**
- Inserts serialise on reading the last `row_hash` (needs a lock / serialisable step in PostgreSQL).
- A DB superuser could disable triggers and rewrite the entire chain; mitigated by least-privilege DB roles and (**Assumption**, future) periodic anchoring of the chain head outside the DB.
- Canonical serialisation must stay stable forever (versioned if it ever changes).
- Table grows unbounded until archived.

## Alternatives considered

| Option | Why not |
|---|---|
| Application log files | Easy to edit/rotate away; not transactional with business data. |
| Audit columns on business tables only | Lose history; not tamper-evident. |
| External WORM storage / ledger DB (e.g. QLDB) | Extra infrastructure and cost; not transactional with our DB. |
| Event sourcing | Much larger design change than needed. |

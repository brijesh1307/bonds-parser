# ADR-0009: Stateless API — deal slips are never stored

**Status:** Accepted (2026-10-02). Supersedes ADR-0004 (SQLAlchemy / SQLite → PostgreSQL) and the
slip-storage parts of ADR-0006 (audit in a database table).

## Context

The original design stored every uploaded PDF (`data/uploads/`) and its parse result (`slips`
table) so that slips could be reviewed, approved, re-parsed and exported later. The business owner
decided that **deal slips must not be stored anywhere** — no database, no S3 / blob storage, no
files. Slips contain client names, PANs and deal values; not keeping them removes a whole class of
data-protection, retention and breach risk.

## Decision

- The API is stateless for slip data: the PDF is read into memory, parsed, and discarded at the
  end of the request. The result is returned (JSON / XML / Excel) and never kept.
- `/api/v1/slips/*` endpoints are dropped. Review is done by the frontend, which already holds the
  PDF the user uploaded: it calls `POST /api/v1/templates/preview` and `POST /api/v1/templates` with
  the **PDF + mapping** in one multipart request.
- There is **no database**. What must persist is kept in files:
  - templates: one JSON file per template in `BONDS_TEMPLATES_DIR`, all versions inside, written
    atomically, reviewable in git;
  - API clients: `BONDS_CLIENTS_FILE` (Argon2 hashes);
  - audit: append-only JSON-lines file `BONDS_AUDIT_FILE` with a SHA-256 hash chain, **metadata
    only** (no file names — they often contain client names — no deal values, no PANs).

## Consequences

Positive:
- No slip data at rest: nothing to encrypt, back up, retain, purge or leak.
- No database to run, migrate or secure; deployment is one container plus two small folders.
- Templates are plain JSON: reviewable in pull requests and easy to move between environments.

Negative:
- No server-side review queue, history per slip, re-parse or bulk export of past deals; the
  frontend (or a downstream system) must keep whatever it needs.
- No duplicate-upload detection (nothing to compare with). The SHA-256 is still written to the
  audit log, so duplicates can be found there.
- File stores assume one writer: run one API process per templates folder, or put the folder on a
  shared volume behind a single instance. Scaling out later needs a shared store (out of scope).

## Alternatives considered

- **Keep storing slips (original design)** — rejected by the business owner.
- **Store results but not PDFs** — still stores client data (names, PANs, amounts); rejected.
- **Database for templates only** — works, but adds a service to run for a few small files.

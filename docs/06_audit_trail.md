# 06 · Audit Trail Design

Source of truth: [`00_design_baseline.md`](00_design_baseline.md) (D9, D11, §6 `audit_log` + audit
actions, §9 `BONDS_AUDIT_RETENTION_YEARS`). Anything not stated in the baseline is marked
**(assumption)**. Endpoints: [`04_api_spec.md`](04_api_spec.md); security context:
[`07_security.md`](07_security.md).

---

## 1. Goals

| Goal | How |
|---|---|
| **Who** | `actor_type`, `actor_id` (authenticated `client_id`, `cli` OS user, or `system`), `actor_name` (`X-Actor-Name`, informational) |
| **What** | `action` (fixed catalogue §3), `entity_type` + `entity_id`, `outcome`, `error` |
| **When** | `occurred_at`, UTC, microseconds |
| **Where** | `ip_address`, `user_agent`, `request_id` (correlates with API response + app logs) |
| **Before / after** | `status_before`, `status_after`, `changes` (field-level diff), `details` (context) |
| **Tamper evidence** | Append-only (triggers / grants) + SHA-256 hash chain (`prev_hash` → `row_hash`) + `/audit/verify` |
| **Completeness** | Written in the **same DB transaction** as the action: no action without its audit row, no audit row for a rolled-back action (failures recorded separately, §5.2) |

---

## 2. `audit_log` table

| Column | Type (SQLite / Postgres) | Null | Meaning |
|---|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT / BIGINT IDENTITY | no | Insertion order; chain order |
| `event_id` | TEXT / UUID, UNIQUE | no | Globally unique event ID (safe to reference outside the DB) |
| `occurred_at` | TEXT / TIMESTAMPTZ | no | UTC time of the action, `YYYY-MM-DDTHH:MM:SS.ffffffZ` |
| `actor_type` | TEXT | no | `client` \| `system` \| `cli` |
| `actor_id` | TEXT | yes | `client_id`; OS user for CLI (assumption); `null` if an auth attempt had no parseable client ID |
| `actor_name` | TEXT | yes | `X-Actor-Name` header (not verified), max 100 chars |
| `action` | TEXT | no | One of the catalogue actions (§3) |
| `entity_type` | TEXT | no | `slip` \| `template` \| `client` \| `export` \| `audit` |
| `entity_id` | TEXT | yes | Slip UUID, template slug, `client_id`, export UUID; `null` for one-shot `/parse` (sha256 in `details`) |
| `status_before` | TEXT | yes | Slip status before the action |
| `status_after` | TEXT | yes | Slip status after the action |
| `changes` | JSON (TEXT / JSONB) | yes | Field-level diff `{"field": {"old": x, "new": y}}` (§5.6) |
| `details` | JSON (TEXT / JSONB) | yes | Action context (file name, template id/version, format, filters, reason) |
| `request_id` | TEXT | yes | `X-Request-ID` (given or generated); `null` for CLI |
| `ip_address` | TEXT | yes | Client IP (real IP behind trusted proxy); `null` for CLI |
| `user_agent` | TEXT | yes | `User-Agent` header, truncated to 512 chars (assumption) |
| `outcome` | TEXT | no | `SUCCESS` \| `FAILURE` |
| `error` | TEXT | yes | Error code + short message on `FAILURE` (no stack trace, no slip values) |
| `prev_hash` | CHAR(64) | no | `row_hash` of the previous row; genesis = 64 × `0` |
| `row_hash` | CHAR(64) | no | SHA-256 hex of this row (§5.3) |

**Indexes**

| Index | Purpose |
|---|---|
| `UNIQUE (event_id)` | Idempotency, external reference |
| `UNIQUE (prev_hash)` (assumption) | Prevents a forked chain if two writers race |
| `(entity_type, entity_id, id)` | Slip / template history |
| `(occurred_at)` | Date-range search, export, archive |
| `(actor_id, occurred_at)` | Per-client activity |
| `(action, occurred_at)` | e.g. all `AUTH_FAILED` in a period |
| `(request_id)` | Correlate with logs / support tickets |

---

## 3. Event catalogue

### 3.1 Audited actions (baseline §6)

| Action | Trigger | `entity_type` / `entity_id` | `status_before → after` | `changes` | `details` | Scope |
|---|---|---|---|---|---|---|
| `SLIP_UPLOADED` | `POST /slips`, `POST /slips/batch` | slip / slip id | — | — | `file_name, file_size, sha256, page_count, duplicate_of` | MVP |
| `SLIP_PARSED` | `POST /slips` (after ingest); `POST /parse` (entity `slip` / `null`, sha256 in `details`) | slip / id | `null → PARSED\|NEEDS_REVIEW\|NEW_TEMPLATE\|UNREADABLE\|FAILED` | — | `template_id, template_version, match_score, market, market_confidence, missing_required, validation_failed, unmapped_count` (`format` for `/parse`) | MVP |
| `SLIP_VIEWED` | `GET /slips/{id}` | slip / id | — | — | `status` | MVP |
| `SLIP_PDF_DOWNLOADED` | `GET /slips/{id}/pdf` | slip / id | — | — | `sha256` | MVP |
| `SLIP_PREVIEWED` | `POST /slips/{id}/preview` | slip / id | — | — | counts of `label_map` / rules / constants, `would_be_status`, `missing_required`, `validation_failed` | MVP |
| `SLIP_APPROVED` | `POST /slips/{id}/approve` | slip / id | `NEW_TEMPLATE\|NEEDS_REVIEW\|PARSED → APPROVED` | deal field diff (stored result vs approved) | `template_id, template_version, template_created, accept_validation_issues, accepted_issues` | MVP |
| `SLIP_REPARSED` | `POST /slips/{id}/reparse` | slip / id | `<old> → <new>` | deal field diff | `force`, old/new `template_id` + `template_version`, `match_score` | Later |
| `SLIP_DELETED` | `DELETE /slips/{id}` | slip / id | `<old> → null` | — | `file_name, sha256, deal_id` (kept so the deleted record stays identifiable) | Later |
| `SLIP_EXPORTED` | `GET /slips/{id}/export` | slip / id | — | — | `format, status, deal_id` | MVP |
| `DEALS_EXPORTED` | `GET /exports/deals` | `export` / generated UUID | — | — | `format, filters (status, from, to), row_count, slip_ids` | Later |
| `TEMPLATE_CREATED` | approve with `template_id=null` | template / slug | — | — | `name, version (1), source_slip_id, note, definition` (full) | MVP |
| `TEMPLATE_VERSION_ADDED` | approve with existing `template_id`; `PUT /templates/{id}` | template / slug | — | definition diff vs previous version (dotted paths, e.g. `label_map.stl dt`) | `version, source_slip_id, note, definition` | MVP (approve) / Later (PUT) |
| `TEMPLATE_ENABLED` | `PATCH /templates/{id}` `is_active=true` | template / slug | — | `{"is_active": {"old": false, "new": true}}` | — | Later |
| `TEMPLATE_DISABLED` | `PATCH /templates/{id}` `is_active=false` | template / slug | — | `{"is_active": {"old": true, "new": false}}` | — | Later |
| `TEMPLATE_IMPORTED` | `POST /templates/import` | template / slug | — | — | `name, version, definition` | Later |
| `AUTH_FAILED` | Any authenticated endpoint, bad/missing credentials | client / attempted `client_id` (or `null`) | — | — | `reason` (`MISSING_HEADER, MALFORMED, UNKNOWN_CLIENT, BAD_SECRET, CLIENT_DISABLED`), `method`, `path` | MVP |
| `AUTH_BLOCKED` | Limiter threshold reached (`BONDS_AUTH_MAX_FAILURES` in `BONDS_AUTH_WINDOW_SECONDS`) | client / last attempted id | — | — | `failures, window_seconds, block_seconds, blocked_until` | Later |
| `CLIENT_ADDED` | CLI `add-client` | client / client_id | — | — | `description` (never the secret) | MVP |
| `CLIENT_DISABLED` | CLI `disable-client` | client / client_id | — | `{"is_active": {"old": true, "new": false}}` | — | MVP |
| `CLIENT_SECRET_ROTATED` | CLI `rotate-secret` | client / client_id | — | — | `rotated_at` (never old/new secret or hash) | MVP |
| `AUDIT_EXPORTED` | `GET /audit/export` | audit / generated UUID | — | — | `format, from, to, row_count, last_row_hash` | Later |

`outcome=FAILURE` rows use the same action with `error` set (e.g. `SLIP_UPLOADED` + `FILE_TOO_LARGE`,
`SLIP_APPROVED` + `MISSING_REQUIRED_FIELDS`) (assumption). Template rename has no action in baseline §6 —
**open point**, proposed `TEMPLATE_UPDATED`.

### 3.2 Not audited (assumption)

List/search reads without record content of a single slip: `GET /health`, `GET /slips` (list),
`GET /templates*`, `GET /schema/*`, `GET /slips/{id}/history`, `GET /audit`, `GET /audit/verify`.
Rationale: they are high-volume and do not change or disclose individual records beyond what the
audited actions already cover. Revisit if auditors require read-logging of audit access.

---

## 4. Actor and context capture

- API: `actor_type="client"`, `actor_id` = authenticated `client_id`, `actor_name` = `X-Actor-Name`,
  `request_id`, `ip_address`, `user_agent` from the request (a FastAPI dependency builds an
  `AuditContext` once per request).
- CLI: `actor_type="cli"`, `actor_id` = OS user running the command (assumption), no IP/request ID.
- `system`: background work without a request (future async parsing, archive job).

---

## 5. Rules

### 5.1 Append-only

SQLite (created by `init-db`):

```sql
CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
```

PostgreSQL (production):

```sql
REVOKE ALL ON audit_log FROM bonds_app;
GRANT SELECT, INSERT ON audit_log TO bonds_app;
GRANT USAGE ON SEQUENCE audit_log_id_seq TO bonds_app;
-- defence in depth, also blocks the table owner outside maintenance:
CREATE FUNCTION audit_log_block() RETURNS trigger LANGUAGE plpgsql AS
$$ BEGIN RAISE EXCEPTION 'audit_log is append-only'; END $$;
CREATE TRIGGER audit_log_no_update_delete BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_block();
CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
  FOR EACH STATEMENT EXECUTE FUNCTION audit_log_block();
```

The app role (`bonds_app`) is not the table owner; migrations run as a separate owner role.
The ORM model exposes no update/delete path for `AuditLog`.

### 5.2 Same-transaction write

`audit_service.record(session, ctx, action, ...)` adds the row to the **caller's session**; the
business change and the audit row commit or roll back together.

```python
with session.begin():
    slip.status = "APPROVED"
    template_service.create_version(session, ...)          # TEMPLATE_CREATED row
    audit_service.record(session, ctx, "SLIP_APPROVED", entity=slip, before=..., after=..., changes=diff)
```

Failures: when the business transaction rolls back, a `FAILURE` row is written afterwards in its own
short transaction. `AUTH_FAILED` / `AUTH_BLOCKED` always use their own transaction (no business change).
If the audit insert itself fails, the action fails (`500`) — never "action without audit".

### 5.3 Hash chain

Fields hashed, in this **fixed order** (everything except `id`, `prev_hash`, `row_hash`):

```
event_id, occurred_at, actor_type, actor_id, actor_name, action, entity_type, entity_id,
status_before, status_after, changes, details, request_id, ip_address, user_agent, outcome, error
```

```python
HASHED_FIELDS = ["event_id", "occurred_at", "actor_type", "actor_id", "actor_name", "action",
                 "entity_type", "entity_id", "status_before", "status_after", "changes", "details",
                 "request_id", "ip_address", "user_agent", "outcome", "error"]
GENESIS = "0" * 64

def canonical(row: dict) -> str:
    # JSON array in fixed order; nested objects sorted by key; no whitespace; UTF-8
    return json.dumps([row[f] for f in HASHED_FIELDS],
                      ensure_ascii=False, separators=(",", ":"), sort_keys=True)

def row_hash(prev_hash: str, row: dict) -> str:
    return hashlib.sha256((prev_hash + canonical(row)).encode("utf-8")).hexdigest()
```

Canonicalisation rules:
- `occurred_at` is hashed as the exact stored string `YYYY-MM-DDTHH:MM:SS.ffffffZ`.
- `changes` / `details` contain only strings, integers, booleans, `null`, lists and objects. **No
  floats**: decimals (amounts, rates, scores) are written as strings (D10) so every language
  re-serialises them identically.
- `null` columns are hashed as JSON `null`.

Write sequence (serialised so the chain never forks):
1. Lock: SQLite — the write transaction (`BEGIN IMMEDIATE`) already serialises writers; Postgres —
   `SELECT pg_advisory_xact_lock(<audit lock key>)` (assumption).
2. `prev_hash` = `row_hash` of the row with the highest `id`, or `GENESIS` if the table is empty.
3. Compute `row_hash`, insert. `UNIQUE(prev_hash)` rejects a racing second writer.

### 5.4 Verification

```python
def verify(rows):                       # rows ordered by id ascending
    expected_prev = GENESIS             # or the archive anchor (§5.8)
    for r in rows:
        if r.prev_hash != expected_prev:
            return broken(r, "PREV_HASH_MISMATCH")   # row inserted, deleted or reordered
        if row_hash(r.prev_hash, r.as_dict()) != r.row_hash:
            return broken(r, "ROW_HASH_MISMATCH")    # row content altered
        expected_prev = r.row_hash
    return ok(last_row_hash=expected_prev)
```

`GET /api/v1/audit/verify` response:

```json
{
  "valid": true,
  "rows_checked": 8,
  "first_id": 1,
  "last_id": 8,
  "last_row_hash": "6deba678611f0bb0b472e4e0233a4267ecc269fdcadf024d528e3cb7e6d92d24",
  "broken_at": null,
  "checked_at": "2026-09-27T12:00:00.000000Z"
}
```

Failure example: `"valid": false, "broken_at": {"id": 5, "event_id": "e6f0a4c8-2d5b-4a97-8c13-5b7e9d0f2a64",
"reason": "ROW_HASH_MISMATCH"}`.

Limit of a pure chain: deleting the **newest** rows leaves a valid shorter chain. Mitigation
(assumption): a daily job stores `(last_id, last_row_hash, date)` outside the DB (WORM storage, ticket,
email to compliance); auditors compare `last_row_hash` of that id with the anchor. `id` gaps alone are
not evidence (Postgres sequences skip values on rollback).

### 5.5 Timestamps

`occurred_at` is set by the app in UTC with microseconds (`datetime.now(timezone.utc)`), formatted with
`Z` (D11). Server clocks synced via NTP. Ordering truth is `id`, not `occurred_at`.

### 5.6 Content rules and diff format

- **Never** stored: client secrets, `Authorization` header, Argon2 hashes, PDF bytes or extracted PDF
  text, full key/value lists.
- Deal values appear only in `changes` (field-level diffs on approve / reparse) and identifying
  `details` (`deal_id`, `file_name`) — needed to show what was changed. The audit table is therefore
  classified like deal data (`07_security.md` §5).
- `counterparty_pan` is **masked** wherever it would appear in `changes` or `details`: first five and
  last character kept (`ABCDE****F`). The full PAN exists only in the deal output (baseline §5).
- Diff format — one entry per changed field, dotted path for nested fields, values in their canonical
  JSON form (decimals as strings, dates ISO):

```json
{
  "deal_type": { "old": null, "new": "OUTRIGHT" },
  "repo.leg2_amount": { "old": "101797994.80", "new": "101797994.86" }
}
```

  Unchanged fields are omitted. Template diffs use the definition path (`label_map.stl dt`,
  `constants.platform`, `region_rules[0].bbox`).
- String sizes capped (`user_agent` 512, `actor_name` 100, `error` 1000) (assumption).

### 5.7 Deletion of business data

`SLIP_DELETED` removes the slip row and PDF but never audit rows. History of a deleted slip remains
available via `GET /slips/{id}/history` and `GET /audit?entity_id=`.

### 5.8 Retention and archive

- Retention: `BONDS_AUDIT_RETENTION_YEARS` (default **8**).
- Archive job (Later, assumption): rows older than the online window (e.g. 2 years) are exported
  (JSON lines with all columns, including hashes) to encrypted, write-once storage together with a
  manifest `{first_id, last_id, last_row_hash, sha256_of_file}`. Rows are removed from the live table
  only by a maintenance role that temporarily bypasses the triggers, and the action is recorded in the
  live log (`system` actor, proposed action `AUDIT_ARCHIVED` — **open point**, not in baseline §6).
- After archive, verification of the live table starts from the manifest's `last_row_hash` instead
  of `GENESIS`; the archive file is verified on its own from genesis / previous manifest.
- Archives older than the retention period are destroyed with a documented approval.

---

## 6. Example: timeline of one slip

Mock slip 01 uploaded into an empty system (no templates yet) → `NEW_TEMPLATE` → reviewed → preview →
approve (creates template v1) → export as Excel. Hashes are real (computed with the §5.3 algorithm,
starting from genesis), so this block can serve as a test vector.

```json
[
  {
    "id": 1,
    "event_id": "0b7d3a51-6c2e-4f1a-8d93-1e5f7a2c4b60",
    "occurred_at": "2026-09-27T10:15:02.431000Z",
    "actor_type": "client",
    "actor_id": "treasury-frontend",
    "actor_name": "Priya Shah",
    "action": "SLIP_UPLOADED",
    "entity_type": "slip",
    "entity_id": "3f6c1e2a-8b4d-4f7a-9c2e-5d1b7a9e0c44",
    "status_before": null,
    "status_after": null,
    "changes": null,
    "details": {
      "file_name": "01_gsec_outright_purchase.pdf",
      "file_size": 4187,
      "sha256": "9b2f4c1d7e0a6b3c8d5e2f1a0c9b8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a2b1c",
      "page_count": 1,
      "duplicate_of": null
    },
    "request_id": "ui-7c1d",
    "ip_address": "10.20.4.17",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TreasuryUI/1.4",
    "outcome": "SUCCESS",
    "error": null,
    "prev_hash": "0000000000000000000000000000000000000000000000000000000000000000",
    "row_hash": "f5a437afd6eaae847f3490afff71f1336334476d284c48e964acf67b109ffb62"
  },
  {
    "id": 2,
    "event_id": "5e2a9c07-3b8f-4d61-a2c4-7f0e1b9d3a85",
    "occurred_at": "2026-09-27T10:15:02.905000Z",
    "actor_type": "client",
    "actor_id": "treasury-frontend",
    "actor_name": "Priya Shah",
    "action": "SLIP_PARSED",
    "entity_type": "slip",
    "entity_id": "3f6c1e2a-8b4d-4f7a-9c2e-5d1b7a9e0c44",
    "status_before": null,
    "status_after": "NEW_TEMPLATE",
    "changes": null,
    "details": {
      "template_id": null,
      "template_version": null,
      "match_score": null,
      "market": "IN",
      "market_confidence": "0.97",
      "missing_required": [
        "deal_type",
        "buy_sell"
      ],
      "validation_failed": [],
      "unmapped_count": 5
    },
    "request_id": "ui-7c1d",
    "ip_address": "10.20.4.17",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TreasuryUI/1.4",
    "outcome": "SUCCESS",
    "error": null,
    "prev_hash": "f5a437afd6eaae847f3490afff71f1336334476d284c48e964acf67b109ffb62",
    "row_hash": "86b504a7d2c930549d328842cb77a1488921505d79c50685903a64c05bad0628"
  },
  {
    "id": 3,
    "event_id": "9a41f6d2-0c7b-4e38-b5a1-2d8c6e4f7b19",
    "occurred_at": "2026-09-27T10:16:10.112000Z",
    "actor_type": "client",
    "actor_id": "treasury-frontend",
    "actor_name": "Priya Shah",
    "action": "SLIP_VIEWED",
    "entity_type": "slip",
    "entity_id": "3f6c1e2a-8b4d-4f7a-9c2e-5d1b7a9e0c44",
    "status_before": null,
    "status_after": null,
    "changes": null,
    "details": {
      "status": "NEW_TEMPLATE"
    },
    "request_id": "ui-8a02",
    "ip_address": "10.20.4.17",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TreasuryUI/1.4",
    "outcome": "SUCCESS",
    "error": null,
    "prev_hash": "86b504a7d2c930549d328842cb77a1488921505d79c50685903a64c05bad0628",
    "row_hash": "97f8164b9b006bb347c659085e07724e98d01bfce9977283f3c68fb62cf7225a"
  },
  {
    "id": 4,
    "event_id": "c3d8b2e4-7a19-4f05-9e6b-0a5c3d1f8e27",
    "occurred_at": "2026-09-27T10:16:10.348000Z",
    "actor_type": "client",
    "actor_id": "treasury-frontend",
    "actor_name": "Priya Shah",
    "action": "SLIP_PDF_DOWNLOADED",
    "entity_type": "slip",
    "entity_id": "3f6c1e2a-8b4d-4f7a-9c2e-5d1b7a9e0c44",
    "status_before": null,
    "status_after": null,
    "changes": null,
    "details": {
      "sha256": "9b2f4c1d7e0a6b3c8d5e2f1a0c9b8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a2b1c"
    },
    "request_id": "ui-8a03",
    "ip_address": "10.20.4.17",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TreasuryUI/1.4",
    "outcome": "SUCCESS",
    "error": null,
    "prev_hash": "97f8164b9b006bb347c659085e07724e98d01bfce9977283f3c68fb62cf7225a",
    "row_hash": "8e1dd14b1061d3e3e1d4eddfea11489e6a7a0be0a8a6d72d2432571272c64ab5"
  },
  {
    "id": 5,
    "event_id": "e6f0a4c8-2d5b-4a97-8c13-5b7e9d0f2a64",
    "occurred_at": "2026-09-27T10:19:37.560000Z",
    "actor_type": "client",
    "actor_id": "treasury-frontend",
    "actor_name": "Priya Shah",
    "action": "SLIP_PREVIEWED",
    "entity_type": "slip",
    "entity_id": "3f6c1e2a-8b4d-4f7a-9c2e-5d1b7a9e0c44",
    "status_before": null,
    "status_after": null,
    "changes": null,
    "details": {
      "label_map_count": 5,
      "region_rule_count": 0,
      "regex_rule_count": 0,
      "constant_count": 1,
      "would_be_status": "PARSED",
      "missing_required": [],
      "validation_failed": []
    },
    "request_id": "ui-8f11",
    "ip_address": "10.20.4.17",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TreasuryUI/1.4",
    "outcome": "SUCCESS",
    "error": null,
    "prev_hash": "8e1dd14b1061d3e3e1d4eddfea11489e6a7a0be0a8a6d72d2432571272c64ab5",
    "row_hash": "9f9ac9ecfc4bc321e148f652513d0b156a3bc2e0caa06b16776bca15187b6ec1"
  },
  {
    "id": 6,
    "event_id": "17b5e9d3-8f2a-4c60-b4e7-3c9a1f5d0e82",
    "occurred_at": "2026-09-27T10:21:44.071000Z",
    "actor_type": "client",
    "actor_id": "treasury-frontend",
    "actor_name": "Priya Shah",
    "action": "TEMPLATE_CREATED",
    "entity_type": "template",
    "entity_id": "orca_treasury_g_sec_deal_slip",
    "status_before": null,
    "status_after": null,
    "changes": null,
    "details": {
      "name": "Orca Treasury G-Sec deal slip",
      "version": 1,
      "source_slip_id": "3f6c1e2a-8b4d-4f7a-9c2e-5d1b7a9e0c44",
      "note": "first onboarding",
      "definition": {
        "template_id": "orca_treasury_g_sec_deal_slip",
        "version": 1,
        "market": "IN",
        "fingerprint": {
          "labels": [
            "deal reference no",
            "deal type",
            "trading platform",
            "trade date",
            "trade time",
            "settlement date",
            "security type",
            "security description",
            "isin",
            "coupon rate",
            "maturity date",
            "last coupon date",
            "face value",
            "clean price",
            "yield to maturity",
            "principal amount",
            "accrued interest days",
            "accrued interest",
            "total consideration",
            "counterparty",
            "settlement mode",
            "portfolio book",
            "sgl csgl ac",
            "dealer"
          ],
          "keywords": [
            "DEAL SLIP - GOVERNMENT SECURITIES"
          ]
        },
        "match_threshold": "0.80",
        "label_map": {
          "deal type": "deal_type"
        },
        "region_rules": [],
        "regex_rules": [],
        "constants": {
          "platform": "NDS-OM"
        },
        "ignore": [
          "trading platform",
          "trade time",
          "security type",
          "sgl csgl ac"
        ],
        "accepted_derived": [
          "buy_sell"
        ]
      }
    },
    "request_id": "ui-9f2a",
    "ip_address": "10.20.4.17",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TreasuryUI/1.4",
    "outcome": "SUCCESS",
    "error": null,
    "prev_hash": "9f9ac9ecfc4bc321e148f652513d0b156a3bc2e0caa06b16776bca15187b6ec1",
    "row_hash": "ccb15acbecaa5a9bcd6fe60082dfae25cede2f76ea5edb8121e4807e935b8611"
  },
  {
    "id": 7,
    "event_id": "a7e3c0d2-1f4b-4c89-9e61-3b2d5f7a8c10",
    "occurred_at": "2026-09-27T10:21:44.090000Z",
    "actor_type": "client",
    "actor_id": "treasury-frontend",
    "actor_name": "Priya Shah",
    "action": "SLIP_APPROVED",
    "entity_type": "slip",
    "entity_id": "3f6c1e2a-8b4d-4f7a-9c2e-5d1b7a9e0c44",
    "status_before": "NEW_TEMPLATE",
    "status_after": "APPROVED",
    "changes": {
      "buy_sell": {
        "old": null,
        "new": "BUY"
      },
      "deal_type": {
        "old": null,
        "new": "OUTRIGHT"
      },
      "platform": {
        "old": null,
        "new": "NDS-OM"
      }
    },
    "details": {
      "template_id": "orca_treasury_g_sec_deal_slip",
      "template_version": 1,
      "template_created": true,
      "accept_validation_issues": false,
      "accepted_issues": []
    },
    "request_id": "ui-9f2a",
    "ip_address": "10.20.4.17",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TreasuryUI/1.4",
    "outcome": "SUCCESS",
    "error": null,
    "prev_hash": "ccb15acbecaa5a9bcd6fe60082dfae25cede2f76ea5edb8121e4807e935b8611",
    "row_hash": "5b0052573e147cb67a12c8f953c0c11456f444036427d8724af5539076a191a5"
  },
  {
    "id": 8,
    "event_id": "d4c2e8f1-5a3b-4d76-9b0e-6f1a8c2d4e53",
    "occurred_at": "2026-09-27T10:22:05.214000Z",
    "actor_type": "client",
    "actor_id": "treasury-frontend",
    "actor_name": "Priya Shah",
    "action": "SLIP_EXPORTED",
    "entity_type": "slip",
    "entity_id": "3f6c1e2a-8b4d-4f7a-9c2e-5d1b7a9e0c44",
    "status_before": null,
    "status_after": null,
    "changes": null,
    "details": {
      "format": "xlsx",
      "status": "APPROVED",
      "deal_id": "GS/NDSOM/2026/004571"
    },
    "request_id": "ui-a011",
    "ip_address": "10.20.4.17",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TreasuryUI/1.4",
    "outcome": "SUCCESS",
    "error": null,
    "prev_hash": "5b0052573e147cb67a12c8f953c0c11456f444036427d8724af5539076a191a5",
    "row_hash": "6deba678611f0bb0b472e4e0233a4267ecc269fdcadf024d528e3cb7e6d92d24"
  }
]
```

---

## 7. How auditors use it

### 7.1 API queries

| Question | Call |
|---|---|
| Everything that happened to a slip | `GET /api/v1/slips/{id}/history` or `GET /api/v1/audit?entity_type=slip&entity_id={id}` |
| Who approved what this month | `GET /api/v1/audit?action=SLIP_APPROVED&from=2026-09-01&to=2026-09-30` |
| All activity of one client | `GET /api/v1/audit?actor_id=treasury-frontend&from=2026-09-27&to=2026-09-27` |
| Failed / blocked logins | `GET /api/v1/audit?action=AUTH_FAILED&from=...` and `action=AUTH_BLOCKED` |
| Template changes | `GET /api/v1/audit?entity_type=template&entity_id={slug}` or `GET /api/v1/templates/{id}/history` |
| Data leaving the system | `action=SLIP_EXPORTED`, `DEALS_EXPORTED`, `SLIP_PDF_DOWNLOADED`, `AUDIT_EXPORTED` |
| Trace a support ticket | `request_id` from the ticket → search logs + `audit_log.request_id` |
| Is the log intact? | `GET /api/v1/audit/verify`; compare `last_row_hash` with the external anchor |

### 7.2 SQL (read-only DB user)

```sql
-- approvals with validation issues accepted
SELECT occurred_at, actor_id, actor_name, entity_id, details
FROM audit_log
WHERE action = 'SLIP_APPROVED' AND details LIKE '%"accept_validation_issues":true%';   -- Postgres: details->>'accept_validation_issues' = 'true'

-- failed auths per IP per day
SELECT substr(occurred_at, 1, 10) AS day, ip_address, count(*) AS failures
FROM audit_log WHERE action = 'AUTH_FAILED'
GROUP BY day, ip_address ORDER BY failures DESC;
```

### 7.3 Export

`GET /api/v1/audit/export?format=csv|xlsx|json&from=&to=` (Later) returns all columns including
`prev_hash` / `row_hash`; the export itself is logged as `AUDIT_EXPORTED` with the last exported hash.
An auditor can re-run §5.4 offline on the JSON export (a contiguous range verifies from its first row's
`prev_hash`).

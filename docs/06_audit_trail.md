# 06 · Audit Trail Design

How every action is recorded and how tampering is detected. Decisions: B D9, ADR-0006 (append-only,
hash-chained) and ADR-0009 (stored as a file, metadata only). Implementation:
`app/services/audit_service.py`.

---

## 1. Goals

| Goal | How |
|---|---|
| Who / what / when / from where | `actor_id` (client), `actor_name` (`X-Actor-Name`), `action`, `occurred_at` (UTC), `ip_address`, `user_agent`, `request_id` |
| Completeness | Every state change and every parse writes a record — including failures (`outcome = FAILURE`) |
| Tamper evidence | Each record carries the hash of the previous one; any edit, insert or deletion in the middle breaks the chain |
| No slip data | Metadata only: no file names (they often contain client names), no deal values, no PDF text; PAN-shaped text is masked |

## 2. Storage

One file, `BONDS_AUDIT_FILE` (default `./data/audit/audit.jsonl`), git-ignored. One JSON object per
line, written in canonical form (sorted keys, no spaces), opened in **append** mode, flushed and
`fsync`ed on every record. There is no database and no other copy.

## 3. Record

| Field | Type | Meaning |
|---|---|---|
| `seq` | int | 1, 2, 3, … in file order |
| `event_id` | str | Random UUID (hex) |
| `occurred_at` | str | UTC `YYYY-MM-DDTHH:MM:SS.ffffffZ` |
| `actor_type` | str | `client` (API), `cli` (command line), `system` |
| `actor_id` | str / null | Client id; for `cli` the OS user; `null` when authentication failed |
| `actor_name` | str / null | `X-Actor-Name` header (informational, not verified) |
| `action` | str | §4 |
| `entity_type` | str / null | `parse`, `template`, `client` |
| `entity_id` | str / null | Template id, client id, or the PDF's SHA-256 for a parse |
| `outcome` | str | `SUCCESS` / `FAILURE` |
| `error` | str / null | Problem slug or reason (`unsupported-media-type`, `BAD_SECRET`, …) |
| `details` | object | Action-specific metadata (§4) |
| `request_id` | str / null | `X-Request-ID` of the call |
| `ip_address`, `user_agent` | str / null | Caller |
| `prev_hash` | str | `row_hash` of the previous record; 64 zeros for the first |
| `row_hash` | str | SHA-256 (hex) of the canonical JSON of all other fields |

## 4. Actions

| Action | Written by | `entity` | `details` |
|---|---|---|---|
| `SLIP_PARSED` | `POST /api/v1/parse` | `parse` / SHA-256 | `sha256, size, pages, status, market, template_id, template_version, match_score, format`; on a rejected upload only `format` + `error` |
| `TEMPLATE_PREVIEWED` | `POST /api/v1/templates/preview` | `template` / base id or null | `sha256, would_be_status, missing_required, label_map_entries, rules` |
| `TEMPLATE_CREATED` | `POST /api/v1/templates` (new) | `template` / id | `version, name, note, sha256, status_after, accepted_validation_issues` |
| `TEMPLATE_VERSION_ADDED` | approve with `template_id`, or `PUT /templates/{id}` | `template` / id | `version, note`, approve also `sha256, status_after` |
| `TEMPLATE_ENABLED` / `TEMPLATE_DISABLED` | `PATCH` `is_active` | `template` / id | – |
| `TEMPLATE_UPDATED` | `PATCH` name / description | `template` / id | `changes: {field: {old, new}}` |
| `TEMPLATE_IMPORTED` | `POST /api/v1/templates/import` | `template` / id | `name` |
| `AUTH_FAILED` | any protected endpoint | `client` / attempted id | `reason` (`MISSING_HEADER`, `UNKNOWN_CLIENT`, `BAD_SECRET`, `CLIENT_DISABLED`), `path` |
| `AUTH_BLOCKED` | limiter threshold reached | `client` / attempted id | `ip, block_seconds, window_seconds` |
| `CLIENT_ADDED`, `CLIENT_DISABLED`, `CLIENT_SECRET_ROTATED` | CLI | `client` / id | – |

Reads (`GET` templates, schema, audit, `verify`) are not audited.

## 5. Rules

### 5.1 Append-only

The service only ever appends. Changing or deleting a line is detected by verification (§5.3).
In production, make the file append-only at the OS level as well (e.g. `chattr +a` on Linux, or a
log shipper that forwards each line to write-once storage).

### 5.2 Hash chain

```
row_hash = SHA-256( canonical_json(record without row_hash) )          # UTF-8, sorted keys, no spaces
record.prev_hash = row_hash of the previous record (64 × "0" for seq 1)
```

### 5.3 Verification

`GET /api/v1/audit/verify` or `python -m app.cli verify-audit` re-reads the file in order and
checks, for every record, that `prev_hash` equals the previous `row_hash` and that `row_hash`
matches the recomputed hash.

```json
{"ok": false, "checked": 41, "first_bad_seq": 42, "reason": "row_hash_mismatch", "last_hash": "9f2c…"}
```

`row_hash_mismatch` = the record was edited; `prev_hash_mismatch` = a record before it was removed
or inserted. Deleting the **newest** records cannot be seen from the file alone: keep a copy of
`last_hash` outside the server (for example in the daily ops report) and compare.

### 5.4 Content rules

- Never recorded: client secrets, `Authorization` headers, Argon2 hashes, PDF bytes or text, file
  names, deal values.
- PAN-shaped text (`AAAAA9999A`) anywhere in `actor_name`, `error` or `details` is masked to
  `AAAAA****A` before hashing.

### 5.5 Retention

`BONDS_AUDIT_RETENTION_YEARS` (default 8) is the policy; archiving (rotate the file, keep the last
`row_hash` as the next file's starting point) is an operations task (`docs/11_runbook.md`).

## 6. Example

```json
{"action":"SLIP_PARSED","actor_id":"frontend","actor_name":"Priya Shah","actor_type":"client","details":{"format":"json","market":"IN","match_score":0.9333,"pages":1,"sha256":"5e1c…","size":3410,"status":"PARSED","template_id":"mld_client_confirmation_letter","template_version":1},"entity_id":"5e1c…","entity_type":"parse","error":null,"event_id":"0b6f…","ip_address":"127.0.0.1","occurred_at":"2026-10-02T09:12:04.123456Z","outcome":"SUCCESS","prev_hash":"4ef1…","request_id":"ui-7c1d","row_hash":"a917…","seq":12,"user_agent":"Mozilla/5.0"}
```

## 7. How auditors use it

- `GET /api/v1/audit?action=TEMPLATE_CREATED` — who approved which layouts.
- `GET /api/v1/audit?actor_id=frontend&from=2026-10-01&to=2026-10-31` — one client's activity.
- `GET /api/v1/audit?action=AUTH_FAILED` — failed logins.
- `GET /api/v1/audit?entity_id=<sha256>` — every parse of one file (compute the SHA-256 of a PDF
  you hold to find its records).
- `GET /api/v1/audit/verify` — integrity of the whole log.

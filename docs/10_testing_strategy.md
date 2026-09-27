# 10 · Testing Strategy

How the Bonds Deal Slip Parser is tested: what is tested at each level, the golden-file rules,
the CI pipeline and the commands to run. Names follow `docs/00_design_baseline.md`.

> Items marked **(assumption)** (tool choices beyond `requirements.txt`, numeric targets) are
> proposals, not baseline decisions.

---

## 1. Principles

- **Money is `Decimal`.** Tests compare amounts as `Decimal`, never `float`.
- **No real slips in the repo.** Tests use only `samples/mock/` and synthetic fixtures generated
  with reportlab. `samples/real/` is git-ignored and never read by tests.
- **Deterministic.** No network, no clock dependence (inject "now"), no LLM, and a fresh temp DB
  per test session.
- **Every bug gets a test** before it is fixed, usually as a new golden fixture.

---

## 2. Test pyramid

| Level | Folder | What | Share / speed |
|---|---|---|---|
| Unit (engine) | `tests/unit/` | Pure functions in `app/engine/*`: normalisers, label normalisation, synonym / abbreviation / fuzzy mapping, fingerprint + Jaccard, market vote, derivations, validators | ~60%, < 5 s total |
| Property | `tests/unit/test_properties.py` | Invariants of normalisers and validators over generated inputs | < 10 s |
| Golden | `tests/golden/` | Whole pipeline: PDF → canonical deal, compared with expected JSON | 4 mock + 1 per approved template |
| API | `tests/api/` | FastAPI `TestClient` against a temp SQLite DB: auth, endpoints, flows, errors | ~20% |
| Audit | `tests/audit/` | Append-only triggers, hash chain, diffs | small, critical |
| Security | `tests/security/` (and `tests/auth/`) | Auth failures, limits, input abuse, leakage | small, critical |
| Performance | `tests/perf/` (marker `perf`, not in the default run) | Latency targets | nightly |

---

## 3. Unit tests (engine)

| Module | Examples |
|---|---|
| `extract` | Table label/value pairs, `label\|value\|label\|value` rows, grid header + rows, `Label: value`, gap-separated text, prose; bboxes returned |
| `normalize` | `5,00,00,000.00`, `50,000,000.00`, `5.00 Cr`, `50 Lakh` → `"50000000"`; dates `24-Sep-2026`, `24/09/2026`, `24.09.2026`, `2026-09-24`, `24 September 2026` → `2026-09-24`; PURCHASE / BOUGHT → `BUY`; `Actual/Actual` → `ACT/ACT`; `7.10% p.a. (Semi-Annual)` → `7.10` |
| `mapping` | label normaliser strips `(INR)`, `(%)` and punctuation; `Stl Dt` → `settlement_date` via abbreviation (0.90); fuzzy score ≤ 0.85; template `label_map` beats synonyms; `_ignore` |
| `detect` | Jaccard score; threshold 0.80 default, per-template override; disabled template skipped; keywords |
| `market` | Each signal in `docs/09_market_profiles.md` §3; vote, confidence, conflict; all 4 mock slips → `IN` |
| `derive` | coupon and tenor from the security name; repo leg 1 → top level; `buy_sell` from deal type; `REVERSE_REPO` → `BUY` |
| `validate` | ISIN check digit (valid / one-char mutation invalid); principal ±0.01; consideration; discount; quantity; repo `leg1 + interest = leg2`, `repo_days`; settlement ≥ trade; weekend; holiday |
| `pipeline` / decide | Status rules of baseline §3 using stubbed field sets, e.g. one required field at 0.89 → `NEEDS_REVIEW` |

Reuse the helpers in `tools/generate_mock_slips.py` (`isin_with_check`, `inr`, `days_30_360`)
as test oracles. Import them; do not copy them.

---

## 4. Property-style tests

Use `hypothesis` (**assumption**: dev dependency) or seeded `pytest.mark.parametrize` over
generated values.

| Property | Statement |
|---|---|
| Indian amount round trip | For any `Decimal` `x` with 2 dp in [0, 10¹⁴): `parse_amount(inr(x)) == x` |
| Western amount round trip | `parse_amount(f"{x:,.2f}") == x` |
| Unit scaling | `parse_amount(f"{x} Cr") == x * 10**7`; `Lakh` → `x * 10**5` |
| Date round trip (`IN`) | For any date `d`, every format in `IN.json` `date.formats` parses back to `d` |
| ISIN | `isin_with_check(body)` is always valid; changing any single character makes it invalid (except a check digit that happens to collide) |
| Label normalisation | Idempotent: `norm(norm(s)) == norm(s)`; case- and whitespace-insensitive |
| No float | Normaliser outputs are `Decimal` / `str` / `int` / `date` only |
| 32nds (after MVP) | `parse_32nds(format_32nds(p)) == p` for p on the 1/256 grid |

---

## 5. Golden-file tests

Input `samples/mock/*.pdf`, expected `samples/mock/expected/<same stem>.json`. The four slips
cover four layouts: two-column table (01), header + horizontal grids (02), letter / prose (03)
and a two-leg grid (04).

### 5.1 Comparison rules

| Rule | Detail |
|---|---|
| Keys checked | Only keys present in the expected JSON (recursively, including `repo`). Extra parser keys are not failures |
| `null` in expected | The parser must return `null` (e.g. `quantity` in 01, `broker` in 02) |
| Decimal fields | `Decimal(actual) == Decimal(expected)`, so `"7.1"` equals `"7.10"`. Actual must be a **string** in JSON (D10); a number type fails |
| Integers | `accrued_days`, `quantity`, `tenor_days`, `coupon_frequency`, `repo_days` compared as int |
| Dates | Exact ISO string |
| Text | Exact after whitespace collapse |
| Report | One assertion per slip listing **all** mismatches (`field: expected X, got Y`) |

```python
# tests/golden/test_mock_slips.py (sketch)
KNOWN_EXCEPTIONS = {
    # The 04 repo slip prints only "6.99% GS 2031"; no maturity date appears anywhere on it.
    # The generator knows it (2031-06-17), but the parser must not invent it.
    ("04_market_repo_reverse_repo", "maturity_date"): None,
}

@pytest.mark.parametrize("pdf", sorted(MOCK_DIR.glob("*.pdf")), ids=lambda p: p.stem)
def test_golden(pdf):
    expected = json.loads((MOCK_DIR / "expected" / f"{pdf.stem}.json").read_text())
    for (stem, field), value in KNOWN_EXCEPTIONS.items():
        if stem == pdf.stem:
            expected[field] = value
    result = parse_pdf(pdf.read_bytes(), file_name=pdf.name)
    assert_deal_matches(result.deal, expected)      # Decimal-aware, collects all diffs
```

### 5.2 Documented known exception

| Slip | Field | Expected JSON | Parser must return | Why |
|---|---|---|---|---|
| `04_market_repo_reverse_repo` | `maturity_date` | `"2031-06-17"` | `null` | The slip does not print a maturity date. A value the parser cannot see must not be invented. Do **not** "fix" this by editing the expected JSON; the generator is the source of that file and is re-run |

No other exceptions are allowed without a row in this table and a code comment.

### 5.3 Golden status expectations

Without templates, mock slips go through the generic path. The test asserts the deal values, not
`PARSED`. A separate test seeds a template for each mock layout and asserts `PARSED` for 01–03.
For 04, fields read from prose (`settlement_mode`, `portfolio`, `dealer`) must still reach ≥ 0.90
through the template, or be in `accepted_derived`.

### 5.4 Per-template regression suite

Each approved template has
`tests/fixtures/templates/<template_id>/{template.json, sample.pdf, expected.json}`
(`docs/08_template_onboarding_guide.md` §12). `sample.pdf` must be synthetic. The test:

1. loads `template.json` into the temp DB
2. parses `sample.pdf` and asserts `status == "PARSED"`, the right `template_id`, and `match_score` ≥ threshold
3. compares the deal with `expected.json` using §5.1
4. asserts that the sample does **not** match any *other* fixture template above its threshold (catches over-broad templates)

---

## 6. API tests (FastAPI TestClient)

Setup (`tests/conftest.py`): `BONDS_DATA_DIR` = `tmp_path`, `BONDS_DATABASE_URL` =
`sqlite:///<tmp>/bonds.db`, run `init-db`, create client `test` with a known secret, and give
`TestClient(app)` default Basic auth.

| Area | Test | Expect |
|---|---|---|
| Health | `GET /health` without auth | 200, DB ok |
| Auth | Any `/api/v1/*` without header | 401 `application/problem+json`, `WWW-Authenticate: Basic` |
| | Wrong secret / unknown client / disabled client | 401, `AUTH_FAILED` audit row, secret not echoed |
| | Valid credentials | 200, `clients.last_used_at` updated |
| Parse | `POST /parse?format=json` with 01 | 200 `application/json`, deal matches golden; **no** `slips` row, no file in `data/uploads/` |
| | `format=xml` | 200 `application/xml`, well-formed, `isin` element present |
| | `format=xlsx` | 200 `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, opens with openpyxl |
| Upload | `POST /slips` with 02 | 201/200, id, status; file at `data/uploads/<id>.pdf`; SHA-256 stored |
| | Same file twice | Second has `duplicate_of` = first id |
| Onboarding flow | see below | |
| List | `GET /slips?status=NEW_TEMPLATE` | Contains the new slip; `limit` / `offset` honoured |
| Export | `GET /slips/{id}/export?format=json\|xml\|xlsx` | Content types as above, `Content-Disposition: attachment`, `SLIP_EXPORTED` audit |
| History | `GET /slips/{id}/history` | Ordered audit events for the slip |
| Headers | `X-Request-ID: abc` | Echoed; stored in audit `request_id`. Absent → generated and echoed |
| Errors | 404 unknown slip, 422 bad `format`, 413, 422 encrypted / too many pages | RFC 7807 body with `type`, `title`, `status`, `detail`, `instance`; never a stack trace |

**Onboarding flow test** (unknown layout generated inside the test):

```python
def make_unknown_layout_pdf(path):
    # reportlab canvas: "ACME BROKING (MOCK)" header, labels no template knows
    # ("Contract No", "Contract Dt", "Stl Dt", "Paper", "Nominal (Cr)", "Rate", "Net Payable"),
    # a valid ISIN from isin_with_check(), consistent figures.
    ...

def test_new_template_approve_then_parsed(client, tmp_path):
    a = client.post("/api/v1/slips", files={"file": pdf_bytes(tmp_path, "a")}).json()
    assert a["status"] == "NEW_TEMPLATE"
    prev = client.post(f"/api/v1/slips/{a['slip_id']}/preview", json=MAPPING).json()
    assert prev["missing_required"] == []
    assert client.get(f"/api/v1/templates").json() == []           # preview saved nothing
    r = client.post(f"/api/v1/slips/{a['slip_id']}/approve", json=MAPPING)
    assert r.status_code == 200 and r.json()["status"] == "APPROVED"
    assert client.post(f"/api/v1/slips/{a['slip_id']}/approve", json=MAPPING).status_code == 409
    b = client.post("/api/v1/slips", files={"file": pdf_bytes(tmp_path, "b", deal_no=2)}).json()
    assert b["status"] == "PARSED" and b["template"]["template_id"] == TEMPLATE_ID
```

Also: approve with a missing required mapping → 422; approve with `template_id` = existing →
version 2 (`TEMPLATE_VERSION_ADDED`), version 1 unchanged.

---

## 7. Audit tests

| Test | Expect |
|---|---|
| `UPDATE audit_log …` via raw SQL | Raises (trigger) |
| `DELETE FROM audit_log …` | Raises (trigger) |
| Every mutating endpoint | Exactly the expected `action` rows, in the **same transaction**: force a failure after the audit insert and assert that neither the business row nor the audit row exists |
| Hash chain | `row_hash[n] = sha256(prev_hash + canonical row)`; `prev_hash[n] == row_hash[n-1]`; first row uses the genesis value |
| `GET /audit/verify` on a clean DB | `ok: true`, row count |
| Tamper detection | In the test, drop the trigger, edit one row's `changes`, recreate the trigger → verify reports `ok: false` and the first broken `id` |
| Diff on approve | `SLIP_APPROVED.changes` holds status `NEW_TEMPLATE → APPROVED` and the approved mapping; `TEMPLATE_VERSION_ADDED.changes` holds the definition diff between versions |
| Actor | `actor_type = client`, `actor_id` = client id, `actor_name` from `X-Actor-Name`; `ip_address`, `user_agent` set |
| Failures | Failed approve (422) writes an audit row with `outcome = FAILURE` and `error` (**assumption**) |
| No secrets | No audit row contains a client secret or `Authorization` header |

---

## 8. Security tests

| Threat | Test |
|---|---|
| Missing / bad credentials | §6 auth rows |
| Brute force (after MVP) | 11th failure from one IP within 300 s → 429 until 900 s; `AUTH_BLOCKED` audit |
| Secret storage | `clients.secret_hash` is Argon2 (`$argon2id$`); plain secret appears nowhere in the DB or logs |
| Oversized file | > `BONDS_MAX_UPLOAD_MB` → 413 without reading the whole body into memory |
| Too many pages | > `BONDS_MAX_PAGES` → 422 |
| Not a PDF / renamed `.exe` / truncated PDF | 4xx problem+json, never 500. Magic bytes `%PDF-` checked |
| Encrypted PDF | 422 (password-protected support is after MVP) |
| Path traversal | `file_name="../../x.pdf"` → stored as `data/uploads/<uuid>.pdf` only |
| Injection | SQL-ish filter params (`status=' OR 1=1--`) → 422 or empty result; ORM parameters only |
| XML output | Values with `<`, `&` are escaped; output parses |
| Excel output | Values starting with `=`, `+`, `-`, `@` are written as text (formula injection) (**assumption**) |
| CORS | Only `BONDS_CORS_ORIGINS` get CORS headers |
| Docs switch | `BONDS_DOCS_ENABLED=false` → `/docs`, `/redoc`, `/openapi.json` 404 |
| Data leakage | Logs at `INFO` contain no slip values, only ids and status (log capture assertion) |
| No external calls | `socket` blocked in the test session (e.g. `pytest-socket`, **assumption**); pipeline still passes (D6) |

---

## 9. Performance targets (**assumption**)

| Case | Target (dev laptop, SQLite) |
|---|---|
| `POST /parse` of a 1–2 page slip | p95 ≤ 1.5 s |
| `POST /slips` with 20 active templates | p95 ≤ 2 s |
| 20-page slip (the maximum) | ≤ 10 s |
| `GET /slips` list, 10 000 slips | p95 ≤ 300 ms |
| `GET /audit/verify`, 100 000 rows | ≤ 30 s |

Run with `pytest -m perf`. The test fails if the target is exceeded by more than 50%, so it can
catch regressions without being flaky.

---

## 10. Coverage targets (**assumption**)

| Scope | Line coverage | Branch coverage |
|---|---|---|
| `app/engine/` | ≥ 90% | ≥ 80% |
| `app/services/`, `app/auth.py`, audit | ≥ 90% | ≥ 80% |
| Whole `app/` | ≥ 85% | n/a |

Measured with `pytest-cov`. CI fails below the whole-`app/` target.

---

## 11. CI pipeline (after MVP per baseline §11)

| Stage | Command | Fails on |
|---|---|---|
| 1 Lint / format | `ruff check .` and `ruff format --check .` | any finding |
| 2 Types | `mypy app` | any error (strict on `app/engine`) |
| 3 Unit + property | `pytest tests/unit` | failure |
| 4 Golden + template regression | `pytest tests/golden` | any mismatch |
| 5 API / audit / security | `pytest tests/api tests/audit tests/security tests/auth --cov=app --cov-fail-under=85` | failure / coverage |
| 6 Dependencies | `pip-audit -r requirements.txt` | known vulnerability without a documented ignore |
| 7 Mock drift | `python tools/generate_mock_slips.py && git diff --exit-code samples/mock/expected` | expected JSON changed without being committed |
| Nightly | `pytest -m perf` | target missed |

Dev tools (`ruff`, `mypy`, `pytest-cov`, `pip-audit`, `hypothesis`) are not in
`requirements.txt`. They go in `requirements-dev.txt` (**assumption**). Matrix: Python 3.11 and
3.12, on Ubuntu and Windows.

Stage 7 compares JSON only, because PDF bytes contain timestamps.

---

## 12. Test data management

| Data | Where | Committed |
|---|---|---|
| Mock slips + expected JSON | `samples/mock/`, regenerated by `python tools/generate_mock_slips.py` | Yes |
| Template regression fixtures | `tests/fixtures/templates/<id>/`, synthetic PDFs only | Yes |
| Unknown-layout PDFs for API tests | Generated in `tmp_path` during the test | No |
| Real (masked) slips | `samples/real/` (git-ignored except README) | **Never** |
| Test DB / uploads | `tmp_path` per session | No (`data/` is git-ignored) |

Rules:

- All names in fixtures are fictional and marked `(MOCK)`. ISINs are generated with
  `isin_with_check`. The footer reads "MOCK DATA - FOR PARSER TESTING ONLY".
- A real slip may be used **locally** to reproduce a bug. The committed test must be a synthetic
  replica.
- Before committing, run `git status --ignored samples/real` and confirm that nothing in it is staged.
- Change expected JSON only by changing the generator, never by hand-editing it to make a test pass.

---

## 13. Commands

```bash
pip install -r requirements.txt            # + requirements-dev.txt for lint/type/cov tools
python tools/generate_mock_slips.py        # regenerate samples/mock/*.pdf + expected/*.json

pytest                                     # everything except perf
pytest tests/unit -q                       # fast loop
pytest tests/golden -k 04_market_repo -vv  # one golden slip, full diff
pytest tests/golden -k <template_id>       # one template regression
pytest tests/api -k onboarding             # NEW_TEMPLATE -> approve -> PARSED
pytest --cov=app --cov-report=term-missing
pytest -m perf                             # performance targets

ruff check . && ruff format --check . && mypy app && pip-audit -r requirements.txt
```

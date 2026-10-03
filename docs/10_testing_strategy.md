# 10 · Testing Strategy

How `v0.1.0` is tested. Run everything with `pytest` (206+ tests, ~94 % coverage).

---

## 1. Principles

- Money values are checked exactly (Decimal), never approximately.
- Golden files define correct behaviour for whole slips; a change to an expected file must be
  explained in the pull request.
- Tests never use real client data: all sample slips are fictitious (`tools/generate_mock_slips.py`).
- Tests never write into the repository: API tests use temporary template / client / audit folders.

## 2. Test levels

| Level | Where | What |
|---|---|---|
| Unit | `tests/unit/` | Field registry, label normalisation, normalisers (dates, amounts, rates, ISIN, enums, scale), extraction helpers, mapping chain, market vote, derivation, validation, XSD |
| Property-based | `tests/unit/test_normalize.py` (hypothesis) | Date round trips; Indian / western amount round trips |
| Golden files | `tests/golden/test_mock_slips.py` | Every `samples/mock/*.pdf` against `samples/mock/expected/*.json` |
| API | `tests/api/` | Parse, templates (onboarding flow), schema, XML / Excel / XSD, errors, auth, limiter, audit, CLI |
| Architecture | `tests/test_architecture.py` | Engine and export import no web, service or storage code |
| Config / errors | `tests/test_config.py`, `tests/test_errors.py`, `tests/test_main.py` | Defaults, validation, status mapping, docs toggle |

## 3. Golden files

Six fictitious slips cover the layouts: two-column table (01), header + grids (02), letter with
prose (03), two-leg repo grid (04), merged-cell client letters (05, 06).

Comparison rules: only keys present in the expected JSON are checked; decimals compare by value but
must be strings; integers as integers; dates exact ISO; text after whitespace collapse; all
mismatches of a slip reported together.

**Documented exception:** slip 04 prints no maturity date; the parser must return `null` although
the generator's expected JSON has a value. No other exceptions.

## 4. Key API scenarios

| Scenario | Test |
|---|---|
| Unknown layout → `NEW_TEMPLATE` → preview → approve → second slip `PARSED`; versions; disable; template file holds no slip values | `test_full_onboarding_flow` |
| MLD client letter onboarded from one slip; another slip `PARSED` with the right deal | `test_jm_style_letter_onboarded_once_then_parsed` |
| **Nothing from a slip is stored**: only `clients.json` and the audit file exist; audit has no names, PANs or file names | `test_nothing_from_the_slip_is_stored` |
| 400 invalid mapping, 422 required fields missing, 409 validation issues, acceptance | `test_invalid_mapping_is_400`, `test_approve_*` |
| New version via approve, import, 404 | `test_new_version_via_approve_and_import` |
| XML validates against the XSD; bad decimals / enums / dates / version rejected; committed XSD current | `tests/unit/test_xsd.py` |
| 401 (missing, wrong, unknown, disabled, rotated), 429 lockout, every route protected | `tests/api/test_auth_audit.py` |
| Request id echoed; actor name audited; failed parse audited; audit search / verify; tampering detected; PAN masked | `tests/api/test_auth_audit.py` |
| File limits: 413, 415, 422 (encrypted, corrupt, too many pages), 500 without internals | `tests/api/test_parse.py` |

## 5. Regression suite for real layouts

For every layout onboarded from a real slip, add a synthetic replica to the mock generator and a
test that approves one replica and parses another as `PARSED` with the expected deal. The real
slip never enters the repository.

## 6. Quality gates (CI, every push)

1. `ruff check` (incl. security rules) and `ruff format --check`
2. `mypy --strict` on `app/`
3. `pytest` with coverage
4. `pip-audit` on `requirements.txt`
5. Docker job: build the image, run it, check `/health`, 401 without credentials and the CLI

Pre-commit runs ruff, mypy and file checks locally and blocks real slips and `.env`.

## 7. Targets

| Target | Value |
|---|---|
| Coverage, engine | ≥ 90 % |
| Coverage, overall | ≥ 80 % (currently ~94 %) |
| Parse time, 1–5 page text PDF | p95 ≤ 3 s (performance test planned, PH8) |

## 8. Commands

```bash
pytest                                   # everything
pytest tests/unit tests/golden -q        # fast engine loop
pytest --cov=app --cov-report=term-missing
python tools/generate_mock_slips.py      # regenerate mock slips (expected JSON must not change)
python tools/build_xsd.py                # regenerate the XSD after changing response models
python tools/demo.py                     # end-to-end demo
```

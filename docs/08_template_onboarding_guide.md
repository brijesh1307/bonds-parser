# 08 · Template Onboarding Guide

How to teach the parser a **new slip layout** through the API, as a back-office user (via the
frontend) or a frontend developer (via curl / Swagger). Names, statuses, thresholds and payloads
follow `docs/00_design_baseline.md`; if anything here disagrees with it, the baseline wins.

> Statements marked **(assumption)** are not fixed by the baseline. Confirm them against the live
> OpenAPI schema at `/docs` before relying on them.

---

## 1. When you need this guide

| Upload result `status` | Meaning | Action |
|---|---|---|
| `PARSED` | Known template matched and everything passed | Nothing to onboard |
| `NEW_TEMPLATE` | No active template scored ≥ its match threshold (default **0.80**) | **Onboard** (this guide, §3–§8) |
| `NEEDS_REVIEW` | A template matched, but a required field is missing, validation failed, confidence < **0.90**, or the market is uncertain / has no profile | Fix the mapping and approve a **new version** (§9) |
| `UNREADABLE` | No text layer (scan) | OCR is after MVP. Ask the sender for a text PDF |
| `FAILED` | Internal error | See `docs/11_runbook.md` and the slip's audit history |

---

## 2. Prerequisites

1. **API credentials.** A `client_id` / `client_secret` pair created with
   `python -m app.cli add-client <name>` (see `docs/11_runbook.md`). Every call below sends
   `Authorization: Basic base64(client_id:client_secret)`; curl does this with `-u`.
2. **Set `X-Actor-Name`** to the person doing the onboarding. It is informational only (no RBAC),
   but it is stored in the audit trail and on the slip as `approved_actor_name`.
3. **Mask the real slip first.** Replace counterparty names, account / SGL / DP / client IDs and
   dealer names. **Keep the layout, labels and number formats unchanged**, because the template
   is learnt from those.
4. **Store it only in `samples/real/`.** Everything there except its README is git-ignored.
   Never commit a real slip anywhere else.
5. **Get two or three slips of the same layout** if you can. One slip lets you onboard; a second
   lets you check that the next slip is `PARSED` automatically (§8).

Shell variables used below:

```bash
export API=http://127.0.0.1:8000/api/v1
export CRED='frontend:<client_secret>'
export ACTOR='Asha Rao (Back Office)'
```

On Windows PowerShell, call `curl.exe` (not the `curl` alias) and use `$env:API` and so on.

---

## 3. Step 1: upload the slip

```bash
curl -s -u "$CRED" -H "X-Actor-Name: $ACTOR" \
     -F "file=@samples/real/xyz_contract_note_01.pdf" \
     "$API/slips" | tee upload.json
```

The multipart field name `file` is an **(assumption)**; check it in Swagger. The upload is stored
as `data/uploads/<slip_id>.pdf`, hashed (SHA-256, duplicate check) and parsed. Audit rows
`SLIP_UPLOADED` and `SLIP_PARSED` are written.

`GET $API/slips/{id}` returns the same full result later.

---

## 4. Step 2: read the `NEW_TEMPLATE` response

The shape below is illustrative (**assumption** on exact key names). The authoritative model is
in `/docs`.

```json
{
  "slip_id": "5b0c6f2e-8a51-4a57-9a2b-1f0f3c1d7e44",
  "status": "NEW_TEMPLATE",
  "template": {"template_id": null, "version": null, "match_score": 0.31,
               "best_candidate": "bank_gsec_dealslip"},
  "market": {"market": "IN", "issuer_country": "IN", "slip_locale": "en-IN",
             "market_confidence": 0.93,
             "signals": [{"signal": "isin_prefix", "value": "IN", "market": "IN", "weight": 3.0},
                         {"signal": "currency", "value": "INR", "market": "IN", "weight": 2.0}]},
  "deal": {"deal_id": null, "trade_date": null, "isin": "IN0020230051", "...": "..."},
  "fields": {
    "isin":  {"value": "IN0020230051", "raw": "IN0020230051", "label": "ISIN",
              "method": "synonym", "confidence": 0.95, "page": 1,
              "bbox": [150.2, 262.0, 232.9, 272.0]},
    "settlement_date": {"value": "2026-09-23", "raw": "23/09/2026", "label": "Stl Dt",
                        "method": "abbreviation", "confidence": 0.90, "page": 1,
                        "bbox": [410.0, 118.4, 462.3, 128.4]}
  },
  "key_values": [
    {"key": "Contract No", "value": "XYZ/GS/26/00731", "source": "text", "page": 1,
     "key_bbox": [40.0, 118.4, 98.1, 128.4], "value_bbox": [104.0, 118.4, 190.6, 128.4],
     "mapped_to": null},
    {"key": "Nominal (Cr)", "value": "5.00", "source": "table", "page": 1,
     "key_bbox": [40.0, 300.1, 110.0, 310.1], "value_bbox": [150.0, 300.1, 172.4, 310.1],
     "mapped_to": null}
  ],
  "missing_required": ["trade_date", "face_value", "consideration", "price"],
  "validation": [
    {"check": "isin_check_digit", "passed": true},
    {"check": "principal", "passed": false,
     "message": "face_value x price / 100 does not equal principal_amount"}
  ]
}
```

| Part | What it tells you | What to do with it |
|---|---|---|
| `status` | `NEW_TEMPLATE`: nothing scored ≥ 0.80 | Onboard |
| `template.match_score` | Best Jaccard score against the active templates' fingerprints | Close to 0.80 means the slip may be a **drifted version** of `best_candidate`. See §9 |
| `market` | Detected market plus `issuer_country`, `slip_locale`, `market_confidence` and the signals that voted (`docs/09_market_profiles.md`) | Must be `IN` in the MVP. Anything else ends in `NEEDS_REVIEW` ("market profile not available") |
| `key_values` | **Every** label/value pair found, with `source` (`table` / `grid` / `text` / `prose`), page and bboxes in PDF points (origin top-left, as pdfplumber reports them) | This is the list you map from. The frontend draws the bboxes over the PDF (`GET /slips/{id}/pdf`) |
| `fields` | Best-effort canonical values, each with `method` and `confidence` (template 1.00, region 1.00, constant 1.00, regex 0.95, synonym 0.95, abbreviation 0.90, derived 0.90, fuzzy ≤ 0.85, text 0.80) | Anything below **0.90** stops a slip from being `PARSED`. Pin those fields with an explicit mapping |
| `missing_required` | Required canonical fields with no value | Each one needs a `label_map` entry, a region rule, a regex rule or a constant |
| `validation` | Results of the checks (ISIN check digit, principal, consideration, discount, quantity, repo legs, dates, holidays) | A failure after mapping usually means a wrong mapping or a unit problem (lakh / crore) |

Required fields (baseline §5): `deal_type, trade_date, settlement_date, security_name, isin,
face_value, consideration` (`deal_id` is optional), plus `buy_sell, price` for `OUTRIGHT`, `price` for
`PRIMARY_AUCTION`, and `repo.repo_rate, repo.leg1_amount, repo.leg2_date, repo.leg2_amount` for
`REPO` / `REVERSE_REPO`. `GET $API/schema/fields` lists every canonical field with its type,
`required` flag and enum values.

---

## 5. Step 3: decide the mappings

Build one **mapping payload**. The same payload is used by preview and approve:

| Key | Use it for | Notes |
|---|---|---|
| `label_map` | `"<label as printed>": "<canonical field>"` | Write labels as they appear on the slip (for example `"Stl Dt"`). They are normalised (lower-case, punctuation and `(INR)` / `(%)` stripped) when stored in the template. Map to `"_ignore"` for labels that are noise (`GST No`, `Page`); these go to the template's `ignore` list and leave `_unmapped` |
| `region_rules` | Values with no label, or labels the extractor pairs wrongly | `{"field", "page", "bbox": [x0, top, x1, bottom]}` in PDF points. All words inside the box are joined with a space (**assumption**). Leave about 2 pt of margin |
| `regex_rules` | Values buried in prose | `{"field", "pattern"}`. Runs over the full slip text; **capture group 1** is the value (**assumption**). Remember JSON escaping (`\\d`) |
| `constants` | Values that are always the same for this layout | For example `{"platform": "OTC", "currency": "INR"}` |
| `keywords` | Stable text that identifies the sender | Broker name, form title. Stored in `fingerprint.keywords` |
| `template_id` | `null` for a new template; an existing id to add a **new version** | For a new template the id is a slug, derived from `template_name` (**assumption**) |
| `template_name` | Human name | |
| `accept_validation_issues` | `true` to approve even though a validation check fails | Use only when the slip itself is wrong. The override is audited |
| `note` | Why you are approving | Stored on the template version and in the audit row |

Precedence when a field has several sources (baseline §4): template `label_map` → built-in
synonyms → abbreviation expansion → fuzzy match; then template rules (region, regex, constants).
A field set by a rule carries that rule's confidence.

Do **not** map a field that the engine already derives correctly (coupon from `7.18% GS 2033`,
`buy_sell` from the deal type, `instrument_type` from the name). If a reviewer accepts a
`derived` or `text` value for this layout, list it in the template's `accepted_derived` (set via
`PUT /templates/{id}` after MVP) so it scores 0.95 when this template matches.

---

## 6. Step 4: preview loop

Preview re-parses the stored slip with your draft mapping. **Nothing is saved** except a
`SLIP_PREVIEWED` audit row.

```bash
curl -s -u "$CRED" -H "X-Actor-Name: $ACTOR" -H "Content-Type: application/json" \
     -d @mapping.json "$API/slips/$SLIP_ID/preview" | tee preview.json
```

Repeat until all of the following hold:

- [ ] `missing_required` is `[]`
- [ ] every required field has `confidence` ≥ 0.90
- [ ] `validation` has no failures, or you can explain each one
- [ ] amounts look right to the rupee (lakh / crore scaling) and dates are right (day-first for `IN`)
- [ ] `market.market` is `IN`

---

## 7. Step 5: approve

```bash
curl -s -u "$CRED" -H "X-Actor-Name: $ACTOR" -H "Content-Type: application/json" \
     -d @mapping.json "$API/slips/$SLIP_ID/approve"
```

In a single DB transaction, approve:

1. builds the template definition (fingerprint labels from the slip, `label_map`, rules,
   constants, `ignore`, `keywords`, `match_threshold` 0.80)
2. inserts `templates` (new) and `template_versions` (version 1, or `current_version + 1`).
   Versions are never overwritten
3. sets the slip to `APPROVED` with `template_id`, `template_version`, `approved_mapping_json`,
   `approved_by`, `approved_actor_name` and `approved_at`
4. writes the audit rows `TEMPLATE_CREATED` or `TEMPLATE_VERSION_ADDED`, and `SLIP_APPROVED`
   (with a before/after diff in `changes`)

Error responses are RFC 7807 `application/problem+json`. Exact codes per case are an
**(assumption)**:

| Code | When | Fix |
|---|---|---|
| `422` | Required field still missing after applying the mapping | Add the missing mapping and preview again |
| `422` | Validation failed and `accept_validation_issues` is `false` | Fix the mapping. If the slip itself is wrong, set it to `true` and explain why in `note` |
| `422` | Unknown canonical field in `label_map` / rules, invalid regex, bad bbox, or a page that does not exist | Correct the payload (`GET /schema/fields`) |
| `409` | Slip is already `APPROVED` | Nothing to do. For a changed mapping, approve a **new** slip of that layout with `template_id` set |
| `409` | `template_id: null`, but a template with the derived slug already exists | Set `template_id` to add a version, or pick another `template_name` |
| `404` | `template_id` given but no such template exists | Use `null` to create it |

---

## 8. Step 6: verify the next slip parses automatically

Upload a **second** slip of the same layout:

```bash
curl -s -u "$CRED" -H "X-Actor-Name: $ACTOR" -F "file=@samples/real/xyz_contract_note_02.pdf" "$API/slips"
```

Expect `status: "PARSED"`, `template.template_id` set to your template, and `match_score` ≥ 0.80.
If it comes back `NEEDS_REVIEW`, read `missing_required` / `validation` and add a new version (§9).
`GET $API/templates/{id}` shows `match_count` and `last_matched_at` increasing.

---

## 9. Format drift (the broker changes the slip)

| Symptom | Cause | Action |
|---|---|---|
| Was `PARSED`, now `NEW_TEMPLATE` with `match_score` 0.6–0.79 and `best_candidate` = your template | Labels were renamed or added, so the fingerprint no longer overlaps enough | Approve with `"template_id": "<existing id>"`. This creates version N+1 with the new fingerprint |
| Was `PARSED`, now `NEEDS_REVIEW` with a field missing | A label was renamed but the rest still matches | Same: approve a new version with the new label added to `label_map` |
| A region-rule field has the wrong value | Layout shifted, so the bbox is off | New version with a corrected bbox. Prefer a label or regex rule if one is now possible |

Old versions are kept for audit and history (`GET /templates/{id}/history` shows version diffs,
after MVP). Slips parsed under an old version keep their `template_version`. Re-run non-approved
slips with `POST /slips/{id}/reparse` (after MVP). Approved slips re-parse only with
`?force=true`, which is audited.

---

## 10. Disabling a bad template

A template that matches the wrong slips, or produces wrong values, must stop matching straight away:

```bash
curl -s -u "$CRED" -H "X-Actor-Name: $ACTOR" -H "Content-Type: application/json" \
     -X PATCH -d '{"is_active": false}' "$API/templates/broker_xyz_gsec_confirm"
```

`PATCH` is after MVP and writes `TEMPLATE_DISABLED`. A disabled template is skipped by detection.
Slips it would have matched come back as `NEW_TEMPLATE`. Re-enable with `{"is_active": true}`
(`TEMPLATE_ENABLED`). Templates are never deleted. Then list what it touched and review those slips:

```bash
curl -s -u "$CRED" "$API/slips?template_id=broker_xyz_gsec_confirm&limit=100"
```

---

## 11. Moving templates between environments (UAT → PROD)

```bash
# export: the current definition is in the template detail response
curl -s -u "$CRED_UAT" "$UAT/api/v1/templates/broker_xyz_gsec_confirm" \
  | python -c "import json,sys; t=json.load(sys.stdin); print(json.dumps(t['definition'], indent=2))" \
  > broker_xyz_gsec_confirm.json

# import (after MVP)
curl -s -u "$CRED_PROD" -H "X-Actor-Name: $ACTOR" -H "Content-Type: application/json" \
     -d @broker_xyz_gsec_confirm.json "$PROD/api/v1/templates/import"
```

- The `definition` key name is an **(assumption)**. The file content is the template definition
  JSON (baseline §6).
- Import writes `TEMPLATE_IMPORTED`. If the id already exists, the import is added as a new
  version; it never overwrites (**assumption**, consistent with D7).
- Template JSON contains labels and bboxes only, no slip data. It is safe to keep in a
  config repo. Review the diff like code.
- After importing, upload a masked slip in the target environment and confirm `PARSED`.

---

## 12. Adding a regression fixture

Every approved template gets a golden test so that engine changes cannot silently break it
(`docs/10_testing_strategy.md`):

```
tests/fixtures/templates/<template_id>/
├── template.json     exported definition (§11)
├── sample.pdf        SYNTHETIC replica of the layout, never a real slip
└── expected.json     canonical deal expected from sample.pdf
```

1. Build `sample.pdf` as a **synthetic replica**: the same labels, positions and number formats,
   with invented values. Use reportlab, as `tools/generate_mock_slips.py` does. A masked real
   slip must still not be committed.
2. Write `expected.json` in the same style as `samples/mock/expected/*.json`: decimal strings,
   ISO dates, `null` for fields that must be absent. The ISIN must have a valid check digit
   (`isin_with_check()` in the generator).
3. Run `pytest tests/golden -k <template_id>`. It must pass before the template is promoted to PROD.

---

## 13. Worked example: "XYZ Securities" broker confirmation

A fictional broker, **XYZ Securities Pvt Ltd (MOCK)**, sends this G-Sec contract note. All values
are invented and internally consistent (30/360 accrued, 59 days).

```
                    XYZ SECURITIES PVT LTD (MOCK)          ┌──────────────────────────────┐
              Debt Market Desk - Contract Note             │ Orca Treasury Bank Ltd (MOCK)│  <- counterparty,
                                                           └──────────────────────────────┘     no label
 Contract No  XYZ/GS/26/00731        Contract Dt  22/09/2026        Stl Dt  23/09/2026

 Paper              7.18% GS 2033
 ISIN               IN0020230051
 B/S                Purchase
 Nominal (Cr)       5.00
 Rate               100.7250
 Accr Int           5,88,361.11
 Net Payable        5,09,50,861.11

 We confirm having dealt at a yield of 7.0412% p.a. for settlement DVP-III through CCIL.
 GST No 27AAACX0000X1Z5                                                        Page 1 of 1
```

### 13.1 Upload result (abridged)

`status: NEW_TEMPLATE`, `match_score: 0.22`, market `IN` (ISIN prefix `IN`, `CCIL`, Indian digit
grouping), `market_confidence: 0.91`.

| Label | Built-in outcome | Problem |
|---|---|---|
| `Contract No` | not recognised | `deal_id` missing |
| `Contract Dt` | fuzzy → `trade_date`, 0.72 | below 0.90 |
| `Stl Dt` | abbreviation → `settlement_date`, 0.90 | OK, but pin it anyway |
| `Paper` | not recognised | `security_name` missing |
| `ISIN` | synonym, 0.95 | OK |
| `B/S` | not recognised | `buy_sell` missing |
| `Nominal (Cr)` | synonym `Nominal` → `face_value`, value `5.00` | wrong scale unless the crore unit is applied |
| `Rate` | fuzzy → `price`, 0.78 | below 0.90 |
| `Accr Int` | abbreviation → `accrued_interest`, 0.90 | OK |
| `Net Payable` | fuzzy → `consideration`, 0.80 | below 0.90 |
| counterparty box | no label | needs a region rule |
| yield in prose | no label | needs a regex rule |

### 13.2 `mapping.json`

```json
{
  "label_map": {
    "Contract No": "deal_id",
    "Contract Dt": "trade_date",
    "Stl Dt": "settlement_date",
    "Paper": "security_name",
    "B/S": "buy_sell",
    "Nominal (Cr)": "face_value",
    "Rate": "price",
    "Accr Int": "accrued_interest",
    "Net Payable": "consideration",
    "GST No": "_ignore",
    "Page": "_ignore"
  },
  "region_rules": [
    {"field": "counterparty", "page": 1, "bbox": [372, 38, 560, 62]}
  ],
  "regex_rules": [
    {"field": "yield", "pattern": "at a yield of ([\\d.]+)%"},
    {"field": "settlement_mode", "pattern": "settlement (DVP-I{1,3}) through"}
  ],
  "constants": {"platform": "OTC", "currency": "INR", "deal_type": "OUTRIGHT"},
  "template_id": null,
  "template_name": "XYZ Securities G-Sec contract note",
  "keywords": ["XYZ SECURITIES", "Contract Note"],
  "accept_validation_issues": false,
  "note": "first onboarding, masked slip xyz_contract_note_01"
}
```

### 13.3 Preview

```bash
curl -s -u "$CRED" -H "X-Actor-Name: $ACTOR" -H "Content-Type: application/json" \
     -d @mapping.json "$API/slips/$SLIP_ID/preview" \
  | python -c "import json,sys; r=json.load(sys.stdin); print(r['status'], r['missing_required']); print(json.dumps(r['deal'], indent=1))"
```

Expected deal (abridged):

```json
{
  "deal_id": "XYZ/GS/26/00731",
  "deal_type": "OUTRIGHT",
  "instrument_type": "GSEC",
  "buy_sell": "BUY",
  "platform": "OTC",
  "trade_date": "2026-09-22",
  "settlement_date": "2026-09-23",
  "security_name": "7.18% GS 2033",
  "isin": "IN0020230051",
  "coupon_rate": "7.18",
  "face_value": "50000000",
  "price": "100.7250",
  "yield": "7.0412",
  "accrued_interest": "588361.11",
  "consideration": "50950861.11",
  "currency": "INR",
  "counterparty": "Orca Treasury Bank Ltd (MOCK)",
  "settlement_mode": "DVP-III"
}
```

Checks: `Nominal (Cr) 5.00` → `50000000`. `principal_amount` is not printed. Where the engine
derives it (`50362500.00`), the consideration check `50362500.00 + 588361.11 = 50950861.11`
passes. `22/09/2026` is read day-first because the market is `IN`.

### 13.4 Approve

```bash
curl -s -u "$CRED" -H "X-Actor-Name: $ACTOR" -H "Content-Type: application/json" \
     -d @mapping.json "$API/slips/$SLIP_ID/approve"
```

Stored definition (version 1, illustrative):

```json
{
  "template_id": "xyz_securities_g_sec_contract_note",
  "version": 1,
  "market": "IN",
  "fingerprint": {"labels": ["contract no", "contract dt", "stl dt", "paper", "isin", "b s",
                             "nominal cr", "rate", "accr int", "net payable"],
                  "keywords": ["XYZ SECURITIES", "Contract Note"]},
  "match_threshold": 0.80,
  "label_map": {"contract no": "deal_id", "contract dt": "trade_date", "stl dt": "settlement_date",
                "paper": "security_name", "b s": "buy_sell", "nominal cr": "face_value",
                "rate": "price", "accr int": "accrued_interest", "net payable": "consideration"},
  "region_rules": [{"field": "counterparty", "page": 1, "bbox": [372, 38, 560, 62]}],
  "regex_rules": [{"field": "yield", "pattern": "at a yield of ([\\d.]+)%"},
                  {"field": "settlement_mode", "pattern": "settlement (DVP-I{1,3}) through"}],
  "constants": {"platform": "OTC", "currency": "INR", "deal_type": "OUTRIGHT"},
  "ignore": ["gst no", "page"],
  "accepted_derived": []
}
```

Upload `xyz_contract_note_02.pdf` and expect `PARSED`. Then add
`tests/fixtures/templates/xyz_securities_g_sec_contract_note/` (§12).

---

## 14. Troubleshooting

| Problem | Likely cause | Fix |
|---|---|---|
| Label is visible but not in `key_values` | No separator the extractor recognises (no colon, gap too small, label and value in separate text blocks) | Add a **region rule** with the value's bbox. Take coordinates from a nearby `key_values` entry or from the frontend's box tool |
| Date has day and month swapped (`05/06/2026`) | Market detected wrongly, or a month-first slip in an `IN` layout | Check `market` first (`docs/09_market_profiles.md` §6). For `IN`, dates are day-first. A per-template date-order override is **not** in the baseline; raise it as an enhancement and meanwhile approve with `accept_validation_issues` only if the dates are really correct |
| Amount 10⁷ too small (`5.00` instead of `50000000`) | Crore / lakh unit printed in the label (`Nominal (Cr)`) or next to the value (`5.00 Cr`) | The `IN` normaliser scales `Cr` / `Crore` / `Lakh` / `Lac`. Confirm in preview. The principal / consideration validation catches a wrong scale. If the unit is only implied (not printed), do not approve; raise an enhancement |
| Value spans two lines (long counterparty / security name) | Extractor takes only the first line | **Region rule** with a bbox that covers both lines. Words are joined with a space |
| ISIN glued to the next word (`IN0020230051Settlement`) | Missing space in the PDF text layer | **Regex rule** `{"field": "isin", "pattern": "([A-Z]{2}[A-Z0-9]{9}[0-9])"}` (anchor it on `ISIN\\s*:?\\s*` if needed). Validation still checks the check digit |
| Two values mapped to the same field | A synonym and your `label_map` both hit | The explicit `label_map` wins. Map the unwanted label to `"_ignore"` |
| Template does not match the next slip (`NEW_TEMPLATE`, score < 0.80) | Fingerprint includes labels that only appear sometimes (optional rows, page footers), so Jaccard drops | Map those labels to `"_ignore"` so they leave the fingerprint. Add stable `keywords`. As a last resort lower that template's `match_threshold` (for example 0.70) in a new version via `PUT /templates/{id}`, and check that it does not now capture other brokers' slips |
| Wrong template matches | Two layouts share most labels | Add distinguishing `keywords` to both templates. Disable the wrong one while fixing it (§10) |
| `NEEDS_REVIEW`, reason "market profile not available" | Non-`IN` slip | Expected in the MVP. See `docs/09_market_profiles.md` |
| Approve returns `422` although preview looked fine | Payload differs from the one you previewed, so a required field is missing (validation issues give `409`, not `422`) | Send the exact same `mapping.json`. Read the problem `detail` |

# 08 · Template Onboarding Guide

How to teach the parser a new slip layout so that every later slip in that layout comes back
`PARSED`. Everything happens through the API; **no slip is stored** — you send the PDF with each
preview / approve call, and only the template (labels and rules) is saved.

---

## 1. When you need a template

`POST /api/v1/parse` returns `NEW_TEMPLATE` for a layout it has not seen. The deal is often already
complete (the built-in synonyms know most labels), but a slip is only `PARSED` once its layout is
approved as a template. Approving also fixes any labels the parser could not map.

## 2. Before you start

- Use a real slip of the layout, **masked** if it leaves your machine (names, PAN, account ids).
- Keep it in `samples/real/` (git-ignored).
- Have a client id / secret (`python -m app.cli add-client NAME`).

## 3. Read the first result

```bash
curl -u frontend:$SECRET -F "file=@samples/real/xyz_note.pdf" http://127.0.0.1:8000/api/v1/parse
```

| Part | What to look at |
|---|---|
| `deal` | What was understood |
| `missing_required` | Required fields not found — these must be mapped |
| `unmapped` | Labels found on the slip that map to nothing — candidates for `label_map` |
| `key_values[*].mapped_to` | What each label mapped to (check for wrong mappings) |
| `fields[*].method / confidence` | `fuzzy` or `text` values are below the 0.90 threshold |
| `validation` | Failed checks — usually a wrong mapping or a unit (lakh / crore) problem |
| `market` | Must be `IN` (other markets are review-only today) |

`python -m app.cli debug-extract slip.pdf` shows the same key/values as a table.

## 4. Decide the mapping

| Tool | Use for | Example |
|---|---|---|
| `label_map` | A label the parser does not know, or maps wrongly | `{"Contract Dt": "trade_date", "Paper": "security_name"}` |
| `"_ignore"` | Labels you do not need (keeps `unmapped` clean) | `{"GST No": "_ignore"}` |
| `regex_rules` | A value inside a sentence | `{"field": "yield", "pattern": "at a yield of ([\\d.]+)%"}` |
| `region_rules` | A value with no label, at a fixed place | `{"field": "counterparty", "page": 1, "bbox": [320, 140, 560, 158]}` |
| `constants` | Something the layout never prints | `{"platform": "OTC"}` |
| `keywords` | Text that identifies the layout (avoids matching similar slips) | `["XYZ Securities"]` |
| `date_order` | Numeric dates that could be day- or month-first | `"DMY"` |

Field names: `GET /api/v1/schema/fields`. Boxes are PDF points `[x0, top, x1, bottom]` with the
origin top-left — take them from `key_values[*].value_bbox` of a nearby value.

## 5. Preview until it is right

```bash
curl -u frontend:$SECRET -F "file=@samples/real/xyz_note.pdf" \
  -F 'mapping={"label_map": {"Contract Dt": "trade_date", "Paper": "security_name", "Rate": "price",
               "Net Payable": "consideration", "Txn": "deal_type", "GST No": "_ignore"},
               "constants": {"platform": "OTC"}}' \
  http://127.0.0.1:8000/api/v1/templates/preview
```

The response is the result this mapping would give (`template.is_draft = true`). Repeat until
`missing_required` is empty, `validation` is empty (or explained) and `status` is `PARSED`.

## 6. Approve

Same call to `POST /api/v1/templates`, with a `template_name`:

```bash
curl -u frontend:$SECRET -F "file=@samples/real/xyz_note.pdf" \
  -F 'mapping={"template_name": "XYZ Securities contract note", "keywords": ["XYZ Securities"],
               "label_map": {...}, "constants": {"platform": "OTC"}, "note": "first onboarding"}' \
  http://127.0.0.1:8000/api/v1/templates
```

| Response | Meaning | Action |
|---|---|---|
| 201 | Template v1 saved; `result` is the slip parsed with it | Done |
| 422 `approval-blocked` | Required fields still missing (or market unknown) | Map them, preview again |
| 409 `validation-not-accepted` | Numbers do not add up | Fix the mapping, or approve with `"accept_validation_issues": true` if the slip itself is wrong |
| 400 `invalid-mapping` | Unknown field, bad regex, bad box | Fix the mapping |

Required fields the parser only derived or found in the text are recorded as `accepted_derived`
(you saw and accepted them), so later slips reach the confidence threshold.

## 7. Check the next slip

Parse a **different** slip of the same layout. Expect `status: PARSED`,
`template.template_id` = your template and a `template.score` ≥ 0.80.

## 8. Format drift and fixes

When a counterparty changes its layout, slips come back `NEW_TEMPLATE` or `NEEDS_REVIEW`.
Approve again with `"template_id": "<id>"` (instead of `template_name`) → a **new version**; the
earlier versions stay in the file. Small edits without a PDF: `PUT /api/v1/templates/{id}` with an
edited `definition`.

## 9. Disable, move, review

- Disable a bad template: `PATCH /api/v1/templates/{id}` `{"is_active": false}`.
- Move between environments: `GET /api/v1/templates/{id}` → take `definition` →
  `POST /api/v1/templates/import` on the target.
- Templates are files in `templates/`; review and commit them like code. They contain labels and
  rules only — never put client data in a constant.

## 10. Add a regression test

For each onboarded layout, add a **synthetic** replica (fictitious names and numbers) to
`tools/generate_mock_slips.py` with its expected JSON, and a test that approves the replica and
parses a second replica as `PARSED` (see `tests/api/test_templates.py`). Never commit the real slip.

## 11. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| A label is not in `key_values` | Text outside any table and without `:` | `region_rules` with its box, or `regex_rules` |
| Wrong date (day / month swapped) | Numeric date, both parts ≤ 12 | `"date_order": "DMY"` / `"MDY"` |
| Amount 10⁷ too small | Unit only in the label (`Nominal (Cr)`) not recognised | Check `market` is `IN`; map the label to the amount field |
| Value spans two lines | Wrapped cell | Usually joined automatically; else a region rule over both lines |
| ISIN glued to the next text | Narrow column | Repaired automatically when the check digit is valid; else `regex_rules` |
| Template not matching (score < 0.80) | Labels differ between slips (e.g. optional rows) | Approve a second sample as a new version, add `keywords`, or lower the template's `match_threshold` via `PUT` |
| Two templates compete | Similar layouts | Add `keywords` that tell them apart |

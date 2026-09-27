# ADR-0002: pdfplumber for PDF extraction

**Status:** Accepted

## Context

Deal slips are mostly machine-generated PDFs with a text layer, in several layouts: two-column
label/value tables, four-column tables, horizontal grids (header row + value rows), `Label: value`
text, gap-separated text and prose letters (`deal_slip_standard_formats.md` §2). Onboarding a new
layout requires showing the user **where** each value sits, so we need word-level bounding boxes.
Slip data must not leave the environment (no cloud extraction). Scanned slips exist but are rare.

## Decision

Use **pdfplumber** for extraction (baseline D3):

- Words with bbox, lines and table detection → KeyValue candidates `{key, value, source, page, key_bbox, value_bbox}` with sources `table`, `grid`, `text`, `prose`.
- Bboxes are returned to the client and used by template `region_rules`.
- A PDF with no text layer → status `UNREADABLE`. OCR (Tesseract / ocrmypdf) is added after MVP, locally.
- `reportlab` is used only to generate mock slips for tests.

## Consequences

**Positive**
- Pure Python, MIT licence, no external service; runs on any platform.
- Precise coordinates for highlighting and region rules.
- Table and word APIs cover all four source types from one library.

**Negative**
- Slower than PyMuPDF on large documents (acceptable: `BONDS_MAX_PAGES`=20, slips are 1–3 pages).
- Table detection is heuristic; the engine adds its own grid/text heuristics on top of words.
- No OCR; scanned slips are `UNREADABLE` until the OCR phase.
- Password-protected PDFs need pikepdf (after MVP).

## Alternatives considered

| Option | Why not |
|---|---|
| PyMuPDF (fitz) | Faster, but AGPL licence is a problem for internal distribution. |
| pdfminer.six directly | pdfplumber already builds on it and adds tables/bbox helpers. |
| Camelot / Tabula | Tables only; Ghostscript / Java dependencies. |
| Cloud document AI (Textract, Form Recognizer) | Sends slip data outside; violates D6. |
| OCR on every page | Slower and less accurate than the text layer; kept as fallback only. |

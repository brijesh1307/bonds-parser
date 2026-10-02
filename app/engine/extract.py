"""PDF -> ExtractedDocument of label/value candidates with page positions (docs/03_lld.md §3.9, §4.1-4.4).

Sources, in order:
  table  ruled tables read as label|value: 2-column, 4-column, and irregular tables with merged
         cells (label, value, [label, value]) such as dealer-to-client confirmation letters.
         A cell holding "Label value" or several "Label : value" parts is split further.
  grid   ruled tables with a header row and one or more value rows.
  text   lines outside tables: "Label: value", "Label :- value", "A: x | B: y", "Label   value".
  prose  generic patterns over the full text (ISIN, bid type, tenor, dealer sign-off, ...).
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any, Literal

import pdfplumber
from pdfminer.pdfdocument import PDFEncryptionError, PDFPasswordIncorrect
from pdfplumber.utils.exceptions import PdfminerException

from app.engine.fields import Source
from app.engine.models import BBox, ExtractedDocument, KeyValue, Word
from app.engine.normalize import ISIN_RE, isin_check_digit_ok
from app.errors import EncryptedPdfError, InvalidPdfError, TooManyPagesError

MIN_TEXT_CHARS = 20  # fewer non-whitespace characters => no usable text layer (scan) [LLD A5]
GAP_PT = 10.0  # horizontal gap that separates a label from its value on a text line

TableKind = Literal["KV", "KV_ROWS", "GRID", "UNKNOWN"]

# --------------------------------------------------------------------------- cell helpers


def clean_cell(s: str | None) -> str:
    """Collapse whitespace; rule-only cells ('_____', '-----') become empty."""
    if s is None:
        return ""
    s = " ".join(s.split())
    return "" if re.fullmatch(r"[_\-.\s]*", s) else s


def label_like(s: str) -> bool:
    """Looks like a label: has letters, short, fewer than 30 % digits (docs/03_lld.md §4.1)."""
    s = s.strip()
    if not s or len(s) > 60 or not any(ch.isalpha() for ch in s):
        return False
    chars = [c for c in s if not c.isspace()]
    return sum(c.isdigit() for c in chars) / len(chars) < 0.30


def repair_isin_overflow(row: list[str]) -> list[str]:
    """A long ISIN can overflow into the next cell: 'INE917S070328.2' | '5% SIFL..' (LLD §4.2)."""
    row = list(row)
    for j in range(len(row) - 1):
        c = row[j]
        if len(c) > 12 and re.match(ISIN_RE.pattern, c) and isin_check_digit_ok(c[:12]):
            row[j], row[j + 1] = c[:12], c[12:] + row[j + 1]
    return row


def _share(flags: list[bool]) -> float:
    return sum(flags) / len(flags) if flags else 0.0


def classify_table(rows: list[list[str]]) -> TableKind:
    rows = [r for r in rows if any(r)]
    if not rows:
        return "UNKNOWN"
    ncols = max(len(r) for r in rows)
    col0 = _share([label_like(r[0]) for r in rows if r[0]])
    if ncols == 2 and col0 >= 0.8:
        return "KV"
    if ncols == 4 and _share([label_like(r[0]) and label_like(r[2]) for r in rows if r[0]]) >= 0.8:
        return "KV_ROWS"
    merged = any(not c for r in rows for c in r[1:])
    if ncols >= 3 and len(rows) >= 3 and col0 >= 0.8 and merged:
        return "KV_ROWS"  # label | value [| label | value] with merged cells
    header = [c for c in rows[0] if c]
    if len(rows) >= 2 and len(header) >= 2 and all(label_like(c) for c in header):
        return "GRID"
    return "UNKNOWN"


_COLON = re.compile(r"^(?P<k>[^:|]{1,60}?)\s*(?::-\s*|:\s+|:(?=[A-Za-z]))(?P<v>\S.*)$")
_DASH = re.compile(r"^(?P<k>[A-Za-z][A-Za-z .&/()]{0,40}?)\s+[-–—]\s+(?P<v>\S.*)$")
_ID_TOKEN = re.compile(r"[A-Z]{5}\d{4}[A-Z]|[A-Z]{2}[A-Z0-9]{9}\d")  # PAN, ISIN
_VALUE_TOKEN = re.compile(rf"{_ID_TOKEN.pattern}|\d[\d,]*(\.\d+)?")  # PAN, ISIN, number


def _colon_pair(text: str) -> tuple[str, str] | None:
    m = _COLON.match(text.strip())
    if m and label_like(m["k"]) and m["v"].strip():
        return m["k"].strip(), m["v"].strip()
    return None


def inline_pair(cell: str) -> tuple[str, str] | None:
    """A cell holding both label and value: 'SELLER PAN No. ABCPD1234E', 'PAN:- X', 'Ref: X'."""
    if pair := _colon_pair(cell):
        return pair
    head, _, last = cell.rpartition(" ")
    if head and label_like(head) and head.count("(") == head.count(")") and _VALUE_TOKEN.fullmatch(last):
        return head.strip(), last.strip()
    return None


def _strong_inline(cell: str) -> bool:
    """Label+value in one cell even when another cell follows: 'X: value' or a trailing PAN / ISIN."""
    return _colon_pair(cell) is not None or bool(_ID_TOKEN.fullmatch(cell.rpartition(" ")[2]))


def sub_pairs(raw_value: str) -> list[tuple[str, str]]:
    """Several 'Label : value' / 'Label - value' parts inside one value cell.

    'CM BP ID : X | Market type : ICDM(T+0) | CM Name : ICCL' -> 3 pairs;
    'Date - Monday, July 27, 2020\\nLevel - 11131.80' -> 2 pairs.
    Only cells with at least two labelled parts are split (a wrapped name such as
    'Our Buy from:- SAHYADRI INVESTMENTS PRIVATE\\nLTD' stays whole).
    """
    parts = [p.strip() for p in re.split(r"\n|\s\|\s", raw_value) if p.strip()]
    if len(parts) < 2:
        return []
    colon = [pair for p in parts if (pair := _colon_pair(p))]
    dash = [(m["k"].strip(), m["v"].strip()) for p in parts if (m := _DASH.match(p)) and label_like(m["k"])]
    if len(colon) >= 2:
        return colon
    if len(dash) >= 2:
        return dash
    mixed = colon + [d for d in dash if d[0] not in {c[0] for c in colon}]
    return mixed if len(mixed) >= 2 else []


# --------------------------------------------------------------------------- tables


def _bbox(b: Any) -> BBox | None:
    if b is None:
        return None
    x0, top, x1, bottom = b
    return (round(float(x0), 1), round(float(top), 1), round(float(x1), 1), round(float(bottom), 1))


def _emit_value(
    out: list[KeyValue],
    key: str,
    raw_value: str,
    source: Source,
    page: int,
    key_bbox: BBox | None,
    value_bbox: BBox | None,
) -> None:
    value = clean_cell(raw_value)
    if not key or not value:
        return
    out.append(KeyValue(key, value, source, page, key_bbox, value_bbox))
    for sub_key, sub_value in sub_pairs(raw_value):
        # Parent label in parentheses keeps context; lookup also tries the key without it.
        out.append(KeyValue(f"{sub_key} ({key})", sub_value, source, page, value_bbox, value_bbox))


def _kv_rows(
    rows_raw: list[list[str | None]], cells: list[list[Any]], page: int, out: list[KeyValue]
) -> None:
    for raw, cbox in zip(rows_raw, cells, strict=False):
        filled = [(j, c) for j, c in enumerate(raw) if clean_cell(c)]
        i = 0
        while i < len(filled):
            j, label_raw = filled[i]
            label = clean_cell(label_raw)
            nxt = filled[i + 1] if i + 1 < len(filled) else None
            if nxt and label_like(label) and not _strong_inline(label):
                k = nxt[0]
                _emit_value(out, label, nxt[1] or "", Source.table, page, _bbox(cbox[j]), _bbox(cbox[k]))
                i += 2
                continue
            if pair := inline_pair(label):  # label and value share one cell
                out.append(KeyValue(pair[0], pair[1], Source.table, page, _bbox(cbox[j]), _bbox(cbox[j])))
            i += 1


def _grid_rows(rows: list[list[str]], cells: list[list[Any]], page: int, out: list[KeyValue]) -> None:
    header, body = rows[0], rows[1:]
    if len(body) == 1:
        for j, h in enumerate(header):
            if h and j < len(body[0]) and body[0][j]:
                out.append(KeyValue(h, body[0][j], Source.grid, page, _bbox(cells[0][j]), _bbox(cells[1][j])))
        return
    for i, r in enumerate(body, start=1):  # multi-row grid, e.g. repo legs: key = row label + header
        for j in range(1, len(header)):
            if header[j] and j < len(r) and r[j]:
                key = f"{r[0]} {header[j]}".strip()
                out.append(KeyValue(key, r[j], Source.grid, page, _bbox(cells[0][j]), _bbox(cells[i][j])))


def _table_pairs(table: Any, page: int) -> list[KeyValue]:
    raw = [list(r) for r in table.extract()]
    cells = [list(r.cells) for r in table.rows]
    rows = [repair_isin_overflow([clean_cell(c) for c in r]) for r in raw]
    kind = classify_table(rows)
    out: list[KeyValue] = []
    if kind == "KV" or kind == "KV_ROWS":
        _kv_rows(raw, cells, page, out)
    elif kind == "GRID":
        keep = [i for i, r in enumerate(rows) if any(r)]
        _grid_rows([rows[i] for i in keep], [cells[i] for i in keep], page, out)
    return out


# --------------------------------------------------------------------------- text lines


def _inside(word: dict[str, Any], boxes: list[BBox]) -> bool:
    cx, cy = (word["x0"] + word["x1"]) / 2, (word["top"] + word["bottom"]) / 2
    return any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes)


def _words_bbox(words: list[dict[str, Any]]) -> BBox:
    return _bbox(
        (
            min(w["x0"] for w in words),
            min(w["top"] for w in words),
            max(w["x1"] for w in words),
            max(w["bottom"] for w in words),
        )
    )  # type: ignore[return-value]


def _text_pairs(page: Any, page_no: int, exclude: list[BBox]) -> list[KeyValue]:
    words = [w for w in page.extract_words(x_tolerance=1.5, y_tolerance=2) if not _inside(w, exclude)]
    lines: list[list[dict[str, Any]]] = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) <= 3:
            lines[-1].append(w)
        else:
            lines.append([w])

    out: list[KeyValue] = []
    for line in lines:
        line.sort(key=lambda w: w["x0"])
        segs: list[list[dict[str, Any]]] = []
        for w in line:
            if w["text"] == "|":
                segs.append([])
                continue
            if segs and segs[-1] and w["x0"] - segs[-1][-1]["x1"] <= GAP_PT:
                segs[-1].append(w)
            else:
                segs.append([w])
        segs = [s for s in segs if s]
        texts = [" ".join(w["text"] for w in s) for s in segs]

        found = False
        for s, t in zip(segs, texts, strict=True):  # 1) colon pairs (several per line allowed)
            if pair := _colon_pair(t):
                out.append(KeyValue(pair[0], pair[1], Source.text, page_no, _words_bbox(s), _words_bbox(s)))
                found = True
        if found:
            continue
        if len(segs) == 2 and label_like(texts[0]) and not label_like(texts[1]):  # 2) "Label    value"
            out.append(
                KeyValue(texts[0], texts[1], Source.text, page_no, _words_bbox(segs[0]), _words_bbox(segs[1]))
            )
    return out


# --------------------------------------------------------------------------- prose

_PROSE: list[tuple[str, re.Pattern[str]]] = [
    ("Bid Type", re.compile(r"\b(non[- ]?competitive|competitive)\s+bid\b", re.I)),
    ("Tenor", re.compile(r"\b(\d{2,3})[- ]day\s+(?:treasury bill|t-?bill|cash management bill)", re.I)),
    ("Yield", re.compile(r"at a yield of ([\d.]+)\s*%", re.I)),
    ("Counterparty", re.compile(r"conducted by (?:the )?(Reserve Bank of India)", re.I)),
    ("Dealer", re.compile(r"\(([A-Z]\.\s?[A-Z][A-Za-z]+)\)\s*\n\s*Dealer\b")),
]


def _prose_pairs(text: str) -> list[KeyValue]:
    out: list[KeyValue] = []
    for isin in dict.fromkeys(re.findall(rf"\b{ISIN_RE.pattern}\b", text)):
        if isin_check_digit_ok(isin):
            out.append(KeyValue("ISIN", isin, Source.prose, 1, None, None))
    for key, rx in _PROSE:
        if m := rx.search(text):
            out.append(KeyValue(key, m.group(1), Source.prose, 1, None, None))
    return out


# --------------------------------------------------------------------------- document


def _open(pdf: Path | bytes) -> Any:
    try:
        return pdfplumber.open(io.BytesIO(pdf) if isinstance(pdf, bytes) else pdf)
    except (PDFPasswordIncorrect, PDFEncryptionError) as exc:
        raise EncryptedPdfError() from exc
    except PdfminerException as exc:
        inner = exc.args[0] if exc.args else None
        if isinstance(inner, (PDFPasswordIncorrect, PDFEncryptionError)):
            raise EncryptedPdfError() from exc
        raise InvalidPdfError() from exc
    except Exception as exc:  # pdfminer raises many types for corrupt files
        raise InvalidPdfError() from exc


def extract(pdf: Path | bytes, *, max_pages: int) -> ExtractedDocument:
    with _open(pdf) as doc:
        if len(doc.pages) > max_pages:
            raise TooManyPagesError(f"PDF has {len(doc.pages)} pages; the limit is {max_pages}")
        pairs: list[KeyValue] = []
        texts: list[str] = []
        sizes: list[tuple[float, float]] = []
        table_boxes: list[list[BBox]] = []
        page_words: list[list[Word]] = []
        try:
            for page_no, page in enumerate(doc.pages, start=1):
                sizes.append((round(float(page.width), 1), round(float(page.height), 1)))
                tables = page.find_tables()
                boxes = [b for t in tables if (b := _bbox(t.bbox))]
                table_boxes.append(boxes)
                for t in tables:
                    pairs.extend(_table_pairs(t, page_no))
                pairs.extend(_text_pairs(page, page_no, boxes))
                texts.append(page.extract_text() or "")
                page_words.append(
                    [
                        (w["text"], b)
                        for w in page.extract_words(x_tolerance=1.5, y_tolerance=2)
                        if (b := _bbox((w["x0"], w["top"], w["x1"], w["bottom"])))
                    ]
                )
        except Exception as exc:
            raise InvalidPdfError("PDF content cannot be read") from exc

    text = "\f".join(texts)
    has_text = sum(not c.isspace() for c in text) >= MIN_TEXT_CHARS
    if has_text:
        pairs.extend(_prose_pairs(text))
    return ExtractedDocument(
        pairs=pairs if has_text else [],
        text=text,
        pages=len(sizes),
        page_sizes=sizes,
        table_bboxes=table_boxes,
        words=page_words,
        has_text_layer=has_text,
    )

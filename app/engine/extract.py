"""PDF -> ExtractedDocument of label/value candidates with page positions (docs/03_lld.md §3.9, §4.1-4.4).

PH1 reads ruled label|value tables. Grids, 4-column label|value tables, text lines and prose
are added in PH2 (docs/14_task_breakdown.md T2.1).
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
from app.engine.models import BBox, ExtractedDocument, KeyValue
from app.errors import EncryptedPdfError, InvalidPdfError, TooManyPagesError

MIN_TEXT_CHARS = 20  # fewer non-whitespace characters => no usable text layer (scan) [LLD A5]

TableKind = Literal["KV", "KV2", "GRID", "UNKNOWN"]


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


def _share(flags: list[bool]) -> float:
    return sum(flags) / len(flags) if flags else 0.0


def classify_table(rows: list[list[str]]) -> TableKind:
    rows = [r for r in rows if any(r)]
    if not rows:
        return "UNKNOWN"
    ncols = max(len(r) for r in rows)
    if ncols == 2 and _share([label_like(r[0]) for r in rows if r[0]]) >= 0.8:
        return "KV"
    if ncols == 4 and _share([label_like(r[0]) and label_like(r[2]) for r in rows if r[0]]) >= 0.8:
        return "KV2"
    header = [c for c in rows[0] if c]
    if len(rows) >= 2 and len(header) >= 2 and all(label_like(c) for c in header):
        return "GRID"
    return "UNKNOWN"


def _bbox(b: Any) -> BBox | None:
    if b is None:
        return None
    x0, top, x1, bottom = b
    return (round(x0, 1), round(top, 1), round(x1, 1), round(bottom, 1))


def _table_pairs(table: Any, page_no: int) -> list[KeyValue]:
    rows = [[clean_cell(c) for c in r] for r in table.extract()]
    cells = [list(r.cells) for r in table.rows]
    if classify_table(rows) != "KV":
        return []  # KV2 / GRID: PH2
    pairs = []
    for r, c in zip(rows, cells, strict=True):
        if len(r) >= 2 and r[0] and r[1]:
            pairs.append(KeyValue(r[0], r[1], Source.table, page_no, _bbox(c[0]), _bbox(c[1])))
    return pairs


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
        try:
            for page_no, page in enumerate(doc.pages, start=1):
                sizes.append((round(float(page.width), 1), round(float(page.height), 1)))
                tables = page.find_tables()
                table_boxes.append([b for t in tables if (b := _bbox(t.bbox))])
                for t in tables:
                    pairs.extend(_table_pairs(t, page_no))
                texts.append(page.extract_text() or "")
        except Exception as exc:
            raise InvalidPdfError("PDF content cannot be read") from exc

    text = "\f".join(texts)
    has_text = sum(not c.isspace() for c in text) >= MIN_TEXT_CHARS
    return ExtractedDocument(
        pairs=pairs if has_text else [],
        text=text,
        pages=len(sizes),
        page_sizes=sizes,
        table_bboxes=table_boxes,
        has_text_layer=has_text,
    )

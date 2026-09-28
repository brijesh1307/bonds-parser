from __future__ import annotations

from pathlib import Path

import pytest

from app.engine.extract import classify_table, clean_cell, extract, label_like
from app.engine.fields import Source
from app.errors import EncryptedPdfError, InvalidPdfError, TooManyPagesError
from tests.conftest import make_pdf


def test_mock01_yields_all_24_label_value_pairs_with_positions(slip01: Path) -> None:
    doc = extract(slip01, max_pages=20)
    assert doc.has_text_layer
    assert doc.pages == 1
    assert doc.page_sizes == [(595.3, 841.9)]  # A4 in points
    table = [p for p in doc.pairs if p.source is Source.table]
    assert len(table) == 24
    assert [(p.key, p.value) for p in doc.pairs if p.source is Source.prose] == [("ISIN", "IN0020240A75")]
    doc.pairs = table
    first = doc.pairs[0]
    assert (first.key, first.value) == ("Deal Reference No.", "GS/NDSOM/2026/004571")
    for p in doc.pairs:
        assert p.key_bbox is not None and p.value_bbox is not None
        x0, top, x1, bottom = p.value_bbox
        assert 0 <= x0 < x1 <= 595.3 and 0 <= top < bottom <= 841.9
        assert p.key_bbox[2] <= x0  # label cell is left of the value cell
    by_key = {p.key: p.value for p in doc.pairs}
    assert by_key["Face Value (INR)"] == "5,00,00,000.00"
    assert by_key["Accrued Interest Days"] == "167 (30/360)"
    assert "MOCK DATA" in doc.text


def test_accepts_bytes(slip01: Path) -> None:
    assert len(extract(slip01.read_bytes(), max_pages=20).pairs) == 25


def test_page_without_text_is_unreadable() -> None:
    doc = extract(make_pdf(text=None), max_pages=20)
    assert not doc.has_text_layer
    assert doc.pairs == []


def test_too_many_pages() -> None:
    with pytest.raises(TooManyPagesError):
        extract(make_pdf(pages=3), max_pages=2)


def test_encrypted_pdf() -> None:
    with pytest.raises(EncryptedPdfError):
        extract(make_pdf(encrypt="secret"), max_pages=20)


def test_corrupt_pdf() -> None:
    with pytest.raises(InvalidPdfError):
        extract(b"%PDF-1.4\nthis is not really a pdf", max_pages=20)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Face Value (INR)", True),
        ("SGL / CSGL A/c", True),
        ("Cut-off Price (per INR 100)", True),
        ("5,00,00,000.00", False),
        ("IN0020240A75", False),
        ("", False),
        ("x" * 61, False),
    ],
)
def test_label_like(text: str, expected: bool) -> None:
    assert label_like(text) is expected


def test_clean_cell() -> None:
    assert clean_cell(None) == ""
    assert clean_cell("  Deal\n Type ") == "Deal Type"
    assert clean_cell("________________") == ""


def test_classify_table() -> None:
    assert classify_table([["Deal ID", "X1"], ["Trade Date", "24-Sep-2026"]]) == "KV"
    assert (
        classify_table([["ISIN", "Coupon", "Maturity"], ["IN0020240A75", "7.10%", "08-Apr-2034"]]) == "GRID"
    )
    assert classify_table([["Deal ID", "X1", "Trade Date", "24-Sep-2026"]]) == "KV_ROWS"
    assert classify_table([["1", "2", "3"], ["4", "5", "6"]]) == "UNKNOWN"

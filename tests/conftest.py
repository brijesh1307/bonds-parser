"""Shared test fixtures: mock slip paths and small generated PDFs."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
MOCK_DIR = ROOT / "samples" / "mock"
EXPECTED_DIR = MOCK_DIR / "expected"


def mock_pdf(stem: str) -> Path:
    return MOCK_DIR / f"{stem}.pdf"


def make_pdf(
    *, pages: int = 1, text: str | None = "Plain text page with enough characters", **kw: object
) -> bytes:
    """A small PDF: optional text on each page; `encrypt=` makes it password-protected."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, **kw)  # type: ignore[arg-type]
    for _ in range(pages):
        if text:
            c.drawString(72, 750, text)
        else:
            c.rect(72, 600, 200, 100, fill=1)  # image-like content, no text layer
        c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture
def slip01() -> Path:
    return mock_pdf("01_gsec_outright_purchase")

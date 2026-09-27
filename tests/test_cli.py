from __future__ import annotations

from pathlib import Path

import pytest

from app.cli import main
from tests.conftest import make_pdf


def test_debug_extract_lists_pairs_and_mappings(slip01: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["debug-extract", str(slip01)]) == 0
    out = capsys.readouterr().out
    assert "1 page(s)" in out
    assert "Deal Reference No." in out
    assert "deal_id (synonym 0.95)" in out
    assert "-- unmapped --" in out  # Trade Time, SGL / CSGL A/c
    assert "24 pairs, 22 mapped, 2 unmapped" in out


def test_debug_extract_scanned(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(make_pdf(text=None))
    assert main(["debug-extract", str(pdf)]) == 0
    assert "No text layer" in capsys.readouterr().out


def test_debug_extract_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["debug-extract", str(tmp_path / "missing.pdf")]) == 2
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 garbage")
    assert main(["debug-extract", str(bad)]) == 1
    assert "PDF cannot be read" in capsys.readouterr().err

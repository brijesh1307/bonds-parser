"""Regenerate docs/schemas/parse_result.xsd from the response models.

Usage:  python tools/build_xsd.py
A test fails when the committed file differs from what the code generates.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.export.xsd import build_xsd  # noqa: E402

OUT = ROOT / "docs" / "schemas" / "parse_result.xsd"

if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build_xsd(), encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)}")

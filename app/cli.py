"""Command line: python -m app.cli <command> (docs/03_lld.md §3.4, docs/13_development_plan.md §3).

PH1: debug-extract. init-db and client management (add-client, list-clients, disable-client,
rotate-secret) arrive with the database and auth (PH3, PH4).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from app.config import Settings
from app.engine.extract import extract
from app.engine.mapping import lookup_label
from app.errors import AppError


def _fmt_bbox(b: tuple[float, float, float, float] | None) -> str:
    return "-" if b is None else "[{:.1f}, {:.1f}, {:.1f}, {:.1f}]".format(*b)


def debug_extract(pdf: Path, max_pages: int) -> int:
    """Print every extracted key/value with source, page, position and the field it maps to."""
    try:
        doc = extract(pdf, max_pages=max_pages)
    except AppError as exc:
        print(f"error: {exc.title}: {exc.detail}", file=sys.stderr)
        return 1

    print(f"{pdf.name}: {doc.pages} page(s), sizes {doc.page_sizes}, text layer: {doc.has_text_layer}")
    if not doc.has_text_layer:
        print("No text layer: this looks like a scanned slip (status UNREADABLE, OCR needed).")
        return 0
    rows = []
    for kv in doc.pairs:
        hit = lookup_label(kv.key)
        target = f"{hit[0]} ({hit[1].value} {hit[2]:.2f})" if hit else "-- unmapped --"
        rows.append((kv.source.value, str(kv.page), kv.key, kv.value, target, _fmt_bbox(kv.value_bbox)))
    headers = ("source", "page", "key", "value", "maps to", "value bbox")
    widths = [min(max(len(r[i]) for r in [headers, *rows]), 40) for i in range(len(headers))]
    line = "  ".join(f"{{:<{w}}}" for w in widths)
    print(line.format(*headers))
    print(line.format(*("-" * w for w in widths)))
    for r in rows:
        print(line.format(*(c if len(c) <= 40 else c[:37] + "..." for c in r)))
    mapped = sum(1 for kv in doc.pairs if lookup_label(kv.key))
    print(f"\n{len(doc.pairs)} pairs, {mapped} mapped, {len(doc.pairs) - mapped} unmapped")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Bonds Deal Slip Parser tools")
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("debug-extract", help="show what the parser reads from a PDF")
    d.add_argument("pdf", type=Path)
    args = parser.parse_args(argv)

    if args.command == "debug-extract":
        if not args.pdf.is_file():
            print(f"error: file not found: {args.pdf}", file=sys.stderr)
            return 2
        return debug_extract(args.pdf, Settings.from_env().max_pages)
    return 2  # pragma: no cover - argparse rejects unknown commands


if __name__ == "__main__":
    sys.exit(main())

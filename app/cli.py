"""Command line: python -m app.cli <command> (docs/03_lld.md §3.4, docs/13_development_plan.md §3).

Commands: debug-extract, add-client, list-clients, disable-client, rotate-secret, verify-audit.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Sequence
from pathlib import Path

from app.auth import ClientStore
from app.config import Settings
from app.engine.extract import extract
from app.engine.mapping import lookup_label
from app.errors import AppError
from app.services.audit_service import AuditLog


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
        hits = lookup_label(kv.key)
        target = ", ".join(f"{p} ({m.value} {c:.2f})" for p, m, c in hits) if hits else "-- unmapped --"
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


def _audit_cli(settings: Settings, action: str, client_id: str) -> None:
    AuditLog(settings.audit_path).record(
        action, actor_type="cli", actor_id=getpass.getuser(), entity_type="client", entity_id=client_id
    )


def _clients(args: argparse.Namespace, settings: Settings) -> int:
    store = ClientStore(settings.clients_path)
    try:
        if args.command == "add-client":
            secret = store.add(args.client_id, args.description)
            _audit_cli(settings, "CLIENT_ADDED", args.client_id)
            print(f"client: {args.client_id}\nsecret: {secret}\n")
            print("Store the secret now: it is shown only once (only its Argon2 hash is kept).")
            return 0
        if args.command == "list-clients":
            for c in store.list():
                print(
                    f"{c['client_id']:<24} active={c['is_active']!s:<5} created={c['created_at']} "
                    f"last_used={c['last_used_at']} rotated={c['rotated_at']}"
                )
            return 0
        if args.command == "disable-client":
            store.disable(args.client_id)
            _audit_cli(settings, "CLIENT_DISABLED", args.client_id)
            print(f"client {args.client_id} disabled")
            return 0
        if args.command == "rotate-secret":
            secret = store.rotate(args.client_id)
            _audit_cli(settings, "CLIENT_SECRET_ROTATED", args.client_id)
            print(f"client: {args.client_id}\nnew secret: {secret}\n\nThe old secret no longer works.")
            return 0
    except ValueError as exc:  # invalid client id
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except AppError as exc:  # exists / not found
        print(f"error: {exc.detail}", file=sys.stderr)
        return 1
    return 2  # pragma: no cover


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Bonds Deal Slip Parser tools")
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("debug-extract", help="show what the parser reads from a PDF")
    d.add_argument("pdf", type=Path)
    a = sub.add_parser("add-client", help="create an API client (prints the secret once)")
    a.add_argument("client_id")
    a.add_argument("--description")
    sub.add_parser("list-clients", help="list API clients (never shows secrets)")
    for name, text in (("disable-client", "disable an API client"), ("rotate-secret", "issue a new secret")):
        sub.add_parser(name, help=text).add_argument("client_id")
    sub.add_parser("verify-audit", help="verify the audit log hash chain")
    args = parser.parse_args(argv)
    settings = Settings.from_env()

    if args.command == "debug-extract":
        if not args.pdf.is_file():
            print(f"error: file not found: {args.pdf}", file=sys.stderr)
            return 2
        return debug_extract(args.pdf, settings.max_pages)
    if args.command == "verify-audit":
        result = AuditLog(settings.audit_path).verify()
        print(
            ("OK" if result["ok"] else "BROKEN")
            + f": {result['checked']} record(s) verified"
            + ("" if result["ok"] else f"; first bad seq {result['first_bad_seq']} ({result['reason']})")
        )
        return 0 if result["ok"] else 1
    return _clients(args, settings)


if __name__ == "__main__":
    sys.exit(main())

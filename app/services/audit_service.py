"""Append-only, hash-chained audit log in a JSON-lines file (baseline §6, ADR-0006, ADR-0009).

Each line is one record; row_hash = SHA-256 of the canonical JSON of all other fields, and
prev_hash links to the previous line (64 zeros for the first). Editing or deleting any line
breaks the chain, which `verify()` reports. Records hold metadata only: no file names, no deal
values; anything shaped like a PAN is masked defensively.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GENESIS_HASH = "0" * 64
_PAN = re.compile(r"\b([A-Z]{5})(\d{4})([A-Z])\b")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def row_hash(record: dict[str, Any]) -> str:
    body = {k: v for k, v in record.items() if k != "row_hash"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        return _PAN.sub(lambda m: f"{m[1]}****{m[3]}", value)
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._last: tuple[int, str] | None = None  # (seq, row_hash) of the last line

    def _tail(self) -> tuple[int, str]:
        if self._last is None:
            seq, last = 0, GENESIS_HASH
            for rec in self._iter():
                seq, last = rec["seq"], rec["row_hash"]
            self._last = (seq, last)
        return self._last

    def record(
        self,
        action: str,
        *,
        actor_type: str = "client",
        actor_id: str | None = None,
        actor_name: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        outcome: str = "SUCCESS",
        error: str | None = None,
        details: dict[str, Any] | None = None,
        request_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            seq, prev = self._tail()
            rec: dict[str, Any] = {
                "seq": seq + 1,
                "event_id": uuid.uuid4().hex,
                "occurred_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "actor_type": actor_type,
                "actor_id": actor_id,
                "actor_name": _scrub(actor_name),
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "outcome": outcome,
                "error": _scrub(error),
                "details": _scrub(details or {}),
                "request_id": request_id,
                "ip_address": ip_address,
                "user_agent": user_agent[:512] if user_agent else None,
                "prev_hash": prev,
            }
            rec["row_hash"] = row_hash(rec)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as f:  # append-only
                f.write(canonical_json(rec) + "\n")
                f.flush()
                os.fsync(f.fileno())
            self._last = (rec["seq"], rec["row_hash"])
            return rec

    def _iter(self) -> Iterator[dict[str, Any]]:
        if not self.path.is_file():
            return
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)

    def search(
        self,
        *,
        action: str | None = None,
        actor_id: str | None = None,
        entity_id: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Newest first. Dates are ISO prefixes compared against occurred_at (UTC)."""
        hits = [
            r
            for r in self._iter()
            if (action is None or r["action"] == action)
            and (actor_id is None or r["actor_id"] == actor_id)
            and (entity_id is None or r["entity_id"] == entity_id)
            and (date_from is None or r["occurred_at"][: len(date_from)] >= date_from)
            and (date_to is None or r["occurred_at"][: len(date_to)] <= date_to)
        ]
        hits.reverse()
        return hits[offset : offset + limit], len(hits)

    def records(self, *, date_from: str | None = None, date_to: str | None = None) -> list[dict[str, Any]]:
        """All records in file order (oldest first), optionally limited to an ISO date / time range."""
        return [
            r
            for r in self._iter()
            if (date_from is None or r["occurred_at"][: len(date_from)] >= date_from)
            and (date_to is None or r["occurred_at"][: len(date_to)] <= date_to)
        ]

    def verify(self) -> dict[str, Any]:
        prev, checked, last = GENESIS_HASH, 0, None
        for line_no, rec in enumerate(self._iter(), start=1):
            if rec.get("prev_hash") != prev:
                return {
                    "ok": False,
                    "checked": checked,
                    "first_bad_seq": rec.get("seq", line_no),
                    "reason": "prev_hash_mismatch",
                    "last_hash": last,
                }
            if row_hash(rec) != rec.get("row_hash"):
                return {
                    "ok": False,
                    "checked": checked,
                    "first_bad_seq": rec.get("seq", line_no),
                    "reason": "row_hash_mismatch",
                    "last_hash": last,
                }
            prev = last = rec["row_hash"]
            checked += 1
        return {"ok": True, "checked": checked, "first_bad_seq": None, "reason": None, "last_hash": last}

    def writable(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8"):
                pass
            return True
        except OSError:
            return False

"""File-based template store: one JSON file per template, all versions inside (baseline §6, ADR-0009).

Writes are atomic (temp file + rename) and serialised by a process lock; versions are never
overwritten. Files hold labels and rules only - never PDFs, deal values or PANs.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.engine.models import TemplateDefinition
from app.errors import ConflictError, NotFoundError

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,99}$")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:100]
    return slug or "template"


class TemplateStore:
    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ read

    def _path(self, template_id: str) -> Path:
        if not _ID_RE.match(template_id):
            raise NotFoundError(f"template {template_id!r} not found")
        return self.dir / f"{template_id}.json"

    def exists(self, template_id: str) -> bool:
        return _ID_RE.match(template_id) is not None and self._path(template_id).is_file()

    def get(self, template_id: str) -> dict[str, Any]:
        path = self._path(template_id)
        if not path.is_file():
            raise NotFoundError(f"template {template_id!r} not found")
        record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return record

    def find(self, *, is_active: bool | None = None, market: str | None = None) -> list[dict[str, Any]]:
        if not self.dir.is_dir():
            return []
        out = []
        for path in sorted(self.dir.glob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            if is_active is not None and record["is_active"] is not is_active:
                continue
            if market and record["market"] != market:
                continue
            out.append(record)
        return out

    @staticmethod
    def current(record: dict[str, Any]) -> dict[str, Any]:
        version: dict[str, Any] = next(
            v for v in record["versions"] if v["version"] == record["current_version"]
        )
        return version

    def active_definitions(self) -> list[TemplateDefinition]:
        return [
            TemplateDefinition.from_json(self.current(r)["definition"], name=r["name"])
            for r in self.find(is_active=True)
        ]

    # ------------------------------------------------------------------ write

    def _write(self, record: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self._path(record["id"])
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(record, f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def new_id(self, name: str) -> str:
        base = slugify(name)
        candidate, n = base, 2
        while self.exists(candidate):
            candidate, n = f"{base}_{n}", n + 1
        return candidate

    def create(
        self,
        *,
        template_id: str,
        name: str,
        description: str | None,
        definition: TemplateDefinition,
        actor: str,
        note: str | None,
    ) -> dict[str, Any]:
        with self._lock:
            if self.exists(template_id):
                raise ConflictError(f"template {template_id!r} already exists")
            now = _now()
            record = {
                "id": template_id,
                "name": name,
                "description": description,
                "market": definition.market,
                "is_active": True,
                "current_version": definition.version,
                "created_by": actor,
                "created_at": now,
                "updated_at": now,
                "versions": [
                    {
                        "version": definition.version,
                        "definition": definition.to_json(),
                        "note": note,
                        "created_by": actor,
                        "created_at": now,
                    }
                ],
            }
            self._write(record)
            return record

    def add_version(
        self, template_id: str, definition: TemplateDefinition, *, actor: str, note: str | None
    ) -> dict[str, Any]:
        with self._lock:
            record = self.get(template_id)
            version = record["current_version"] + 1
            if definition.version != version or definition.template_id != template_id:
                raise ConflictError(f"next version of {template_id!r} is {version}")
            now = _now()
            record["versions"].append(
                {
                    "version": version,
                    "definition": definition.to_json(),
                    "note": note,
                    "created_by": actor,
                    "created_at": now,
                }
            )
            record.update(current_version=version, market=definition.market, updated_at=now)
            self._write(record)
            return record

    def patch(self, template_id: str, changes: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Apply name / description / is_active; returns (record, {field: (old, new)})."""
        with self._lock:
            record = self.get(template_id)
            diff = {k: (record[k], v) for k, v in changes.items() if v is not None and record.get(k) != v}
            for k, (_, new) in diff.items():
                record[k] = new
            if diff:
                record["updated_at"] = _now()
                self._write(record)
            return record, diff

    def writable(self) -> bool:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            probe = self.dir / ".write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return True
        except OSError:
            return False

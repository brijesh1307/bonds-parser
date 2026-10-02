"""HTTP Basic client credentials, Argon2, file-based client store, brute-force limiter
(baseline D8, docs/07_security.md §2).

Clients live in BONDS_CLIENTS_FILE as Argon2 hashes; a secret is shown once, when created or
rotated. Failed attempts per IP are counted in memory; too many within the window block the IP
(429). Credentials and hashes are never logged or audited.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import threading
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.errors import AuthBlockedError, ConflictError, NotFoundError

CLIENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_hasher = PasswordHasher()  # argon2id, library defaults
_DUMMY_HASH = _hasher.hash("timing-equaliser")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class ClientStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._last_touch: dict[str, float] = {}

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.is_file():
            return {}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return {c["client_id"]: c for c in data.get("clients", [])}

    def _save(self, clients: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"clients": sorted(clients.values(), key=lambda c: c["client_id"])}, f, indent=2)
                f.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def list(self) -> list[dict[str, Any]]:
        return [{k: v for k, v in c.items() if k != "secret_hash"} for c in self._load().values()]

    def add(self, client_id: str, description: str | None = None) -> str:
        if not CLIENT_ID_RE.match(client_id):
            raise ValueError("client id: 2-64 characters, a-z 0-9 _ -, starting with a letter or digit")
        with self._lock:
            clients = self._load()
            if client_id in clients:
                raise ConflictError(f"client {client_id!r} already exists")
            secret = secrets.token_urlsafe(32)
            clients[client_id] = {
                "client_id": client_id,
                "secret_hash": _hasher.hash(secret),
                "description": description,
                "is_active": True,
                "created_at": _now(),
                "last_used_at": None,
                "rotated_at": None,
            }
            self._save(clients)
            return secret

    def disable(self, client_id: str) -> None:
        with self._lock:
            clients = self._load()
            if client_id not in clients:
                raise NotFoundError(f"client {client_id!r} not found")
            clients[client_id]["is_active"] = False
            self._save(clients)

    def rotate(self, client_id: str) -> str:
        with self._lock:
            clients = self._load()
            if client_id not in clients:
                raise NotFoundError(f"client {client_id!r} not found")
            secret = secrets.token_urlsafe(32)
            clients[client_id].update(secret_hash=_hasher.hash(secret), rotated_at=_now())
            self._save(clients)
            return secret

    def verify(self, client_id: str, secret: str) -> tuple[bool, str]:
        """(ok, reason). Runs one Argon2 verification even for unknown clients (no timing hint)."""
        client = self._load().get(client_id)
        try:
            _hasher.verify(client["secret_hash"] if client else _DUMMY_HASH, secret)
            matched = client is not None
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            matched = False
        if client is None:
            return False, "UNKNOWN_CLIENT"
        if not matched:
            return False, "BAD_SECRET"
        if not client.get("is_active", False):
            return False, "CLIENT_DISABLED"
        self._touch(client_id)
        return True, "OK"

    def _touch(self, client_id: str) -> None:
        """Persist last_used_at at most once a minute per client."""
        now = time.monotonic()
        if now - self._last_touch.get(client_id, -1e9) < 60:
            return
        self._last_touch[client_id] = now
        with self._lock:
            clients = self._load()
            if client_id in clients:
                clients[client_id]["last_used_at"] = _now()
                self._save(clients)


class FailureLimiter:
    """Per-IP failed-auth counter: max_failures within window_seconds blocks the IP for block_seconds."""

    def __init__(self, max_failures: int, window_seconds: int, block_seconds: int) -> None:
        self.max_failures, self.window, self.block = max_failures, window_seconds, block_seconds
        self._failures: dict[str, deque[float]] = {}
        self._blocked: dict[str, float] = {}
        self._lock = threading.Lock()

    def check(self, ip: str) -> None:
        with self._lock:
            until = self._blocked.get(ip)
            if until and until > time.monotonic():
                raise AuthBlockedError(int(until - time.monotonic()) + 1)

    def fail(self, ip: str) -> bool:
        """Record a failure; True when this failure blocks the IP."""
        now = time.monotonic()
        with self._lock:
            q = self._failures.setdefault(ip, deque())
            q.append(now)
            while q and q[0] < now - self.window:
                q.popleft()
            if len(q) >= self.max_failures:
                self._blocked[ip] = now + self.block
                q.clear()
                return True
            return False

    def success(self, ip: str) -> None:
        with self._lock:
            self._failures.pop(ip, None)

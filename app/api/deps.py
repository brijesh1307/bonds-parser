"""Shared request dependencies: authentication and request context (docs/03_lld.md §3.5)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.auth import ClientStore, FailureLimiter
from app.config import Settings
from app.errors import AuthError
from app.services.audit_service import AuditLog
from app.services.template_store import TemplateStore

basic = HTTPBasic(auto_error=False, description="Client credentials: client_id / client_secret")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class RequestContext:
    request_id: str | None
    actor_id: str | None
    actor_name: str | None
    ip_address: str | None
    user_agent: str | None

    def audit(self, log: AuditLog, action: str, **kwargs: Any) -> None:
        log.record(
            action,
            actor_type="client",
            actor_id=self.actor_id,
            actor_name=self.actor_name,
            request_id=self.request_id,
            ip_address=self.ip_address,
            user_agent=self.user_agent,
            **kwargs,
        )


def settings_of(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def audit_log(request: Request) -> AuditLog:
    log: AuditLog = request.app.state.audit_log
    return log


def template_store(request: Request) -> TemplateStore:
    store: TemplateStore = request.app.state.template_store
    return store


def _context(request: Request, actor_id: str | None) -> RequestContext:
    name = request.headers.get("X-Actor-Name")
    return RequestContext(
        request_id=getattr(request.state, "request_id", None),
        actor_id=actor_id,
        actor_name=_CONTROL.sub("", name)[:200] if name else None,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("User-Agent"),
    )


def require_client(
    request: Request, credentials: Annotated[HTTPBasicCredentials | None, Depends(basic)]
) -> RequestContext:
    """HTTP Basic check. 401 on bad / missing credentials, 429 when the IP is blocked."""
    clients: ClientStore = request.app.state.client_store
    limiter: FailureLimiter = request.app.state.limiter
    log: AuditLog = request.app.state.audit_log
    ip = request.client.host if request.client else "unknown"
    limiter.check(ip)

    if credentials is None:
        ok, reason, attempted = False, "MISSING_HEADER", None
    else:
        attempted = credentials.username[:64]
        ok, reason = clients.verify(credentials.username, credentials.password)
    if ok:
        limiter.success(ip)
        request.state.client_id = attempted
        return _context(request, attempted)

    ctx = _context(request, None)
    ctx.audit(
        log,
        "AUTH_FAILED",
        entity_type="client",
        entity_id=attempted,
        outcome="FAILURE",
        error=reason,
        details={"reason": reason, "path": request.url.path},
    )
    if limiter.fail(ip):
        ctx.audit(
            log,
            "AUTH_BLOCKED",
            entity_type="client",
            entity_id=attempted,
            outcome="FAILURE",
            details={"ip": ip, "block_seconds": limiter.block, "window_seconds": limiter.window},
        )
    raise AuthError("valid client credentials are required")


Client = Annotated[RequestContext, Depends(require_client)]

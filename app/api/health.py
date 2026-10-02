"""GET /health (baseline §7). Open: no credentials, no audit."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.schemas.common import HealthOut

router = APIRouter(tags=["System"])


@router.get(
    "/health", summary="Liveness check", response_model=HealthOut, responses={503: {"model": HealthOut}}
)
def health(request: Request) -> JSONResponse:
    """Liveness probe: the template store and the audit log must be writable (503 otherwise)."""
    templates_ok = request.app.state.template_store.writable()
    audit_ok = request.app.state.audit_log.writable()
    ok = templates_ok and audit_ok
    body = HealthOut(
        status="ok" if ok else "degraded",
        templates="ok" if templates_ok else "error",
        audit="ok" if audit_ok else "error",
        version=__version__,
        time=datetime.now(UTC),
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=200 if ok else 503)

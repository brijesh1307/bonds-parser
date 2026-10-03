"""GET /health (baseline §7). Open: no credentials, no audit."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app import __version__
from app.api.deps import Client
from app.metrics import CONTENT_TYPE_LATEST
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


metrics_router = APIRouter(tags=["System"])


@metrics_router.get(
    "/metrics",
    summary="Prometheus metrics",
    response_class=Response,
    responses={200: {"content": {"text/plain": {}}, "description": "Prometheus text format"}},
)
def metrics(request: Request, ctx: Client) -> Response:
    """Request counts and latency per route, parse results by status / market, template hits,
    authentication failures and active templates. Scrape with HTTP Basic client credentials."""
    return Response(content=request.app.state.metrics.render(), media_type=CONTENT_TYPE_LATEST)

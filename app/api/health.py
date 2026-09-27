"""GET /health (docs/04_api_spec.md §4.1). Open: no credentials, no audit."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from app import __version__
from app.schemas.common import HealthOut

router = APIRouter(tags=["System"])


@router.get("/health", summary="Liveness check", response_model=HealthOut)
def health() -> HealthOut:
    """Liveness probe for load balancers and monitoring. The database check is added in PH3."""
    return HealthOut(status="ok", db="not_configured", version=__version__, time=datetime.now(UTC))

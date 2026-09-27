"""FastAPI application factory (docs/03_lld.md §3.1).

PH0 provides the app shell with Swagger metadata. Routers, middleware and exception
handlers are added from PH1 onwards (docs/14_task_breakdown.md).
"""

from __future__ import annotations

from fastapi import FastAPI

from app import __version__
from app.config import Settings, get_settings

OPENAPI_TAGS = [
    {"name": "System", "description": "Liveness and database health."},
    {"name": "Parse", "description": "One-shot parse: upload a PDF, get the deal back. Nothing is stored."},
    {"name": "Slips", "description": "Stored slips: upload, review, preview mappings, approve, export."},
    {"name": "Templates", "description": "Approved slip layouts and their versions."},
    {"name": "Schema", "description": "Canonical fields and market profiles, for building mapping UIs."},
    {"name": "Exports", "description": "Combined exports of many deals."},
    {"name": "Audit", "description": "Append-only, hash-chained audit trail."},
]

DESCRIPTION = (
    "Parses bond deal slip PDFs into one canonical deal and returns it as JSON, XML or Excel. "
    "New slip layouts are onboarded once via preview/approve and then parse automatically. "
    "Authenticate with HTTP Basic client credentials (click **Authorize**)."
)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    docs = settings.docs_enabled
    app = FastAPI(
        title="Bonds Deal Slip Parser",
        version=__version__,
        description=DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.settings = settings
    return app


app = create_app()

"""FastAPI application factory (docs/03_lld.md §3.1, baseline §7).

Stateless for slip data (ADR-0009): the only state is the template folder, the client file and
the audit log, all opened here and shared through `app.state`.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import audit, errors, health, parse, templates
from app.auth import ClientStore, FailureLimiter
from app.config import Settings, get_settings
from app.services.audit_service import AuditLog
from app.services.template_store import TemplateStore

log = logging.getLogger("app.access")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

OPENAPI_TAGS = [
    {"name": "System", "description": "Liveness (open, no credentials)."},
    {
        "name": "Parse",
        "description": "Upload a deal slip PDF, get the deal back as JSON / XML / Excel. Nothing is stored.",
    },
    {
        "name": "Templates",
        "description": "Onboard a new slip layout once (preview, approve), manage versions.",
    },
    {"name": "Schema", "description": "Canonical fields and market profiles, for building mapping UIs."},
    {"name": "Audit", "description": "Append-only, hash-chained audit log (metadata only)."},
]

DESCRIPTION = """
Parses bond deal slip PDFs into one canonical deal and returns it as **JSON, XML or Excel**.

* **Nothing is stored**: each PDF is processed in memory and discarded.
* A new slip layout comes back `NEW_TEMPLATE` with every key/value it found. Map it once with
  `POST /api/v1/templates/preview` and save it with `POST /api/v1/templates`; from then on slips in
  that layout come back `PARSED`.
* Authenticate with **HTTP Basic** client credentials: click **Authorize**. Clients are created with
  `python -m app.cli add-client <name>`.
"""


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
    app.state.template_store = TemplateStore(settings.templates_dir)
    app.state.client_store = ClientStore(settings.clients_path)
    app.state.audit_log = AuditLog(settings.audit_path)
    app.state.limiter = FailureLimiter(
        settings.auth_max_failures, settings.auth_window_seconds, settings.auth_block_seconds
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("X-Request-ID", "")
        request.state.request_id = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        log.info(
            "%s %s %s %.0fms client=%s request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - start) * 1000,
            getattr(request.state, "client_id", None),
            request.state.request_id,
        )
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "PUT", "PATCH"],
        allow_headers=["Authorization", "Content-Type", "X-Actor-Name", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Content-Disposition"],
        allow_credentials=False,
    )
    errors.install(app)
    app.include_router(health.router)
    app.include_router(parse.router)
    app.include_router(templates.router)
    app.include_router(audit.schema_router)
    app.include_router(audit.audit_router)
    return app


app = create_app()

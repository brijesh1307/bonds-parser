"""RFC 7807 problem+json responses for every error (docs/03_lld.md §8, docs/04_api_spec.md §2.6)."""

from __future__ import annotations

import logging
import uuid
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.errors import PROBLEM_TYPE_PREFIX, AppError
from app.schemas.common import Problem

log = logging.getLogger("app.api")
PROBLEM_MEDIA_TYPE = "application/problem+json"

# Standard responses for route docs: every business endpoint can fail with these.
PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    code: {"model": Problem, "content": {PROBLEM_MEDIA_TYPE: {}}, "description": text}
    for code, text in {
        413: "File larger than BONDS_MAX_UPLOAD_MB",
        415: "Not a PDF",
        422: "Too many pages, encrypted/corrupt PDF, or invalid request",
        500: "Unexpected error",
    }.items()
}


def _request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    return rid if isinstance(rid, str) else uuid.uuid4().hex


def problem_response(
    request: Request,
    *,
    status: int,
    slug: str,
    title: str,
    detail: str | None = None,
    errors: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = Problem(
        type=PROBLEM_TYPE_PREFIX + slug,
        title=title,
        status=status,
        detail=detail,
        instance=request.url.path,
        request_id=_request_id(request),
        errors=errors or None,
    )
    return JSONResponse(
        body.model_dump(mode="json"), status_code=status, media_type=PROBLEM_MEDIA_TYPE, headers=headers
    )


async def _app_error(request: Request, exc: Exception) -> JSONResponse:
    err = cast(AppError, exc)  # registered for AppError only
    return problem_response(
        request,
        status=err.status,
        slug=err.slug,
        title=err.title,
        detail=err.detail,
        errors=err.errors,
        headers=err.headers,
    )


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    errors = [
        {"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")}
        for e in cast(RequestValidationError, exc).errors()
    ]
    return problem_response(
        request, status=422, slug="request-validation", title="Invalid request", errors=errors
    )


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    exc = cast(StarletteHTTPException, exc)
    slug = {404: "not-found", 405: "method-not-allowed"}.get(exc.status_code, "http-error")
    return problem_response(
        request,
        status=exc.status_code,
        slug=slug,
        title=str(exc.detail),
        headers=getattr(exc, "headers", None),
    )


async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
    rid = _request_id(request)
    request.state.request_id = rid
    log.exception("unhandled error request_id=%s path=%s", rid, request.url.path)
    return problem_response(request, status=500, slug="internal-error", title="Internal error")


def install(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unexpected)

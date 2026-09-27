"""POST /api/v1/parse - one-shot parse, nothing stored (docs/04_api_spec.md §4.2)."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, File, Query, Request, UploadFile

from app.api.errors import PROBLEM_RESPONSES
from app.schemas.slip import ParseResult
from app.services import slip_service

router = APIRouter(prefix="/api/v1", tags=["Parse"])


@router.post(
    "/parse",
    summary="Parse a deal slip PDF (nothing stored)",
    response_model=ParseResult,
    responses=PROBLEM_RESPONSES,
)
def parse(
    request: Request,
    file: Annotated[UploadFile, File(description="Deal slip PDF")],
    format: Annotated[
        Literal["json"], Query(description="Output format. `xml` and `xlsx` are added in v0.1.0 (PH5).")
    ] = "json",
) -> ParseResult:
    """Upload one PDF and get the canonical deal back with every extracted key/value pair,
    field confidence and positions. Nothing is stored.

    Until the parser engine is complete (PH2) and templates exist (PH3), results are
    `NEW_TEMPLATE` and some fields stay `null`.
    """
    return slip_service.parse_once(file, request.app.state.settings)

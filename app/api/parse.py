"""POST /api/v1/parse - one-shot parse, nothing stored (docs/04_api_spec.md §4.2)."""

from __future__ import annotations

import re
from pathlib import PurePath
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Query, Request, Response, UploadFile

from app.api.errors import PROBLEM_RESPONSES
from app.export.excel_export import to_xlsx
from app.export.xml_export import to_xml
from app.schemas.slip import ParseResult
from app.services import slip_service

router = APIRouter(prefix="/api/v1", tags=["Parse"])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "The parse result: JSON body, or an XML / Excel file download",
        "content": {"application/xml": {}, XLSX: {}},
    },
    **PROBLEM_RESPONSES,
}


def _download_name(result: ParseResult, ext: str) -> str:
    base = result.deal.deal_id or PurePath(result.source.file).stem or "deal"
    return re.sub(r"[^A-Za-z0-9._-]", "_", base) + ext


@router.post(
    "/parse",
    summary="Parse a deal slip PDF (nothing stored)",
    response_model=ParseResult,
    responses=_RESPONSES,
)
def parse(
    request: Request,
    file: Annotated[UploadFile, File(description="Deal slip PDF")],
    format: Annotated[
        Literal["json", "xml", "xlsx"],
        Query(description="`json` (response body), `xml` or `xlsx` (file download)"),
    ] = "json",
) -> Any:
    """Upload one PDF and get the canonical deal back with every extracted key/value pair,
    field confidence, page positions and validation results. Nothing is stored.

    Status is `NEW_TEMPLATE` until the layout is approved as a template (PH3); the deal is
    still fully returned.
    """
    result = slip_service.parse_once(file, request.app.state.settings)
    if format == "json":
        return result
    body, media, ext = (
        (to_xml(result), "application/xml", ".xml") if format == "xml" else (to_xlsx(result), XLSX, ".xlsx")
    )
    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{_download_name(result, ext)}"'},
    )

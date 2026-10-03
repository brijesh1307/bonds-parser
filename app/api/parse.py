"""POST /api/v1/parse - parse one PDF with the active templates; nothing is stored (baseline §7)."""

from __future__ import annotations

import re
from pathlib import PurePath
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile

from app.api.deps import Client, audit_log, settings_of, template_store
from app.api.errors import PROBLEM_RESPONSES
from app.config import Settings
from app.errors import AppError
from app.export.excel_export import to_xlsx
from app.export.xml_export import to_xml
from app.schemas.slip import ParseResult
from app.services import parse_service
from app.services.audit_service import AuditLog
from app.services.template_store import TemplateStore

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
    ctx: Client,
    file: Annotated[UploadFile, File(description="Deal slip PDF")],
    settings: Annotated[Settings, Depends(settings_of)],
    store: Annotated[TemplateStore, Depends(template_store)],
    log: Annotated[AuditLog, Depends(audit_log)],
    format: Annotated[
        Literal["json", "xml", "xlsx"],
        Query(description="`json` (response body), `xml` or `xlsx` (file download)"),
    ] = "json",
) -> Any:
    """Upload one PDF and get the canonical deal back with every extracted key/value pair, field
    confidence, page positions and validation results.

    A slip whose layout matches an approved template comes back `PARSED`; an unknown layout comes
    back `NEW_TEMPLATE` with the best-effort deal. The PDF is processed in memory and discarded.
    """
    try:
        engine_result, size = parse_service.parse_once(file, settings, store)
    except AppError as exc:
        ctx.audit(
            log,
            "SLIP_PARSED",
            entity_type="parse",
            outcome="FAILURE",
            error=exc.slug,
            details={"format": format},
        )
        raise
    result = parse_service.to_schema(engine_result)
    t = result.template
    ctx.audit(
        log,
        "SLIP_PARSED",
        entity_type="parse",
        entity_id=result.source.sha256,
        details={
            "sha256": result.source.sha256,
            "size": size,
            "pages": result.source.pages,
            "status": result.status,
            "market": result.market.market,
            "template_id": t.template_id,
            "template_version": t.version,
            "match_score": t.score,
            "format": format,
        },
    )
    request.app.state.metrics.observe_parse(
        result.status, result.market.market, t.template_id if t.matched else None
    )
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

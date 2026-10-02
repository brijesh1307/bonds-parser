"""Audit log search and verification; schema endpoints for mapping UIs (baseline §7)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import Client, audit_log
from app.engine.fields import BASE_REQUIRED, FIELD_SPECS, REQUIRED_BY_TYPE, is_valid_field_path
from app.engine.profiles import available_profiles
from app.export.xsd import build_xsd
from app.schemas.audit import AuditEventOut, AuditPage, AuditVerifyOut, FieldDef, MarketProfileOut
from app.services.audit_service import AuditLog

audit_router = APIRouter(prefix="/api/v1/audit", tags=["Audit"])
schema_router = APIRouter(prefix="/api/v1/schema", tags=["Schema"])

Log = Annotated[AuditLog, Depends(audit_log)]


@audit_router.get("", summary="Search the audit log (newest first)", response_model=AuditPage)
def search_audit(
    ctx: Client,
    log: Log,
    action: Annotated[str | None, Query(examples=["SLIP_PARSED"])] = None,
    actor_id: Annotated[str | None, Query()] = None,
    entity_id: Annotated[str | None, Query()] = None,
    date_from: Annotated[str | None, Query(alias="from", description="ISO date / time prefix, UTC")] = None,
    date_to: Annotated[str | None, Query(alias="to", description="ISO date / time prefix, UTC")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditPage:
    items, total = log.search(
        action=action,
        actor_id=actor_id,
        entity_id=entity_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return AuditPage(
        items=[AuditEventOut.model_validate(i) for i in items], total=total, limit=limit, offset=offset
    )


@audit_router.get("/verify", summary="Verify the audit hash chain", response_model=AuditVerifyOut)
def verify_audit(ctx: Client, log: Log) -> AuditVerifyOut:
    """Recomputes every record's hash and link.

    `ok=false` names the first record that was changed or removed."""
    return AuditVerifyOut.model_validate(log.verify())


@schema_router.get(
    "/fields", summary="Canonical fields (for mapping dropdowns)", response_model=list[FieldDef]
)
def get_fields(ctx: Client) -> list[FieldDef]:
    out = []
    for spec in FIELD_SPECS.values():
        if not is_valid_field_path(spec.path):
            continue
        required = (
            ["ALL"]
            if spec.path in BASE_REQUIRED
            else [t for t, req in REQUIRED_BY_TYPE.items() if spec.path in req]
        )
        out.append(
            FieldDef(
                name=spec.path,
                type=spec.type,
                kind=spec.kind,
                enum=list(spec.enum) if spec.enum else None,
                required_for=required,
                description=spec.description,
            )
        )
    return out


@schema_router.get("/markets", summary="Active market profiles", response_model=list[MarketProfileOut])
def get_markets(ctx: Client) -> list[MarketProfileOut]:
    return [
        MarketProfileOut(code=p.code, locale=p.locale, currency=p.currency, date_order=p.date_order)
        for p in available_profiles()
    ]


@schema_router.get(
    "/xsd",
    summary="XML Schema (XSD) of the XML output",
    response_class=Response,
    responses={200: {"content": {"application/xml": {}}, "description": "parse_result.xsd"}},
)
def get_xsd(ctx: Client) -> Response:
    """The XSD that every `/api/v1/parse?format=xml` response validates against (schema_version 1)."""
    return Response(
        content=build_xsd(),
        media_type="application/xml",
        headers={"Content-Disposition": 'attachment; filename="parse_result.xsd"'},
    )

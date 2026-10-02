"""Templates: preview / approve with the PDF in the request; list, get, version, edit, import.

See docs/00_design_baseline.md §7.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile, status
from pydantic import ValidationError

from app.api.deps import Client, audit_log, settings_of, template_store
from app.api.errors import PROBLEM_RESPONSES
from app.config import Settings
from app.errors import MappingValidationError
from app.schemas.slip import ParseResult
from app.schemas.template import (
    ApproveResult,
    MappingSpec,
    TemplateImport,
    TemplateOut,
    TemplatePatch,
    TemplateVersionIn,
)
from app.services import parse_service, template_service
from app.services.audit_service import AuditLog
from app.services.template_store import TemplateStore

router = APIRouter(prefix="/api/v1/templates", tags=["Templates"])

MAPPING_HELP = (
    "JSON text, for example: "
    '{"label_map": {"Contract Dt": "trade_date", "GST No": "_ignore"}, "constants": {"platform": "OTC"}, '
    '"template_name": "Broker XYZ G-Sec confirmation", "keywords": ["XYZ Securities"]}'
)
_ERRORS: dict[int | str, dict[str, Any]] = {
    **PROBLEM_RESPONSES,
    400: PROBLEM_RESPONSES[422] | {"description": "Invalid mapping (unknown field path, bad regex, ...)"},
    404: PROBLEM_RESPONSES[422] | {"description": "Template not found"},
    409: PROBLEM_RESPONSES[422] | {"description": "Validation issues not accepted, or template id clash"},
}

Store = Annotated[TemplateStore, Depends(template_store)]
Log = Annotated[AuditLog, Depends(audit_log)]
Cfg = Annotated[Settings, Depends(settings_of)]
TemplateId = Annotated[str, Path(description="Template id", examples=["broker_xyz_gsec_confirmation"])]


def _spec(mapping: str) -> MappingSpec:
    try:
        return MappingSpec.model_validate_json(mapping or "{}")
    except ValidationError as exc:
        errors = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        raise MappingValidationError("mapping is not valid JSON for MappingSpec", errors=errors) from exc


@router.post(
    "/preview",
    summary="Preview a mapping on a slip (nothing saved)",
    response_model=ParseResult,
    responses=_ERRORS,
)
def preview(
    ctx: Client,
    settings: Cfg,
    store: Store,
    log: Log,
    file: Annotated[UploadFile, File(description="Deal slip PDF")],
    mapping: Annotated[str, Form(description=MAPPING_HELP)] = "{}",
) -> ParseResult:
    """Parse the PDF with a draft template built from `mapping` and return the result it would give
    (`template.is_draft = true`). Use it in a loop while mapping a new layout. Nothing is stored."""
    spec = _spec(mapping)
    data, sha256 = parse_service.read_upload(file, settings)
    result = template_service.preview(data, file.filename or "upload.pdf", sha256, spec, store, settings)
    ctx.audit(
        log,
        "TEMPLATE_PREVIEWED",
        entity_type="template",
        entity_id=spec.template_id,
        details={
            "sha256": sha256,
            "would_be_status": result.status,
            "missing_required": result.missing_required,
            "label_map_entries": len(spec.label_map),
            "rules": len(spec.region_rules) + len(spec.regex_rules),
        },
    )
    return parse_service.to_schema(result)


@router.post(
    "",
    summary="Approve a mapping: create a template or a new version",
    status_code=status.HTTP_201_CREATED,
    response_model=ApproveResult,
    responses=_ERRORS,
)
def approve(
    ctx: Client,
    settings: Cfg,
    store: Store,
    log: Log,
    file: Annotated[UploadFile, File(description="Deal slip PDF of the layout")],
    mapping: Annotated[str, Form(description=MAPPING_HELP)] = "{}",
) -> ApproveResult:
    """Save the mapping as a template (`template_name`) or as a new version of `template_id`.
    Refused with 422 while required fields are missing, and with 409 when validation issues exist and
    `accept_validation_issues` is false. Only the template is saved; the PDF is discarded."""
    spec = _spec(mapping)
    data, sha256 = parse_service.read_upload(file, settings)
    record, result, created = template_service.approve(
        data, file.filename or "upload.pdf", sha256, spec, store, settings, ctx.actor_id or "unknown"
    )
    ctx.audit(
        log,
        "TEMPLATE_CREATED" if created else "TEMPLATE_VERSION_ADDED",
        entity_type="template",
        entity_id=record["id"],
        details={
            "version": record["current_version"],
            "name": record["name"],
            "note": spec.note,
            "sha256": sha256,
            "status_after": result.status,
            "accepted_validation_issues": spec.accept_validation_issues,
        },
    )
    return ApproveResult(template=template_service.to_out(record), result=parse_service.to_schema(result))


@router.get("", summary="List templates", response_model=list[TemplateOut])
def list_templates(
    ctx: Client,
    store: Store,
    is_active: Annotated[bool | None, Query()] = None,
    market: Annotated[str | None, Query(examples=["IN"])] = None,
) -> list[TemplateOut]:
    return [template_service.to_out(r) for r in store.find(is_active=is_active, market=market)]


@router.get(
    "/{template_id}",
    summary="Get a template with all its versions",
    response_model=TemplateOut,
    responses=_ERRORS,
)
def get_template(ctx: Client, store: Store, template_id: TemplateId) -> TemplateOut:
    return template_service.to_out(store.get(template_id), versions=True)


@router.put(
    "/{template_id}",
    summary="Add a new version from a definition",
    response_model=TemplateOut,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
)
def put_version(
    ctx: Client, store: Store, log: Log, template_id: TemplateId, body: TemplateVersionIn
) -> TemplateOut:
    """Adds version `current + 1` from an edited definition (no PDF needed).

    Versions are never overwritten."""
    record = template_service.put_version(store, template_id, body, ctx.actor_id or "unknown")
    ctx.audit(
        log,
        "TEMPLATE_VERSION_ADDED",
        entity_type="template",
        entity_id=template_id,
        details={"version": record["current_version"], "note": body.note, "via": "PUT"},
    )
    return template_service.to_out(record, versions=True)


@router.patch(
    "/{template_id}",
    summary="Enable / disable, rename or describe a template",
    response_model=TemplateOut,
    responses=_ERRORS,
)
def patch_template(
    ctx: Client, store: Store, log: Log, template_id: TemplateId, body: TemplatePatch
) -> TemplateOut:
    record, diff = template_service.patch(store, template_id, body)
    if "is_active" in diff:
        ctx.audit(
            log,
            "TEMPLATE_ENABLED" if diff["is_active"][1] else "TEMPLATE_DISABLED",
            entity_type="template",
            entity_id=template_id,
        )
    rest = {k: {"old": o, "new": n} for k, (o, n) in diff.items() if k != "is_active"}
    if rest:
        ctx.audit(
            log, "TEMPLATE_UPDATED", entity_type="template", entity_id=template_id, details={"changes": rest}
        )
    return template_service.to_out(record)


@router.post(
    "/import",
    summary="Import a template from another environment",
    response_model=TemplateOut,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
)
def import_template(ctx: Client, store: Store, log: Log, body: TemplateImport) -> TemplateOut:
    record = template_service.import_template(store, body, ctx.actor_id or "unknown")
    ctx.audit(
        log,
        "TEMPLATE_IMPORTED",
        entity_type="template",
        entity_id=record["id"],
        details={"name": record["name"]},
    )
    return template_service.to_out(record)

"""Receive an upload safely and parse it with the active templates (baseline §4, ADR-0009).

The PDF only ever lives in memory for the duration of the request; nothing is stored.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any

from fastapi import UploadFile

from app.config import Settings
from app.engine.fields import Market
from app.engine.models import BBox, EngineResult, KeyValue
from app.engine.pipeline import run
from app.errors import PayloadTooLargeError, UnsupportedMediaTypeError
from app.schemas import slip as s
from app.schemas.deal import Deal
from app.services.template_store import TemplateStore

CHUNK = 64 * 1024


def read_upload(upload: UploadFile, settings: Settings) -> tuple[bytes, str]:
    """Read in chunks, stop at the size limit (413), require the %PDF- signature (415)."""
    data = bytearray()
    digest = hashlib.sha256()
    while chunk := upload.file.read(CHUNK):
        data.extend(chunk)
        if len(data) > settings.max_upload_bytes:
            raise PayloadTooLargeError(f"file is larger than {settings.max_upload_mb} MB")
        digest.update(chunk)
    if not bytes(data[:1024]).lstrip().startswith(b"%PDF-"):
        raise UnsupportedMediaTypeError("the uploaded file does not start with %PDF-")
    return bytes(data), digest.hexdigest()


def parse_once(upload: UploadFile, settings: Settings, store: TemplateStore) -> tuple[EngineResult, int]:
    """Parse with the active templates. Returns the engine result and the file size (for audit)."""
    data, sha256 = read_upload(upload, settings)
    result = run(
        data,
        file_name=upload.filename or "upload.pdf",
        sha256=sha256,
        es=settings.engine,
        templates=store.active_definitions(),
    )
    return result, len(data)


def _bbox(b: BBox | None) -> list[float] | None:
    return list(b) if b else None


def _kv(kv: KeyValue, mapped_to: str | None) -> s.KeyValue:
    return s.KeyValue(
        key=kv.key,
        value=kv.value,
        source=kv.source,
        page=kv.page,
        key_bbox=_bbox(kv.key_bbox),
        value_bbox=_bbox(kv.value_bbox),
        mapped_to=mapped_to,
    )


def _field_value(v: Any) -> Any:
    return format(v, "f") if isinstance(v, Decimal) else v


def to_schema(res: EngineResult) -> s.ParseResult:
    m, t = res.market, res.template
    return s.ParseResult(
        status=res.status,
        source=s.SourceInfo(
            file=res.file_name,
            pages=res.pages,
            page_sizes=[list(p) for p in res.page_sizes],
            sha256=res.sha256,
        ),
        market=s.MarketInfo(
            market=Market(m.market),
            issuer_country=m.issuer_country,
            slip_locale=m.slip_locale,
            market_confidence=m.market_confidence,
            profile_available=m.profile is not None,
            signals=[
                s.MarketSignalOut(signal=x.signal, value=x.value, market=Market(x.market), weight=x.weight)
                for x in m.signals
            ],
        ),
        template=s.TemplateMatch(
            template_id=t.template.template_id if t.template else None,
            name=t.template.name if t.template else None,
            version=None if t.is_draft or not t.template else t.template.version,
            score=t.score,
            threshold=t.threshold,
            matched=t.matched,
            is_draft=t.is_draft,
        ),
        deal=Deal.model_validate(res.deal),
        fields={
            path: s.FieldInfo(
                value=_field_value(fv.value),
                raw=fv.raw,
                method=fv.method,
                confidence=fv.confidence,
                label=fv.label,
                page=fv.page,
                bbox=_bbox(fv.bbox),
            )
            for path, fv in res.fields.items()
        },
        key_values=[_kv(kv, res.mapped_to.get(i)) for i, kv in enumerate(res.key_values)],
        missing_required=res.missing_required,
        validation=[
            s.ValidationIssue(
                code=i.code,
                severity=i.severity,
                message=i.message,
                fields=list(i.fields),
                expected=i.expected,
                actual=i.actual,
                tolerance=i.tolerance,
            )
            for i in res.validation
        ],
        low_confidence=res.low_confidence,
        unmapped=[_kv(kv, None) for kv in res.unmapped],
    )

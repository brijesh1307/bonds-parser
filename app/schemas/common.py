"""Shared API models (docs/03_lld.md §3.7 common.py)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, PlainSerializer


def to_plain_str(d: Decimal) -> str:
    return format(d, "f")


# Decimals serialise as plain-notation strings, never floats or exponent form (D10).
PlainDecimal = Annotated[Decimal, PlainSerializer(to_plain_str, return_type=str, when_used="json")]


class Problem(BaseModel):
    """RFC 7807 problem details (application/problem+json)."""

    type: str = Field(examples=["urn:bonds-parser:problem:unsupported-media-type"])
    title: str = Field(examples=["Not a PDF file"])
    status: int = Field(examples=[415])
    detail: str | None = Field(default=None, examples=["The uploaded file does not start with %PDF-"])
    instance: str | None = Field(default=None, examples=["/api/v1/parse"])
    request_id: str = Field(examples=["3f2a9c0e8b7d4e1f"])
    errors: list[dict[str, Any]] | None = None


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    templates: Literal["ok", "error"] = Field(description="Template folder writable")
    audit: Literal["ok", "error"] = Field(description="Audit log writable")
    version: str
    time: datetime

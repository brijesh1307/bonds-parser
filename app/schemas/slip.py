"""Parse result envelope (docs/05_data_dictionary_and_outputs.md §5, docs/03_lld.md §3.7 slip.py)."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app.engine.fields import Market, Method, Severity, SlipStatus, Source
from app.schemas.deal import Deal

BBoxOut = list[float]


class KeyValue(BaseModel):
    key: str = Field(examples=["Face Value (INR)"])
    value: str = Field(examples=["5,00,00,000.00"])
    source: Source
    page: int
    key_bbox: BBoxOut | None = Field(None, description="[x0, top, x1, bottom] in PDF points, origin top-left")
    value_bbox: BBoxOut | None = None
    mapped_to: str | None = Field(None, examples=["face_value"])


class FieldInfo(BaseModel):
    value: str | int | float | date | None = Field(description="Normalised value; decimals as strings")
    raw: str | None
    method: Method
    confidence: float
    label: str | None
    page: int | None
    bbox: BBoxOut | None


class ValidationIssue(BaseModel):
    code: str
    severity: Severity
    message: str
    fields: list[str] = []
    expected: str | None = None
    actual: str | None = None
    tolerance: str | None = None


class TemplateMatch(BaseModel):
    template_id: str | None = None
    name: str | None = None
    version: int | None = None
    score: float
    threshold: float
    matched: bool
    is_draft: bool = False


class MarketSignalOut(BaseModel):
    signal: str
    value: str
    market: Market
    weight: int


class MarketInfo(BaseModel):
    market: Market
    issuer_country: str | None
    slip_locale: str | None
    market_confidence: float
    profile_available: bool
    signals: list[MarketSignalOut]


class SourceInfo(BaseModel):
    file: str
    pages: int
    page_sizes: list[list[float]] = Field(description="[width, height] in points, per page")
    sha256: str


class ParseResult(BaseModel):
    status: SlipStatus
    source: SourceInfo
    market: MarketInfo
    template: TemplateMatch
    deal: Deal
    fields: dict[str, FieldInfo]
    key_values: list[KeyValue]
    missing_required: list[str]
    validation: list[ValidationIssue]
    low_confidence: list[str]
    unmapped: list[KeyValue]

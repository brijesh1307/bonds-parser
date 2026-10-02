"""Template and mapping models (docs/00_design_baseline.md §6-§7)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.slip import ParseResult


class RegionRuleIn(BaseModel):
    field: str = Field(examples=["counterparty"])
    page: int = Field(1, ge=1)
    bbox: list[float] = Field(min_length=4, max_length=4, description="[x0, top, x1, bottom] in PDF points")


class RegexRuleIn(BaseModel):
    field: str = Field(examples=["yield"])
    pattern: str = Field(max_length=500, examples=[r"at a yield of ([\d.]+)%"])
    group: int = Field(1, ge=0)


class MappingSpec(BaseModel):
    """What the reviewer decided for this layout. Sent as JSON text in the `mapping` form field."""

    label_map: dict[str, str] = Field(
        default_factory=dict,
        description="Slip label as printed -> canonical field path, `repo.<field>`, or `_ignore`",
        examples=[{"Contract Dt": "trade_date", "GST No": "_ignore"}],
    )
    region_rules: list[RegionRuleIn] = Field(default_factory=list)
    regex_rules: list[RegexRuleIn] = Field(default_factory=list)
    constants: dict[str, str] = Field(default_factory=dict, examples=[{"platform": "OTC"}])
    template_id: str | None = Field(default=None, description="Existing template to add a new version to")
    template_name: str | None = Field(default=None, examples=["Broker XYZ G-Sec confirmation"])
    description: str | None = None
    keywords: list[str] = Field(default_factory=list, description="Text that must appear on the slip")
    date_order: Literal["DMY", "MDY"] | None = None
    accept_validation_issues: bool = False
    note: str | None = None


class TemplateDefinitionModel(BaseModel):
    template_id: str
    version: int
    market: str
    fingerprint: dict[str, list[str]]
    match_threshold: float
    label_map: dict[str, str]
    region_rules: list[RegionRuleIn] = Field(default_factory=list)
    regex_rules: list[RegexRuleIn] = Field(default_factory=list)
    constants: dict[str, str] = Field(default_factory=dict)
    ignore: list[str] = Field(default_factory=list)
    accepted_derived: list[str] = Field(default_factory=list)
    date_order: Literal["DMY", "MDY"] | None = None


class TemplateVersionOut(BaseModel):
    version: int
    definition: TemplateDefinitionModel
    note: str | None
    created_by: str
    created_at: datetime


class TemplateOut(BaseModel):
    id: str
    name: str
    description: str | None
    market: str
    is_active: bool
    current_version: int
    created_by: str
    created_at: datetime
    updated_at: datetime
    definition: TemplateDefinitionModel
    versions: list[TemplateVersionOut] | None = None


class TemplatePatch(BaseModel):
    name: str | None = None
    description: str | None = None
    is_active: bool | None = None


class TemplateVersionIn(BaseModel):
    definition: TemplateDefinitionModel
    note: str | None = None


class TemplateImport(BaseModel):
    name: str
    description: str | None = None
    definition: TemplateDefinitionModel


class ApproveResult(BaseModel):
    template: TemplateOut
    result: ParseResult = Field(description="The slip parsed again with the saved template")

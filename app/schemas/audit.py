"""Audit and schema-endpoint models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class AuditEventOut(BaseModel):
    seq: int
    event_id: str
    occurred_at: str
    actor_type: str
    actor_id: str | None
    actor_name: str | None
    action: str
    entity_type: str | None
    entity_id: str | None
    outcome: str
    error: str | None
    details: dict[str, Any]
    request_id: str | None
    ip_address: str | None
    user_agent: str | None
    prev_hash: str
    row_hash: str


class AuditPage(BaseModel):
    items: list[AuditEventOut]
    total: int
    limit: int
    offset: int


class AuditVerifyOut(BaseModel):
    ok: bool
    checked: int
    first_bad_seq: int | None
    reason: Literal["row_hash_mismatch", "prev_hash_mismatch"] | None
    last_hash: str | None


class FieldDef(BaseModel):
    name: str
    type: str
    kind: str | None
    enum: list[str] | None
    required_for: list[str]
    description: str


class MarketProfileOut(BaseModel):
    code: str
    locale: str
    currency: str
    date_order: str

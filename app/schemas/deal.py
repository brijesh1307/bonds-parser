"""Canonical deal models (docs/05_data_dictionary_and_outputs.md §1-§3)."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.engine.fields import BidType, BuySell, DayCount, DealType, InstrumentType, Market
from app.schemas.common import PlainDecimal


class Identifiers(BaseModel):
    isin: str | None = None
    cusip: str | None = None
    sedol: str | None = None
    common_code: str | None = None


class Repo(BaseModel):
    repo_rate: PlainDecimal | None = None
    repo_days: int | None = None
    day_count: DayCount | None = None
    haircut: PlainDecimal | None = None
    leg1_date: date | None = None
    leg1_price: PlainDecimal | None = None
    leg1_accrued_days: int | None = None
    leg1_accrued_interest: PlainDecimal | None = None
    leg1_amount: PlainDecimal | None = None
    leg2_date: date | None = None
    leg2_price: PlainDecimal | None = None
    leg2_accrued_days: int | None = None
    leg2_accrued_interest: PlainDecimal | None = None
    leg2_amount: PlainDecimal | None = None
    repo_interest: PlainDecimal | None = None


class Deal(BaseModel):
    """Every canonical field, always present; null when not applicable or not found."""

    model_config = ConfigDict(populate_by_name=True)

    deal_id: str | None = Field(None, examples=["GS/NDSOM/2026/004571"])
    deal_type: DealType | None = None
    instrument_type: InstrumentType | None = None
    buy_sell: BuySell | None = None
    platform: str | None = Field(None, examples=["NDS-OM"])
    trade_date: date | None = None
    settlement_date: date | None = None
    security_name: str | None = Field(None, examples=["7.10% GS 2034"])
    issuer: str | None = None
    credit_rating: str | None = None
    isin: str | None = Field(None, examples=["IN0020240A75"])
    identifiers: Identifiers = Field(default_factory=Identifiers)
    coupon_rate: PlainDecimal | None = Field(None, examples=["7.10"])
    coupon_frequency: int | None = None
    maturity_date: date | None = None
    last_coupon_date: date | None = None
    day_count: DayCount | None = None
    tenor_days: int | None = None
    face_value: PlainDecimal | None = Field(None, examples=["50000000"])
    face_value_per_unit: PlainDecimal | None = None
    quantity: int | None = None
    price: PlainDecimal | None = Field(None, examples=["101.2350"])
    yield_: PlainDecimal | None = Field(None, alias="yield", examples=["6.8865"])
    principal_amount: PlainDecimal | None = None
    accrued_days: int | None = None
    accrued_interest: PlainDecimal | None = None
    discount_amount: PlainDecimal | None = None
    consideration: PlainDecimal | None = Field(None, examples=["52264305.56"])
    stamp_duty: PlainDecimal | None = None
    settlement_reference: str | None = None
    currency: str | None = None
    settlement_currency: str | None = None
    fx_rate: PlainDecimal | None = None
    counterparty: str | None = None
    counterparty_pan: str | None = Field(
        None, description="Counterparty PAN. Personal data: handle outputs as confidential."
    )
    is_market_linked: bool | None = None
    broker: str | None = None
    settlement_mode: str | None = None
    portfolio: str | None = None
    dealer: str | None = None
    bid_type: BidType | None = None
    bid_amount: PlainDecimal | None = None
    issuer_country: str | None = None
    market: Market | None = None
    slip_locale: str | None = None
    market_confidence: float | None = None
    repo: Repo | None = None

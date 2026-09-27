from __future__ import annotations

import pytest

from app.engine import fields as f


@pytest.mark.parametrize(
    ("label", "full", "short"),
    [  # docs/03_lld.md §4.4 table
        ("Face Value (INR)", "face value inr", "face value"),
        ("Yield to Maturity (%)", "yield to maturity", "yield to maturity"),
        ("Cut-off Price (per INR 100)", "cut off price per inr 100", "cut off price"),
        ("Deal Date / Time", "deal date time", "deal date time"),
        ("SGL / CSGL A/c", "sgl csgl a c", "sgl csgl a c"),
        ("  Ticket   No ", "ticket no", "ticket no"),
    ],
)
def test_normalise_label(label: str, full: str, short: str) -> None:
    assert f.normalise_label(label) == (full, short)


@pytest.mark.parametrize(
    ("label", "path"),
    [
        ("Ticket No", "deal_id"),
        ("Deal Reference No.", "deal_id"),
        ("Value Date", "settlement_date"),
        ("Security Description", "security_name"),
        ("No. of Bonds", "quantity"),
        ("YTM %", "yield"),
        ("Total Consideration", "consideration"),
        ("Last Interest Paid On", "last_coupon_date"),
        ("Portfolio / Book", "portfolio"),
        ("Repo Rate", "repo.repo_rate"),
    ],
)
def test_synonyms(label: str, path: str) -> None:
    assert f.SYNONYMS[f.normalise_label(label)[0]] == path


def test_every_synonym_targets_a_mappable_field() -> None:
    assert all(f.is_valid_field_path(p) for p in f.SYNONYMS.values())


@pytest.mark.parametrize(
    ("key", "path"),
    [
        ("first leg date", "repo.leg1_date"),
        ("first leg settlement amount", "repo.leg1_amount"),
        ("second leg accr days", "repo.leg2_accrued_days"),
        ("leg 2 accrued int", "repo.leg2_accrued_interest"),
        ("second leg direction", None),
        ("first legend date", None),
    ],
)
def test_leg_lookup(key: str, path: str | None) -> None:
    assert f.leg_lookup(key) == path


def test_required_fields_by_deal_type() -> None:
    base = list(f.BASE_REQUIRED)
    assert f.required_fields(None) == base
    assert f.required_fields("OUTRIGHT") == [*base, "buy_sell", "price"]
    assert f.required_fields("PRIMARY_AUCTION") == [*base, "price"]
    assert f.required_fields("REVERSE_REPO")[-4:] == [
        "repo.repo_rate",
        "repo.leg1_amount",
        "repo.leg2_date",
        "repo.leg2_amount",
    ]


def test_registry_matches_baseline() -> None:
    assert len(f.TOP_LEVEL_FIELDS) == 41  # docs/05 §1 rows 1-43 minus identifiers and repo objects
    assert f.FIELD_SPECS["face_value"].kind == "nominal"
    assert f.FIELD_SPECS["price"].kind == "price"
    assert f.FIELD_SPECS["repo.leg2_amount"].kind == "cash"
    assert "30E/360" in f.FIELD_SPECS["day_count"].enum  # type: ignore[operator]
    assert not f.is_valid_field_path("market")  # market fields come from detection only
    assert not f.is_valid_field_path("nonsense")


def test_empty_deal_has_every_key() -> None:
    deal = f.empty_deal()
    assert set(deal) == {*f.TOP_LEVEL_FIELDS, "identifiers", "repo"}
    assert all(v is None for k, v in deal.items() if k not in ("identifiers",))
    assert deal["identifiers"] == {"isin": None, "cusip": None, "sedol": None, "common_code": None}

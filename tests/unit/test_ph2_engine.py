"""PH2 engine rules: merged-cell tables, cell splitting, compound labels, market vote,
derivation and validation (docs/03_lld.md §4.1-4.13)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.engine.derive import derive, reassign_per_unit_face_value
from app.engine.extract import extract, inline_pair, sub_pairs
from app.engine.fields import Method, Source
from app.engine.mapping import lookup_label, resolve
from app.engine.market import detect_market
from app.engine.models import Candidate, ExtractedDocument, FieldValue, KeyValue
from app.engine.profiles import get_profile
from app.engine.validate import validate
from tests.conftest import mock_pdf

IN = get_profile("IN")


# --------------------------------------------------------------------------- extraction


def test_merged_cell_letter_table_is_read_completely() -> None:
    doc = extract(mock_pdf("05_client_letter_mld_individual"), max_pages=20)
    got = {p.key: p.value for p in doc.pairs if p.source is Source.table}
    assert got["DEAL TYPE"] == "Our Buy from:- RAHUL DESHPANDE (MOCK)"
    assert got["SELLER PAN No."] == "ABCPD1234E"  # label and value in one cell
    assert got["MATURITY DATE"] == "Friday, December 24, 2021"  # second pair on the same row
    assert got["QUANTUM (Rs.)"] == "1000000" and got["NO. OF NCDs."] == "4"
    assert got["STAMP DUTY TO BE BORNE BY BUYER (Rs.)"] == "1"  # wrapped label joined
    assert got["Market type (SETTLEMENT DETAILS)"] == "ICDM(T+0)"  # sub-pair inside a cell
    assert got["Level (INITIAL OBSERVATION DATE & LEVEL)"] == "11131.80"


def test_wrapped_name_is_not_split() -> None:
    doc = extract(mock_pdf("06_client_letter_mld_company"), max_pages=20)
    got = {p.key: p.value for p in doc.pairs}
    assert got["DEAL TYPE"] == "Our Buy from:- SAHYADRI INVESTMENTS PRIVATE LTD (MOCK)"
    assert not any(k.startswith("Our Buy from") for k in got)


def test_grid_and_text_lines() -> None:
    doc = extract(mock_pdf("04_market_repo_reverse_repo"), max_pages=20)
    got = {p.key: (p.value, p.source) for p in doc.pairs}
    assert got["Second Leg Settlement Amount"] == ("10,17,97,994.86", Source.grid)
    assert got["Repo Interest (INR)"] == ("45,161.53", Source.text)
    assert got["Dealer"] == ("P. Nair", Source.text)  # "A: x | B: y | Dealer: z" line


@pytest.mark.parametrize(
    ("cell", "pair"),
    [
        ("SELLER PAN No. ABCPD1234E", ("SELLER PAN No.", "ABCPD1234E")),
        ("PAN:- AABCS1234K", ("PAN", "AABCS1234K")),
        ("Ref: TB/AUC/2026/182D/039", ("Ref", "TB/AUC/2026/182D/039")),
        ("Yield to Maturity (%)", None),
        ("Cut-off Price (per INR 100)", None),
        ("Trade Time", None),
    ],
)
def test_inline_pair(cell: str, pair: tuple[str, str] | None) -> None:
    assert inline_pair(cell) == pair


def test_sub_pairs() -> None:
    assert sub_pairs("CM BP ID : IN1 | Market type : ICDM(T+0) | CM Name : ICCL") == [
        ("CM BP ID", "IN1"),
        ("Market type", "ICDM(T+0)"),
        ("CM Name", "ICCL"),
    ]
    assert sub_pairs("Date – Monday, July 27, 2020\nLevel – 11131.80") == [
        ("Date", "Monday, July 27, 2020"),
        ("Level", "11131.80"),
    ]
    assert sub_pairs("Our Buy from:- SAHYADRI INVESTMENTS PRIVATE\nLTD") == []
    assert sub_pairs("single value") == []


# --------------------------------------------------------------------------- mapping


@pytest.mark.parametrize(
    ("label", "paths", "method"),
    [
        ("SECURITY NAME/ISIN NUMBER", ["security_name", "isin"], Method.synonym),
        ("DEAL DATE/VALUE DATE", ["trade_date", "settlement_date"], Method.synonym),
        ("Date of Issue / Settlement", ["settlement_date"], Method.synonym),  # full label wins over split
        ("UNDERLYING/REFERENCE INDEX", [], None),  # "Reference" alone is not a deal id
        ("Stl Dt", ["settlement_date"], Method.abbreviation),
        ("Settelment Date", ["settlement_date"], Method.fuzzy),
        ("SELLER SETTLEMENT AMOUNT (Rs.)", ["_seller_amount"], Method.synonym),
        ("Market type (SETTLEMENT DETAILS)", ["platform"], Method.synonym),
    ],
)
def test_lookup_label(label: str, paths: list[str], method: Method | None) -> None:
    hits = lookup_label(label)
    assert [h[0] for h in hits] == paths
    if method:
        assert all(h[1] is method for h in hits)
    if method is Method.fuzzy:
        assert hits[0][2] < 0.90  # fuzzy never reaches the PARSED threshold


def _kv(key: str, value: str, top: float = 100.0) -> KeyValue:
    return KeyValue(key, value, Source.table, 1, (10, top, 50, top + 10), (60, top, 90, top + 10))


def test_conflicting_values_warn_and_cap_confidence() -> None:
    a = Candidate("price", _kv("Price", "101.00", 100), "101.00", Method.synonym, 0.95)
    b = Candidate("price", _kv("Clean Price", "102.00", 200), "102.00", Method.synonym, 0.95)
    fields, issues = resolve({"price": [a, b]}, {id(a): Decimal("101.00"), id(b): Decimal("102.00")})
    assert fields["price"].value == Decimal("101.00")  # first on the page wins
    assert fields["price"].confidence == 0.85
    assert [i.code for i in issues] == ["CONFLICTING_VALUES"]


def test_per_unit_face_value_is_moved() -> None:
    total = Candidate("face_value", _kv("QUANTUM (Rs.)", "1000000"), "1000000", Method.synonym, 0.95)
    unit = Candidate(
        "face_value", _kv("FACE VALUE (Rs.)", "2,50,000.00"), "2,50,000.00", Method.synonym, 0.95
    )
    qty = Candidate("quantity", _kv("NO. OF NCDs.", "4"), "4", Method.synonym, 0.95)
    cands = {"face_value": [total, unit], "quantity": [qty]}
    norm = {id(total): Decimal("1000000"), id(unit): Decimal("250000"), id(qty): 4}
    reassign_per_unit_face_value(cands, norm)
    assert cands["face_value"] == [total]
    assert [norm[id(c)] for c in cands["face_value_per_unit"]] == [Decimal("250000")]


# --------------------------------------------------------------------------- market


def test_market_vote_india() -> None:
    doc = extract(mock_pdf("05_client_letter_mld_individual"), max_pages=20)
    m = detect_market(doc)
    assert (m.market, m.issuer_country, m.slip_locale, m.market_confidence) == ("IN", "IN", "en-IN", 1.0)
    assert m.profile is IN
    assert {s.signal for s in m.signals} >= {"isin_prefix", "currency", "settlement_system"}


def test_market_vote_foreign_slip_has_no_profile() -> None:
    doc = ExtractedDocument(
        [],
        "Security: UST 4.25 ISIN US0378331005 Amount USD 1,000,000.00 settles via DTC",
        1,
        [(595.0, 842.0)],
        [[]],
        True,
    )
    m = detect_market(doc)
    assert m.market == "US"
    assert m.profile is None  # MVP: only IN is active -> slip goes to review


def test_market_unknown_without_signals() -> None:
    doc = ExtractedDocument([], "nothing useful here at all", 1, [(595.0, 842.0)], [[]], True)
    assert detect_market(doc).market == "UNKNOWN"


# --------------------------------------------------------------------------- derivation


def _fv(value: object, raw: str | None = None) -> FieldValue:
    return FieldValue(value, raw, Method.synonym, 0.95, "label", 1, None)


def test_our_buy_from_sets_side_counterparty_and_amounts() -> None:
    fields = {
        "deal_type": _fv("OUTRIGHT", "Our Buy from:- RAHUL DESHPANDE (MOCK)"),
        "_seller_amount": _fv(Decimal("1152984.00")),
        "_buyer_amount": _fv(Decimal("1152985.00")),
        "_seller_pan": _fv("ABCPD1234E"),
        "security_name": _fv("OWPL MLD FD Plus 24.12.2021"),
        "instrument_type": _fv(
            "CORPORATE_BOND", "Principal Protected Market Linked, Non-Convertible Debentures"
        ),
    }
    doc = ExtractedDocument([], "", 1, [(595.0, 842.0)], [[]], True)
    derive(fields, doc, IN)
    assert fields["buy_sell"].value == "BUY"
    assert fields["counterparty"].value == "RAHUL DESHPANDE (MOCK)"
    assert fields["consideration"].value == Decimal("1152985.00")  # our side: buyer amount
    assert fields["principal_amount"].value == Decimal("1152984.00")
    assert fields["stamp_duty"].value == Decimal("1.00")
    assert fields["counterparty_pan"].value == "ABCPD1234E"
    assert fields["is_market_linked"].value is True
    assert "coupon_frequency" not in fields  # MLD: no default frequency is invented


def test_our_sell_to_uses_seller_amount() -> None:
    fields = {
        "deal_type": _fv("OUTRIGHT", "Our Sell to:- ABC LTD"),
        "_seller_amount": _fv(Decimal("100.00")),
        "_buyer_amount": _fv(Decimal("101.00")),
        "_buyer_pan": _fv("ABCDE1234F"),
    }
    derive(fields, ExtractedDocument([], "", 1, [], [[]], True), IN)
    assert fields["buy_sell"].value == "SELL"
    assert fields["counterparty"].value == "ABC LTD"
    assert fields["consideration"].value == Decimal("100.00")
    assert fields["counterparty_pan"].value == "ABCDE1234F"


def test_derivation_never_overwrites_a_mapped_value() -> None:
    fields = {"deal_type": _fv("OUTRIGHT", "BUY"), "buy_sell": _fv("SELL")}
    derive(fields, ExtractedDocument([], "", 1, [], [[]], True), IN)
    assert fields["buy_sell"].value == "SELL"


# --------------------------------------------------------------------------- validation


def _deal(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "deal_type": "OUTRIGHT",
        "buy_sell": "BUY",
        "isin": "INE999M07033",
        "trade_date": date(2021, 12, 7),
        "settlement_date": date(2021, 12, 7),
        "maturity_date": date(2021, 12, 24),
        "face_value": Decimal("1000000"),
        "face_value_per_unit": Decimal("250000"),
        "quantity": 4,
        "price": Decimal("115.2984"),
        "principal_amount": Decimal("1152984.00"),
        "stamp_duty": Decimal("1.00"),
        "consideration": Decimal("1152985.00"),
        "repo": None,
    }
    base.update(kw)
    return base


def test_consistent_letter_passes_all_checks() -> None:
    assert validate(_deal(), IN) == []


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"isin": "INE999M07034"}, "ISIN_CHECK_DIGIT"),
        ({"settlement_date": date(2021, 12, 6)}, "SETTLEMENT_BEFORE_TRADE"),
        ({"maturity_date": date(2021, 12, 1)}, "MATURITY_NOT_AFTER_SETTLEMENT"),
        ({"settlement_date": date(2021, 12, 11), "trade_date": date(2021, 12, 10)}, "WEEKEND_SETTLEMENT"),
        ({"principal_amount": Decimal("1152000.00")}, "PRINCIPAL_MISMATCH"),
        ({"consideration": Decimal("1152984.00")}, "CONSIDERATION_MISMATCH"),  # stamp duty forgotten
        ({"quantity": 5}, "QUANTITY_MISMATCH"),
    ],
)
def test_each_check_catches_its_error(change: dict[str, object], code: str) -> None:
    assert code in [i.code for i in validate(_deal(**change), IN)]


def test_real_layout_mocks_have_no_issues() -> None:
    from app.config import Settings
    from app.engine.pipeline import run

    for stem in ("05_client_letter_mld_individual", "06_client_letter_mld_company"):
        data = Path(mock_pdf(stem)).read_bytes()
        result = run(data, file_name=stem, sha256="x", es=Settings().engine)
        assert result.validation == [], stem
        assert result.missing_required == [], stem

"""Template onboarding through the API, stateless (baseline §7, ADR-0009)."""

from __future__ import annotations

import io
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

from tests.conftest import mock_pdf


def broker_slip(contract: str, trade: str, settle: str, nominal_cr: str, rate: str, net: str) -> bytes:
    """A layout the built-in synonyms only partly know (labels like a broker contract note)."""
    rows = [
        ["Contract No", contract],
        ["Txn", "OUTRIGHT PURCHASE"],
        ["Contract Dt", trade],
        ["Stl Dt", settle],
        ["Paper", "7.10% GS 2034"],
        ["Code", "IN0020240A75"],
        ["Nominal (Cr)", nominal_cr],
        ["Rate", rate],
        ["Net Payable", net],
        ["GST No", "27AAAAA0000A1Z5"],
    ]
    buf = io.BytesIO()
    t = Table(rows)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, (0, 0, 0))]))
    SimpleDocTemplate(buf, pagesize=A4).build([t])
    return buf.getvalue()


SLIP_A = broker_slip("XYZ/001", "24-Sep-2026", "25-Sep-2026", "5.00", "100.5000", "5,02,50,000.00")
SLIP_B = broker_slip("XYZ/002", "23-Sep-2026", "24-Sep-2026", "2.00", "101.0000", "2,02,00,000.00")
MAPPING = {
    "label_map": {
        "Contract Dt": "trade_date",
        "Paper": "security_name",
        "Code": "isin",
        "Rate": "price",
        "Net Payable": "consideration",
        "Txn": "deal_type",
        "GST No": "_ignore",
    },
    "constants": {"platform": "OTC"},
    "template_name": "XYZ Securities contract note",
    "note": "first onboarding",
}


def _files(data: bytes) -> dict[str, tuple[str, bytes, str]]:
    return {"file": ("slip.pdf", data, "application/pdf")}


def _post(client: TestClient, path: str, data: bytes, mapping: dict[str, object] | None = None) -> object:
    form = {"mapping": json.dumps(mapping)} if mapping is not None else {}
    return client.post(path, files=_files(data), data=form)


def test_full_onboarding_flow(client: TestClient, app: FastAPI) -> None:
    # 1. unknown layout: best-effort deal, required fields missing
    first = client.post("/api/v1/parse", files=_files(SLIP_A)).json()
    assert first["status"] == "NEW_TEMPLATE"
    assert "price" in first["missing_required"] or "consideration" in first["missing_required"]

    # 2. preview the mapping: nothing saved, status it would get
    prev = _post(client, "/api/v1/templates/preview", SLIP_A, MAPPING).json()  # type: ignore[attr-defined]
    assert prev["template"]["is_draft"] is True
    assert prev["missing_required"] == []
    assert prev["deal"]["price"] == "100.5000"
    assert prev["deal"]["face_value"] == "50000000"  # 5.00 Cr (unit in the label)
    assert prev["deal"]["platform"] == "OTC"  # template constant
    assert prev["fields"]["trade_date"]["method"] == "template"
    assert not any(kv["key"] == "GST No" for kv in prev["unmapped"])  # ignored, not unmapped
    assert client.get("/api/v1/templates").json() == []

    # 3. approve: template v1 saved, slip re-parsed with it
    r = _post(client, "/api/v1/templates", SLIP_A, MAPPING)
    assert r.status_code == 201, r.text  # type: ignore[attr-defined]
    body = r.json()  # type: ignore[attr-defined]
    tpl = body["template"]
    assert (tpl["id"], tpl["current_version"], tpl["market"]) == ("xyz_securities_contract_note", 1, "IN")
    assert body["result"]["status"] == "PARSED"
    assert "isin" in tpl["definition"]["accepted_derived"] or tpl["definition"]["label_map"]["code"] == "isin"

    # 4. a new slip in the same layout parses automatically
    second = client.post("/api/v1/parse", files=_files(SLIP_B)).json()
    assert second["status"] == "PARSED"
    assert second["template"]["template_id"] == "xyz_securities_contract_note"
    assert second["template"]["version"] == 1
    assert (second["deal"]["deal_id"], second["deal"]["face_value"]) == ("XYZ/002", "20000000")

    # 5. versions, disable, enable
    detail = client.get("/api/v1/templates/xyz_securities_contract_note").json()
    assert [v["version"] for v in detail["versions"]] == [1]
    put = client.put(
        "/api/v1/templates/xyz_securities_contract_note",
        json={"definition": detail["definition"], "note": "edited"},
    )
    assert put.status_code == 201 and put.json()["current_version"] == 2
    off = client.patch("/api/v1/templates/xyz_securities_contract_note", json={"is_active": False})
    assert off.json()["is_active"] is False
    assert client.post("/api/v1/parse", files=_files(SLIP_B)).json()["status"] == "NEW_TEMPLATE"
    client.patch("/api/v1/templates/xyz_securities_contract_note", json={"is_active": True})

    # 6. the template file holds labels and rules only
    saved = (app.state.settings.templates_dir / "xyz_securities_contract_note.json").read_text(
        encoding="utf-8"
    )
    for value in ("XYZ/001", "5,02,50,000.00", "100.5000", "24-Sep-2026"):
        assert value not in saved


def test_nothing_from_the_slip_is_stored(client: TestClient, app: FastAPI) -> None:
    pdf = mock_pdf("05_client_letter_mld_individual").read_bytes()
    assert client.post("/api/v1/parse", files=_files(pdf)).status_code == 200
    data_dir: Path = app.state.settings.data_dir
    files = sorted(p.relative_to(data_dir).as_posix() for p in data_dir.rglob("*") if p.is_file())
    assert files == ["audit/audit.jsonl", "clients.json"]
    audit = (data_dir / "audit" / "audit.jsonl").read_text(encoding="utf-8")
    for secret_value in ("RAHUL DESHPANDE", "ABCPD1234E", "slip.pdf", "1152985"):
        assert secret_value not in audit
    assert not app.state.settings.templates_dir.exists()  # parsing never writes templates


def test_jm_style_letter_onboarded_once_then_parsed(client: TestClient) -> None:
    """Demo story: approve the MLD letter layout from one slip; another slip of it comes back PARSED."""
    a = mock_pdf("05_client_letter_mld_individual").read_bytes()
    b = mock_pdf("06_client_letter_mld_company").read_bytes()
    assert client.post("/api/v1/parse", files=_files(b)).json()["status"] == "NEW_TEMPLATE"
    r = _post(client, "/api/v1/templates", a, {"template_name": "MLD client confirmation letter"})
    assert r.status_code == 201, r.text  # type: ignore[attr-defined]
    parsed = client.post("/api/v1/parse", files=_files(b)).json()
    assert parsed["status"] == "PARSED"
    assert parsed["template"]["template_id"] == "mld_client_confirmation_letter"
    assert parsed["deal"]["counterparty"] == "SAHYADRI INVESTMENTS PRIVATE LTD (MOCK)"
    assert parsed["deal"]["consideration"] == "11865352.00"


def test_invalid_mapping_is_400(client: TestClient) -> None:
    bad = {
        "label_map": {"Rate": "no_such_field"},
        "regex_rules": [{"field": "yield", "pattern": "("}],
        "template_name": "x",
    }
    r = _post(client, "/api/v1/templates/preview", SLIP_A, bad)
    assert r.status_code == 400  # type: ignore[attr-defined]
    body = r.json()  # type: ignore[attr-defined]
    assert body["type"] == "urn:bonds-parser:problem:invalid-mapping"
    assert len(body["errors"]) == 2
    assert _post(client, "/api/v1/templates/preview", SLIP_A, None).status_code == 200  # type: ignore[attr-defined]
    r2 = client.post("/api/v1/templates/preview", files=_files(SLIP_A), data={"mapping": "{not json"})
    assert r2.status_code == 400


def test_approve_refused_while_required_fields_missing(client: TestClient) -> None:
    r = _post(client, "/api/v1/templates", SLIP_A, {"template_name": "incomplete"})
    assert r.status_code == 422  # type: ignore[attr-defined]
    body = r.json()  # type: ignore[attr-defined]
    assert body["type"] == "urn:bonds-parser:problem:approval-blocked"
    assert {e["field"] for e in body["errors"]} >= {"price", "consideration"}
    assert client.get("/api/v1/templates").json() == []


def test_approve_with_validation_issues_needs_acceptance(client: TestClient) -> None:
    bad = broker_slip(
        "XYZ/003", "25-Sep-2026", "24-Sep-2026", "5.00", "100.5000", "5,02,50,000.00"
    )  # settles first
    r = _post(client, "/api/v1/templates", bad, MAPPING)
    assert r.status_code == 409  # type: ignore[attr-defined]
    assert r.json()["errors"][0]["code"] == "SETTLEMENT_BEFORE_TRADE"  # type: ignore[attr-defined]
    ok = _post(client, "/api/v1/templates", bad, {**MAPPING, "accept_validation_issues": True})
    assert ok.status_code == 201  # type: ignore[attr-defined]


def test_new_version_via_approve_and_import(client: TestClient) -> None:
    _post(client, "/api/v1/templates", SLIP_A, MAPPING)
    r = _post(
        client,
        "/api/v1/templates",
        SLIP_A,
        {**MAPPING, "template_id": "xyz_securities_contract_note", "template_name": None},
    )
    assert r.status_code == 201 and r.json()["template"]["current_version"] == 2  # type: ignore[attr-defined]
    exported = client.get("/api/v1/templates/xyz_securities_contract_note").json()["definition"]
    dup = client.post("/api/v1/templates/import", json={"name": "copy", "definition": exported})
    assert dup.status_code == 409
    exported["template_id"] = "xyz_copy"
    imp = client.post("/api/v1/templates/import", json={"name": "XYZ copy", "definition": exported})
    assert imp.status_code == 201 and imp.json()["current_version"] == 1
    assert client.get("/api/v1/templates/nope").status_code == 404


def test_schema_endpoints(client: TestClient) -> None:
    fields = {f["name"]: f for f in client.get("/api/v1/schema/fields").json()}
    assert fields["trade_date"]["required_for"] == ["ALL"]
    assert fields["price"]["required_for"] == ["OUTRIGHT", "PRIMARY_AUCTION"]
    assert "market" not in fields  # detected, never mapped
    assert fields["_buyer_amount"]["type"] == "decimal"
    assert client.get("/api/v1/schema/markets").json()[0]["code"] == "IN"


def test_xsd_endpoint(client: TestClient) -> None:
    r = client.get("/api/v1/schema/xsd")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert b'name="parse_result"' in r.content

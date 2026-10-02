"""Header auth, brute-force limiter, request context and the audit log (baseline D8, D9)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.cli import main as cli
from tests.api.conftest import MakeApp, authed, basic
from tests.conftest import mock_pdf


def _parse(client: TestClient, **headers: str) -> object:
    data = mock_pdf("01_gsec_outright_purchase").read_bytes()
    return client.post("/api/v1/parse", files={"file": ("x.pdf", data, "application/pdf")}, headers=headers)


def test_health_is_open(anon: TestClient) -> None:
    assert anon.get("/health").status_code == 200


def test_missing_or_wrong_credentials_are_401(app: FastAPI, anon: TestClient) -> None:
    r = anon.get("/api/v1/templates")
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"] == 'Basic realm="bonds-parser"'
    assert r.json()["type"] == "urn:bonds-parser:problem:unauthorized"
    app.state.client_store.add("real")
    assert anon.get("/api/v1/templates", headers=basic("real", "wrong")).status_code == 401
    assert anon.get("/api/v1/templates", headers=basic("ghost", "x")).status_code == 401


def test_disabled_and_rotated_clients(app: FastAPI, anon: TestClient) -> None:
    secret = app.state.client_store.add("svc")
    assert anon.get("/api/v1/templates", headers=basic("svc", secret)).status_code == 200
    new = app.state.client_store.rotate("svc")
    assert anon.get("/api/v1/templates", headers=basic("svc", secret)).status_code == 401
    assert anon.get("/api/v1/templates", headers=basic("svc", new)).status_code == 200
    app.state.client_store.disable("svc")
    assert anon.get("/api/v1/templates", headers=basic("svc", new)).status_code == 401


def test_every_business_route_requires_auth(app: FastAPI) -> None:
    open_paths = {"/health", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path not in open_paths:
            deps = {d.call.__name__ for d in route.dependant.dependencies}  # type: ignore[union-attr]
            assert "require_client" in deps, route.path


def test_brute_force_limiter_blocks_ip(make_app: MakeApp) -> None:
    app = make_app(auth_max_failures="3", auth_block_seconds="60")
    anon = TestClient(app)
    secret = app.state.client_store.add("svc")
    for _ in range(3):
        assert anon.get("/api/v1/templates", headers=basic("svc", "bad")).status_code == 401
    blocked = anon.get("/api/v1/templates", headers=basic("svc", secret))  # even the right secret
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0
    actions = [r["action"] for r in app.state.audit_log.search(limit=50)[0]]
    assert actions.count("AUTH_FAILED") == 3 and "AUTH_BLOCKED" in actions


def test_request_id_and_actor_name(client: TestClient, app: FastAPI) -> None:
    r = _parse(client, **{"X-Request-ID": "ui-7c1d", "X-Actor-Name": "Priya Shah"})
    assert r.headers["X-Request-ID"] == "ui-7c1d"  # type: ignore[attr-defined]
    generated = client.get("/api/v1/templates").headers["X-Request-ID"]
    assert len(generated) == 32
    rec = app.state.audit_log.search(action="SLIP_PARSED")[0][0]
    assert (rec["request_id"], rec["actor_name"], rec["actor_id"]) == ("ui-7c1d", "Priya Shah", "tester")
    assert rec["details"]["status"] == "NEW_TEMPLATE" and rec["details"]["format"] == "json"


def test_failed_parse_is_audited(client: TestClient, app: FastAPI) -> None:
    client.post("/api/v1/parse", files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    rec = app.state.audit_log.search(action="SLIP_PARSED")[0][0]
    assert (rec["outcome"], rec["error"]) == ("FAILURE", "unsupported-media-type")


def test_audit_api_search_and_verify(client: TestClient, app: FastAPI) -> None:
    _parse(client)
    _parse(client)
    page = client.get("/api/v1/audit", params={"action": "SLIP_PARSED", "limit": 1}).json()
    assert (page["total"], len(page["items"])) == (2, 1)
    assert page["items"][0]["seq"] == 2  # newest first
    assert client.get("/api/v1/audit/verify").json() == {
        "ok": True,
        "checked": 2,
        "first_bad_seq": None,
        "reason": None,
        "last_hash": page["items"][0]["row_hash"],
    }


def test_tampering_is_detected(client: TestClient, app: FastAPI) -> None:
    _parse(client)
    _parse(client)
    path: Path = app.state.audit_log.path
    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["details"]["status"] = "PARSED"  # someone edits history
    lines[0] = json.dumps(first)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = client.get("/api/v1/audit/verify").json()
    assert result["ok"] is False and result["first_bad_seq"] == 1 and result["reason"] == "row_hash_mismatch"


def test_pan_is_masked_in_audit(app: FastAPI) -> None:
    rec = app.state.audit_log.record(
        "TEST", details={"note": "client ABCPD1234E called"}, actor_name="ABCPD1234E"
    )
    assert "ABCPD1234E" not in json.dumps(rec)
    assert rec["details"]["note"] == "client ABCPD****E called"


def test_cli_client_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], make_app: MakeApp
) -> None:
    monkeypatch.setenv("BONDS_DATA_DIR", str(tmp_path / "data"))
    assert cli(["add-client", "frontend", "--description", "UI"]) == 0
    secret = capsys.readouterr().out.split("secret: ")[1].split()[0]
    assert cli(["add-client", "frontend"]) == 1  # exists
    assert cli(["add-client", "Bad Id!"]) == 2
    assert cli(["list-clients"]) == 0
    listed = capsys.readouterr().out
    assert "frontend" in listed and secret not in listed
    app = make_app()
    assert authed(app, "other").get("/api/v1/templates").status_code == 200
    anon = TestClient(app)
    assert anon.get("/api/v1/templates", headers=basic("frontend", secret)).status_code == 200
    assert cli(["rotate-secret", "frontend"]) == 0
    assert anon.get("/api/v1/templates", headers=basic("frontend", secret)).status_code == 401
    assert cli(["disable-client", "frontend"]) == 0
    assert cli(["disable-client", "nobody"]) == 1
    assert cli(["verify-audit"]) == 0
    assert "OK:" in capsys.readouterr().out
    actions = [r["action"] for r in app.state.audit_log.search(limit=50)[0] if r["actor_type"] == "cli"]
    assert sorted(actions) == ["CLIENT_ADDED", "CLIENT_DISABLED", "CLIENT_SECRET_ROTATED"]

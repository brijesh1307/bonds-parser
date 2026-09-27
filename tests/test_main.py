from __future__ import annotations

from fastapi.testclient import TestClient

from app import __version__
from app.config import Settings
from app.main import OPENAPI_TAGS, create_app


def test_app_imports_with_swagger_metadata() -> None:
    import app.main

    spec = TestClient(app.main.app).get("/openapi.json").json()
    assert spec["info"]["title"] == "Bonds Deal Slip Parser"
    assert spec["info"]["version"] == __version__
    assert [t["name"] for t in spec["tags"]] == [t["name"] for t in OPENAPI_TAGS]


def test_docs_can_be_disabled() -> None:
    client = TestClient(create_app(Settings.from_env({"BONDS_DOCS_ENABLED": "false"})))
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404

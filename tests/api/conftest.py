"""API test fixtures: an app with temporary template / client / audit files and an authenticated client."""

from __future__ import annotations

import base64
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

MakeApp = Callable[..., FastAPI]


@pytest.fixture
def make_app(tmp_path: Path) -> MakeApp:
    """create_app with files under tmp_path; extra BONDS_* overrides as keyword arguments."""

    def _make(**env: str) -> FastAPI:
        base = {"BONDS_DATA_DIR": str(tmp_path / "data"), "BONDS_TEMPLATES_DIR": str(tmp_path / "templates")}
        base.update({f"BONDS_{k.upper()}": v for k, v in env.items()})
        return create_app(Settings.from_env(base))

    return _make


@pytest.fixture
def app(make_app: MakeApp) -> FastAPI:
    return make_app()


def basic(client_id: str, secret: str) -> dict[str, str]:
    token = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def authed(app: FastAPI, client_id: str = "tester", **kw: object) -> TestClient:
    secret = app.state.client_store.add(client_id)
    return TestClient(app, headers=basic(client_id, secret), **kw)  # type: ignore[arg-type]


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return authed(app)


@pytest.fixture
def anon(app: FastAPI) -> TestClient:
    return TestClient(app)

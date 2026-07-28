from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# A throwaway database per test session, before anything imports settings.
_TMP = Path(tempfile.mkdtemp(prefix="elevenmetric-tests-"))
os.environ["ELEVENMETRIC_DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["ELEVENMETRIC_MEDIA_ROOT"] = str(_TMP / "media")
os.environ["ELEVENMETRIC_SECRET_KEY"] = "test-secret"

from fastapi.testclient import TestClient  # noqa: E402

from app.db.session import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database():
    init_db()
    from app.db.seed import seed

    seed(reset=True)
    yield


@pytest.fixture(scope="session")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="session")
def db():
    session = SessionLocal()
    yield session
    session.close()


def _login(client: TestClient, email: str, password: str = "elevenmetric") -> dict:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def auth(client) -> dict:
    return _login(client, "owner@demo.fc")


@pytest.fixture(scope="session")
def rival_auth(client) -> dict:
    return _login(client, "owner@rival.united")


@pytest.fixture(scope="session")
def team_id(client, auth) -> str:
    teams = client.get("/api/v1/teams", headers=auth).json()
    return next(t["id"] for t in teams if t["kind"] == "first_team")


@pytest.fixture(scope="session")
def match_id(client, auth) -> str:
    return client.get("/api/v1/matches", headers=auth).json()[0]["id"]

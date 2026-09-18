import pytest
from fastapi.testclient import TestClient

from app.auth import ALLOWED_GUILD_NAME
from app.main import app

client = TestClient(app)


def _login(guild_name: str) -> str:
    response = client.post("/api/login", json={"guild_name": guild_name})
    assert response.status_code == 200
    return response.json()["token"]


def test_login_returns_token_and_guild_name():
    response = client.post("/api/login", json={"guild_name": ALLOWED_GUILD_NAME})

    assert response.status_code == 200
    payload = response.json()
    assert payload["guild_name"] == ALLOWED_GUILD_NAME
    assert payload["token"]


def test_login_rejects_wrong_guild_name():
    response = client.post("/api/login", json={"guild_name": "别的公会"})

    assert response.status_code == 401
    assert response.json()["detail"] == "公会名不正确"


def test_me_returns_guild_name_for_valid_token():
    token = _login(ALLOWED_GUILD_NAME)

    response = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {"guild_name": ALLOWED_GUILD_NAME}


def test_me_requires_authorization_header():
    response = client.get("/api/me")

    assert response.status_code == 401
    assert response.json()["detail"] == "请先输入公会名登录"


@pytest.mark.parametrize(
    "header",
    [
        "Bearer not-a-real-token",
        "not-a-bearer-scheme",
        "Bearer ",
    ],
)
def test_me_rejects_invalid_token(header: str):
    response = client.get("/api/me", headers={"Authorization": header})

    assert response.status_code == 401


def test_extract_requires_token():
    response = client.post(
        "/api/extract",
        json={"url": "https://www.warcraftlogs.com/reports/AbCd1234EfGh5678#fight=1"},
    )

    assert response.status_code == 401

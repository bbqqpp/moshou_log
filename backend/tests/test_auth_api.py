import pytest
from fastapi.testclient import TestClient

from app import auth, main
from app.main import app

# 登录凭据来自环境变量 ALLOWED_GUILD_NAME，测试里固定成一个已知值，
# 这样不会依赖开发者本地 .env 的内容。
GUILD = "测试公会"

client = TestClient(app)


@pytest.fixture(autouse=True)
def _fixed_guild(monkeypatch):
    # auth 里读这个模块级变量做校验，main 里读它拼响应
    monkeypatch.setattr(auth, "ALLOWED_GUILD_NAME", GUILD)
    monkeypatch.setattr(main, "ALLOWED_GUILD_NAME", GUILD)


def _login(guild_name: str) -> str:
    response = client.post("/api/login", json={"guild_name": guild_name})
    assert response.status_code == 200
    return response.json()["token"]


def test_login_returns_token_and_guild_name():
    response = client.post("/api/login", json={"guild_name": GUILD})

    assert response.status_code == 200
    payload = response.json()
    assert payload["guild_name"] == GUILD
    assert payload["token"]


def test_login_rejects_wrong_guild_name():
    response = client.post("/api/login", json={"guild_name": "别的公会"})

    assert response.status_code == 401
    assert response.json()["detail"] == "公会名不正确"


def test_unconfigured_guild_name_rejects_everything(monkeypatch):
    """没配 ALLOWED_GUILD_NAME 时必须一律拒绝。

    否则 `"" == ""` 会让**任意输入**通过校验 —— 尤其是一个纯空白的公会名，
    它会 strip 成空串正好等于未配置的默认值，等于把门完全打开。
    """
    monkeypatch.setattr(auth, "ALLOWED_GUILD_NAME", "")

    for guess in ("   ", "任意公会", GUILD):
        response = client.post("/api/login", json={"guild_name": guess})
        assert response.status_code == 401, f"空配置下「{guess}」不该能登录"


def test_me_returns_guild_name_for_valid_token():
    token = _login(GUILD)

    response = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {"guild_name": GUILD}


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

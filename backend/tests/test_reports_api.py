import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.auth import issue_token
from app.cache import AnalysisCache
from app.config import settings
from app.report_store import PlayerReportStore
from app.wcl_data_store import WCLDataStore

REPORT = "AbCd1234EfGh5678"
FIGHT = 7


@pytest.fixture
def client(tmp_path, monkeypatch):
    """全部落到 tmp_path，绝不碰真实 storage。"""
    monkeypatch.setattr(
        main, "analysis_cache", AnalysisCache(tmp_path / "cache", settings.deepseek_model)
    )
    monkeypatch.setattr(main, "player_report_store", PlayerReportStore(tmp_path / "players"))
    monkeypatch.setattr(main, "wcl_data_store", WCLDataStore(tmp_path / "wcl"))
    monkeypatch.setattr(main.settings, "deepseek_api_key", "test-key")
    return TestClient(main.app)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token()}"}


def _seed_fight(client, monkeypatch) -> None:
    main.analysis_cache.set(
        REPORT,
        FIGHT,
        "I now have enough evidence.\n\n# 乌拉特克复盘\n正文",
        fight={"name": "乌拉特克", "boss": 3492, "kill": False, "size": 21},
    )
    main.wcl_data_store.save(
        REPORT,
        FIGHT,
        {"id": FIGHT, "name": "乌拉特克", "kill": False, "start_time": 0, "end_time": 600000},
        {"damage-done": {"entries": [{"id": 12, "name": "甲", "type": "Mage", "total": 100}]}},
    )


def test_report_list_requires_login(client):
    """顺带证明这条路由没被文件末尾的 StaticFiles 挂载吞掉（那样会是 404/405）。"""
    response = client.get("/api/reports")

    assert response.status_code == 401


def test_fight_report_is_public_so_share_links_work_without_login(client, monkeypatch):
    _seed_fight(client, monkeypatch)

    response = client.get(f"/api/reports/{REPORT}/{FIGHT}")

    assert response.status_code == 200
    assert "乌拉特克复盘" in response.json()["analysis"]


def test_fight_report_strips_the_model_preamble(client, monkeypatch):
    _seed_fight(client, monkeypatch)

    analysis = client.get(f"/api/reports/{REPORT}/{FIGHT}").json()["analysis"]

    assert analysis.startswith("# 乌拉特克复盘")
    assert "I now have enough evidence" not in analysis


def test_missing_fight_report_is_404_not_401(client):
    """公开接口对不存在的报告应当 404；返回 401 说明鉴权没摘干净。"""
    assert client.get(f"/api/reports/{REPORT}/999").status_code == 404


def test_player_report_is_public(client, monkeypatch):
    _seed_fight(client, monkeypatch)
    saved = main.player_report_store.save(
        REPORT, FIGHT, "single", ["甲"], analysis="# 甲的复盘"
    )

    response = client.get(f"/api/reports/{REPORT}/{FIGHT}/players/single/{saved['slug']}")

    assert response.status_code == 200
    assert response.json()["analysis"] == "# 甲的复盘"


def test_missing_player_report_is_404_not_401(client):
    assert client.get(f"/api/reports/{REPORT}/{FIGHT}/players/single/deadbeef").status_code == 404


def test_report_list_nests_player_reports_under_their_fight(client, monkeypatch):
    _seed_fight(client, monkeypatch)
    main.player_report_store.save(REPORT, FIGHT, "single", ["甲"], analysis="单人")
    main.player_report_store.save(REPORT, FIGHT, "comparison", ["甲", "乙"], analysis="对比")
    main.player_report_store.save(REPORT, 99, "single", ["丙"], analysis="别的战斗")

    rows = client.get("/api/reports", headers=_auth()).json()["reports"]

    assert len(rows) == 1
    assert rows[0]["fight"]["name"] == "乌拉特克"
    kinds = sorted(item["kind"] for item in rows[0]["player_reports"])
    assert kinds == ["comparison", "single"]


def test_stale_report_stays_listed_and_viewable(client, monkeypatch):
    """改了系统提示词之后，旧报告必须仍然能看，只是打个 stale 标记。"""
    _seed_fight(client, monkeypatch)
    path = main.analysis_cache._path(REPORT, FIGHT)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["signature"] = "outdated"
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

    assert main.analysis_cache.get(REPORT, FIGHT) is None, "严格读法应当失效"

    rows = client.get("/api/reports", headers=_auth()).json()["reports"]
    assert rows[0]["stale"] is True

    body = client.get(f"/api/reports/{REPORT}/{FIGHT}").json()
    assert body["stale"] is True
    assert "乌拉特克复盘" in body["analysis"]


def test_fight_report_without_wcl_data_degrades_to_cache_metadata(client, monkeypatch):
    """实测有 1 场只有分析缓存、原始数据已被删 —— 不能因此整个列表炸掉。"""
    main.analysis_cache.set(
        REPORT, FIGHT, "# 老报告", fight={"name": "乌拉特克", "kill": False}
    )

    body = client.get(f"/api/reports/{REPORT}/{FIGHT}").json()

    assert body["fight"]["name"] == "乌拉特克"
    assert body["summary"] is None
    assert body["roster"] == []


def test_generate_requires_login(client):
    response = client.post(
        "/api/player-reports/analyze",
        json={"report_code": REPORT, "fight_id": FIGHT, "players": ["甲"]},
    )

    assert response.status_code == 401


def test_generate_rejects_unknown_player_with_the_roster(client, monkeypatch):
    _seed_fight(client, monkeypatch)

    response = client.post(
        "/api/player-reports/analyze",
        json={"report_code": REPORT, "fight_id": FIGHT, "players": ["查无此人"]},
        headers=_auth(),
    )

    assert response.status_code == 400
    assert "甲" in response.json()["detail"], "报错要列出在场的人，方便照着改"


def test_generate_rejects_more_than_two_players(client, monkeypatch):
    _seed_fight(client, monkeypatch)

    response = client.post(
        "/api/player-reports/analyze",
        json={"report_code": REPORT, "fight_id": FIGHT, "players": ["甲", "乙", "丙"]},
        headers=_auth(),
    )

    assert response.status_code == 422


def test_generate_picks_kind_from_player_count(client, monkeypatch):
    _seed_fight(client, monkeypatch)

    # 生成现在是一个请求跑到底，所以这里必须把 DeepSeek 那步打桩
    async def fake_report(payload, kind):
        return f"# 报告 {kind}"

    monkeypatch.setattr(main, "run_player_report", fake_report)

    single = client.post(
        "/api/player-reports/analyze",
        json={"report_code": REPORT, "fight_id": FIGHT, "players": ["甲"]},
        headers=_auth(),
    ).json()
    assert single["kind"] == "single"

    # 同一个人选两次要去重，否则会变成「自己对比自己」
    deduped = client.post(
        "/api/player-reports/analyze",
        json={"report_code": REPORT, "fight_id": FIGHT, "players": ["甲", "甲"]},
        headers=_auth(),
    ).json()
    assert deduped["kind"] == "single"
    assert deduped["players"] == ["甲"]


def test_generate_needs_local_wcl_data(client, monkeypatch):
    main.analysis_cache.set(REPORT, FIGHT, "# 报告")

    response = client.post(
        "/api/player-reports/analyze",
        json={"report_code": REPORT, "fight_id": FIGHT, "players": ["甲"]},
        headers=_auth(),
    )

    assert response.status_code == 404


def test_generate_returns_the_report_and_persists_it(client, monkeypatch):
    _seed_fight(client, monkeypatch)

    async def fake_report(payload, kind):
        assert kind == "single"
        assert payload["player"] == "甲"
        return "I now have enough evidence.\n\n# 甲的复盘\n正文"

    monkeypatch.setattr(main, "run_player_report", fake_report)

    response = client.post(
        "/api/player-reports/analyze",
        json={"report_code": REPORT, "fight_id": FIGHT, "players": ["甲"]},
        headers=_auth(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "single"
    assert body["analysis"].startswith("# 甲的复盘"), "模型前言要在返回前剥掉"
    assert "I now have enough evidence" not in body["analysis"]

    # 已经落盘，侧边栏立刻能看到，公开读接口也能拿到
    rows = client.get("/api/reports", headers=_auth()).json()["reports"]
    assert len(rows[0]["player_reports"]) == 1

    saved = client.get(f"/api/reports/{REPORT}/{FIGHT}/players/single/{body['slug']}")
    assert saved.status_code == 200
    assert saved.json()["analysis"].startswith("# 甲的复盘")


def test_generate_reports_deepseek_failure_as_502(client, monkeypatch):
    _seed_fight(client, monkeypatch)

    async def failing_report(payload, kind):
        raise RuntimeError("DeepSeek 返回 500")

    monkeypatch.setattr(main, "run_player_report", failing_report)

    response = client.post(
        "/api/player-reports/analyze",
        json={"report_code": REPORT, "fight_id": FIGHT, "players": ["甲"]},
        headers=_auth(),
    )

    assert response.status_code == 502
    assert "DeepSeek 返回 500" in response.json()["detail"]

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app import main
from app.auth import issue_token
from app.cache import AnalysisCache


class _NullCache:
    """Keep the tests from touching storage/analysis_cache."""

    def get(self, report_code: str, fight_id: int) -> str | None:
        return None

    def set(self, report_code, fight_id, analysis, fight=None) -> None:
        pass


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "analysis_cache", _NullCache())
    monkeypatch.setattr(main.settings, "deepseek_api_key", "test-key")
    # 必须用 with：不用的话 starlette 会给**每个请求**新建一个 portal（事件循环），
    # 请求一结束循环就拆掉，`asyncio.create_task` 起的后台任务当场被取消，
    # 测试会看到任务永远停在 running。生产环境 uvicorn 是单一长驻循环，用 with 才一致。
    with TestClient(main.app) as test_client:
        yield test_client


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token()}"}


def _create_job() -> object:
    return main.jobs.create(
        report_code="AbCd1234EfGh5678",
        fight_id=1,
        url="https://www.warcraftlogs.com/reports/AbCd1234EfGh5678#fight=1",
        fight={"id": 1, "start_time": 0, "end_time": 180000},
        summary={},
        tables={},
    )


def _start(client: TestClient, task_id: str, query: str = ""):
    return client.post(f"/api/analysis/{task_id}{query}", headers=_auth())


def _wait_done(client: TestClient, task_id: str, attempts: int = 100) -> dict:
    """轮询到结束。

    后台任务是 `asyncio.create_task` 起的，要给它调度机会 —— TestClient 的事件循环
    跑在另一个线程里，所以这里 sleep 一下就能让它推进。
    """
    for _ in range(attempts):
        state = client.get(f"/api/analysis/{task_id}", headers=_auth()).json()
        if state["status"] != "running":
            return state
        time.sleep(0.01)
    raise AssertionError("分析没有在预期时间内结束")


def test_start_returns_immediately_then_polling_yields_the_report(client, monkeypatch):
    """整场复盘必须立刻返回 —— 挂在一个请求上等会被 Cloudflare 100 秒掐断。"""

    async def agent(data_store, report_code, fight_id, fight, summary):
        return "# 复盘报告\n最终结论"

    monkeypatch.setattr(main, "run_deepseek_tool_loop", agent)

    job = _create_job()
    started = _start(client, job.task_id)

    assert started.status_code == 200
    assert started.json()["status"] == "running"

    assert _wait_done(client, job.task_id) == {
        "status": "done",
        "analysis": "# 复盘报告\n最终结论",
    }


def test_agent_failure_surfaces_through_the_poll(client, monkeypatch):
    async def failing_agent(data_store, report_code, fight_id, fight, summary):
        raise RuntimeError("DeepSeek 返回 500")

    monkeypatch.setattr(main, "run_deepseek_tool_loop", failing_agent)

    job = _create_job()
    _start(client, job.task_id)
    state = _wait_done(client, job.task_id)

    assert state["status"] == "error"
    assert "DeepSeek 返回 500" in state["message"]


def test_completed_job_replays_without_calling_agent(client, monkeypatch):
    async def exploding_agent(*args, **kwargs):
        raise AssertionError("已完成的任务不应再次调用 DeepSeek")

    monkeypatch.setattr(main, "run_deepseek_tool_loop", exploding_agent)

    job = _create_job()
    job.status = "completed"
    job.deepseek_full_text = "缓存好的报告"

    response = _start(client, job.task_id)

    assert response.status_code == 200
    assert response.json() == {
        "status": "done",
        "analysis": "缓存好的报告",
        "cached": True,
    }


def test_cache_hit_skips_deepseek(client, monkeypatch, tmp_path):
    cache = AnalysisCache(tmp_path, model=main.settings.deepseek_model)
    cache.set("AbCd1234EfGh5678", 1, "# 早先分析过")
    monkeypatch.setattr(main, "analysis_cache", cache)

    async def exploding_agent(*args, **kwargs):
        raise AssertionError("命中缓存时不应调用 DeepSeek")

    monkeypatch.setattr(main, "run_deepseek_tool_loop", exploding_agent)

    response = _start(client, _create_job().task_id)

    assert response.json() == {"status": "done", "analysis": "# 早先分析过", "cached": True}


def test_ignore_cache_forces_a_fresh_run(client, monkeypatch, tmp_path):
    cache = AnalysisCache(tmp_path, model=main.settings.deepseek_model)
    cache.set("AbCd1234EfGh5678", 1, "# 旧报告")
    monkeypatch.setattr(main, "analysis_cache", cache)

    async def agent(data_store, report_code, fight_id, fight, summary):
        return "# 新报告"

    monkeypatch.setattr(main, "run_deepseek_tool_loop", agent)

    job = _create_job()
    started = _start(client, job.task_id, "?ignore_cache=true")
    assert started.json()["status"] == "running"

    state = _wait_done(client, job.task_id)
    assert state["status"] == "done"
    assert state["analysis"] == "# 新报告"
    assert cache.get("AbCd1234EfGh5678", 1) == "# 新报告", "新结果要写回缓存"


def test_restarting_a_running_job_does_not_start_a_second_one(client, monkeypatch):
    calls = []

    async def slow_agent(data_store, report_code, fight_id, fight, summary):
        calls.append(1)
        # 必须真的 await 一下，否则第一次 POST 时任务就已经跑完了，
        # 第二次 POST 走的是「已完成」分支，这个断言就证明不了并发保护
        await asyncio.sleep(0.05)
        return "# 报告"

    monkeypatch.setattr(main, "run_deepseek_tool_loop", slow_agent)

    job = _create_job()
    assert _start(client, job.task_id).json()["status"] == "running"
    assert _start(client, job.task_id).json()["status"] == "running"

    assert _wait_done(client, job.task_id)["analysis"] == "# 报告"
    assert len(calls) == 1, "同一个 task_id 不应并发跑两次"


def test_start_requires_login(client):
    assert client.post(f"/api/analysis/{_create_job().task_id}").status_code == 401


def test_poll_requires_login(client):
    assert client.get(f"/api/analysis/{_create_job().task_id}").status_code == 401


def test_unknown_task_is_404_on_both_verbs(client):
    assert _start(client, "does-not-exist").status_code == 404
    assert client.get("/api/analysis/does-not-exist", headers=_auth()).status_code == 404

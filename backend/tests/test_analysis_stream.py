import asyncio

import pytest
from fastapi.testclient import TestClient

from app import main
from app.auth import issue_token


class _NullCache:
    """Keep the stream tests from touching storage/analysis_cache."""

    def get(self, report_code: str, fight_id: int) -> str | None:
        return None

    def set(self, report_code: str, fight_id: int, analysis: str) -> None:
        pass


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "analysis_cache", _NullCache())
    monkeypatch.setattr(main.settings, "deepseek_api_key", "test-key")
    return TestClient(main.app)


def _create_job() -> object:
    return main.jobs.create(
        report_code="AbCd1234EfGh5678",
        fight_id=1,
        url="https://www.warcraftlogs.com/reports/AbCd1234EfGh5678#fight=1",
        fight={"id": 1, "start_time": 0, "end_time": 180000},
        summary={},
        tables={},
    )


def _stream_body(client: TestClient, task_id: str) -> str:
    token = issue_token()
    with client.stream(
        "GET",
        f"/api/analysis/{task_id}/stream",
        headers={"Authorization": f"Bearer {token}"},
    ) as response:
        assert response.status_code == 200
        return "".join(response.iter_text())


def test_stream_emits_keepalive_while_analysis_runs(client, monkeypatch):
    async def slow_agent(data_store, report_code, fight_id, fight, summary):
        await asyncio.sleep(0.3)
        return "# 复盘报告\n最终结论"

    # Shrink the interval so the test does not wait the real 15 seconds.
    monkeypatch.setattr(main, "KEEPALIVE_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(main, "run_deepseek_tool_loop", slow_agent)

    body = _stream_body(client, _create_job().task_id)

    assert ": keepalive" in body, "长静默期必须发心跳，否则会被 Cloudflare 掐断"
    assert "最终结论" in body
    assert '"type": "done"' in body


def test_stream_reports_agent_failure_as_error_event(client, monkeypatch):
    async def failing_agent(data_store, report_code, fight_id, fight, summary):
        raise RuntimeError("DeepSeek 返回 500")

    monkeypatch.setattr(main, "KEEPALIVE_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(main, "run_deepseek_tool_loop", failing_agent)

    body = _stream_body(client, _create_job().task_id)

    assert '"type": "error"' in body
    assert "DeepSeek 返回 500" in body


def test_stream_replays_completed_job_without_calling_agent(client, monkeypatch):
    async def exploding_agent(*args, **kwargs):
        raise AssertionError("已完成的任务不应再次调用 DeepSeek")

    monkeypatch.setattr(main, "run_deepseek_tool_loop", exploding_agent)

    job = _create_job()
    job.status = "completed"
    job.deepseek_full_text = "缓存好的报告"

    body = _stream_body(client, job.task_id)

    assert "缓存好的报告" in body
    assert '"type": "done"' in body

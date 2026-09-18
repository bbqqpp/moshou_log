from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncGenerator

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from .aggregation import summarize_tables
from .auth import ALLOWED_GUILD_NAME, is_token_valid, issue_token, validate_guild_name
from .cache import AnalysisCache
from .config import settings
from .deepseek_agent import run_deepseek_tool_loop
from .errors import WCLApiError, WCLAuthError, WCLUrlError
from .jobs import JobStore
from .schemas import ExtractRequest, LoginRequest
from .wcl import parse_wcl_url, WCLClient
from .wcl_data_store import WCLDataStore

app = FastAPI(title="WCL 战斗日志分析", version="0.1.0")
jobs = JobStore()

# Reverse proxies (Cloudflare and others) drop connections that stay silent for
# ~100s. The DeepSeek tool loop sends nothing back for minutes, so keep the SSE
# stream fed with comment lines — clients ignore them, proxies count them as bytes.
KEEPALIVE_INTERVAL_SECONDS = 15

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
CACHE_DIR = Path(__file__).resolve().parents[1] / "storage" / "analysis_cache"
WCL_DATA_DIR = Path(__file__).resolve().parents[1] / "storage" / "wcl_data"
analysis_cache = AnalysisCache(CACHE_DIR, settings.deepseek_model)
wcl_data_store = WCLDataStore(WCL_DATA_DIR)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _require_auth(authorization: str | None = Header(default=None)) -> str:
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="请先输入公会名登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not is_token_valid(parts[1]):
        raise HTTPException(
            status_code=401,
            detail="登录已失效，请重新输入公会名",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return parts[1]


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "wcl_configured": bool(settings.wcl_v1_api_key),
        "deepseek_configured": bool(settings.deepseek_api_key),
    }


@app.post("/api/login")
async def login(request: LoginRequest) -> dict[str, Any]:
    if not validate_guild_name(request.guild_name):
        raise HTTPException(status_code=401, detail="公会名不正确")
    return {
        "token": issue_token(),
        "guild_name": ALLOWED_GUILD_NAME,
    }


@app.get("/api/me")
async def me(_: str = Depends(_require_auth)) -> dict[str, Any]:
    """Probe endpoint: lets the frontend validate a stored token on page load."""
    return {"guild_name": ALLOWED_GUILD_NAME}


@app.post("/api/extract")
async def extract_fight(
    request: ExtractRequest,
    _: str = Depends(_require_auth),
) -> dict[str, Any]:
    if not settings.wcl_v1_api_key:
        raise HTTPException(status_code=500, detail="后端未配置 WCL_V1_API_KEY，请检查 backend/.env")

    try:
        report_code, fight_id = parse_wcl_url(request.url)
    except WCLUrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # `#fight=last` arrives as None and is resolved to the report's last boss
    # fight, so the stored data must be keyed by the fight we actually fetched.
    requested_last = fight_id is None

    client = WCLClient(settings)
    try:
        fight, tables, wcl_warnings = await client.extract_fight(report_code, fight_id)
    except WCLAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except WCLApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    resolved_fight_id = int(fight.get("id") or 0)
    if resolved_fight_id <= 0:
        raise HTTPException(status_code=502, detail="WCL 未返回有效的战斗 ID")
    fight_id = resolved_fight_id

    wcl_data_store.save(report_code, fight_id, fight, tables)

    fight_duration_ms = max(
        0,
        float(fight.get("end_time") or 0) - float(fight.get("start_time") or 0),
    )
    fight_start_ms = max(0, float(fight.get("start_time") or 0))
    summary = summarize_tables(
        tables,
        timeline_limit=settings.timeline_preview_limit,
        duration_ms=fight_duration_ms,
        fight_start_ms=fight_start_ms,
    )
    job = jobs.create(
        report_code=report_code,
        fight_id=fight_id,
        url=request.url,
        fight=fight,
        summary=summary,
        tables=tables,
    )

    warnings: list[str] = list(wcl_warnings)
    if requested_last:
        # Say which fight `last` actually landed on — WCL's boss marking is
        # inconsistent, so this is not always the pull the user just did.
        outcome = "击杀" if fight.get("kill") else "灭团"
        warnings.append(
            f"`fight=last` 已解析为第 {fight_id} 场 Boss 战："
            f"{fight.get('name') or '未知'}（{outcome}）"
        )
    if summary.get("table_errors"):
        warnings.append("部分 WCL tables 获取失败，分析可能缺少该部分数据")

    return {
        "task_id": job.task_id,
        "report_code": report_code,
        "fight_id": fight_id,
        "fight": fight,
        "summary": summary,
        "warnings": warnings,
    }


@app.get("/api/analysis/{task_id}/stream")
async def analysis_stream(
    task_id: str,
    ignore_cache: bool = False,
    _: str = Depends(_require_auth),
) -> StreamingResponse:
    job = jobs.get(task_id)
    if job is None:
        raise HTTPException(status_code=404, detail="分析任务不存在或已过期，请重新提交 WCL 链接")

    return StreamingResponse(
        _deepseek_stream(job, ignore_cache=ignore_cache),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _first_delta(payload: dict[str, Any]) -> str:
    try:
        choices = payload.get("choices") or []
        if not choices:
            return ""
        choice = choices[0] or {}
        delta = choice.get("delta") or choice.get("message") or {}
        content = delta.get("content")
        if content:
            return str(content)
        return str(delta.get("reasoning_content") or "")
    except (AttributeError, IndexError, TypeError):
        return ""


async def _deepseek_stream(job, ignore_cache: bool = False) -> AsyncGenerator[str, None]:
    # Replay a finished in-memory task, unless cache is explicitly ignored.
    if not ignore_cache and job.status == "completed" and job.deepseek_full_text:
        yield _sse({"type": "progress", "message": "使用已完成的本地分析结果"})
        yield _sse({"type": "delta", "content": job.deepseek_full_text})
        yield _sse({"type": "done", "message": "分析完成"})
        return

    cached = None
    if not ignore_cache:
        cached = analysis_cache.get(job.report_code, job.fight_id)

    if cached:
        job.status = "completed"
        job.deepseek_full_text = cached
        yield _sse({"type": "progress", "message": "命中本地分析缓存，直接返回结果"})
        yield _sse({"type": "delta", "content": cached})
        yield _sse({"type": "done", "message": "分析完成"})
        return

    if job.status == "streaming":
        yield _sse({"type": "error", "message": "该任务的分析已经在进行中"})
        return

    job.status = "streaming"
    job.deepseek_full_text = ""
    job.error_message = ""
    yield _sse({"type": "progress", "message": "正在连接 DeepSeek..."})

    if not settings.deepseek_api_key:
        job.status = "error"
        job.error_message = "后端未配置 DEEPSEEK_API_KEY，请检查 backend/.env"
        yield _sse({"type": "error", "message": job.error_message})
        return

    try:
        yield _sse({"type": "progress", "message": "DeepSeek 正在按需查询本地 WCL 数据..."})

        analysis_task = asyncio.create_task(
            run_deepseek_tool_loop(
                wcl_data_store,
                job.report_code,
                job.fight_id,
                job.fight,
                job.summary,
            )
        )
        try:
            while True:
                done, _ = await asyncio.wait(
                    {analysis_task},
                    timeout=KEEPALIVE_INTERVAL_SECONDS,
                )
                if done:
                    break
                yield ": keepalive\n\n"
            final_text = analysis_task.result()
        except BaseException:
            # Client hung up (GeneratorExit) or the request was cancelled.
            analysis_task.cancel()
            raise

        job.deepseek_full_text = final_text

        try:
            analysis_cache.set(job.report_code, job.fight_id, job.deepseek_full_text)
        except OSError:
            pass

        job.status = "completed"
        yield _sse({"type": "delta", "content": final_text})
        yield _sse({"type": "done", "message": "分析完成"})
    except Exception as exc:
        job.status = "error"
        job.error_message = str(exc)
        yield _sse({"type": "error", "message": job.error_message})


if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")

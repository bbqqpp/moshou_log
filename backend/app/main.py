from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .aggregation import summarize_tables
from .auth import ALLOWED_GUILD_NAME, is_token_valid, issue_token, validate_guild_name
from .cache import AnalysisCache
from .config import settings
from .deepseek_agent import run_deepseek_tool_loop
from .errors import WCLApiError, WCLAuthError, WCLUrlError
from .jobs import JobStore
from .player_compare import resolve_player
from .wow import is_mythic_plus
from .player_report import (
    build_comparison_payload,
    build_roster,
    build_single_payload,
    run_player_report,
)
from .report_store import PlayerReportStore, strip_preamble
from .schemas import ExtractRequest, LoginRequest, PlayerReportRequest
from .wcl import parse_wcl_url, WCLClient
from .wcl_data_store import WCLDataStore

app = FastAPI(title="WCL 战斗日志分析", version="0.1.0")
jobs = JobStore()

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
CACHE_DIR = Path(__file__).resolve().parents[1] / "storage" / "analysis_cache"
WCL_DATA_DIR = Path(__file__).resolve().parents[1] / "storage" / "wcl_data"
PLAYER_REPORT_DIR = Path(__file__).resolve().parents[1] / "storage" / "player_reports"
analysis_cache = AnalysisCache(CACHE_DIR, settings.deepseek_model)
wcl_data_store = WCLDataStore(WCL_DATA_DIR)
player_report_store = PlayerReportStore(PLAYER_REPORT_DIR)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
        "wcl_configured": bool(settings.wcl_client_id and settings.wcl_client_secret),
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
    if not (settings.wcl_client_id and settings.wcl_client_secret):
        raise HTTPException(
            status_code=500,
            detail="后端未配置 WCL_CLIENT_ID / WCL_CLIENT_SECRET，请检查 backend/.env",
        )

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
        fight=fight,
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
        # 副本不是「Boss 战」，结果也不是击杀/灭团 —— 措辞要跟着分流。
        if is_mythic_plus(fight):
            bonus = fight.get("keystoneBonus")
            # keystoneBonus 缺失（V1 历史缓存）时是「未知」，不是「超时」
            outcome = "限时" if (bonus or 0) > 0 else ("超时" if bonus is not None else "已通关")
            label = f"+{fight.get('keystoneLevel') or '?'} {fight.get('name') or '未知'}（{outcome}）"
        else:
            outcome = "击杀" if fight.get("kill") else "灭团"
            label = f"{fight.get('name') or '未知'}（{outcome}）"
        warnings.append(f"`fight=last` 已解析为第 {fight_id} 场：{label}")
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


# 整场复盘要跑 DeepSeek 工具循环 1-3 分钟，期间一个字节都不发。Cloudflare 的代理读
# 超时是 100 秒（超了返回 524，免费/Pro 版不可调），所以不能把这一整个等待挂在一个请求上。
# 改成：POST 立刻返回、后台任务继续跑，客户端每几秒 GET 一次状态。
# 每个请求都是短请求，任何反代都不会掐；客户端拿到的仍然是整段正文，没有流式输出。
_background_tasks: set[asyncio.Task] = set()

# 任务在内存里的状态；对客户端只暴露 running / done / error 三态
_JOB_RUNNING = ("ready", "running")


def _job_state(job) -> dict[str, Any]:
    """把内部 job 状态映射成客户端能用的三态。"""
    if job.status == "completed" and job.deepseek_full_text:
        return {"status": "done", "analysis": job.deepseek_full_text}
    if job.status == "error":
        return {"status": "error", "message": job.error_message or "分析失败"}
    return {"status": "running"}


async def _run_analysis_job(job) -> None:
    """后台跑完整场复盘。结果写回 job 和缓存，客户端靠轮询取。"""
    try:
        text = await run_deepseek_tool_loop(
            wcl_data_store,
            job.report_code,
            job.fight_id,
            job.fight,
            job.summary,
        )
    except Exception as exc:
        job.status = "error"
        job.error_message = str(exc)
        return

    job.deepseek_full_text = text
    try:
        analysis_cache.set(job.report_code, job.fight_id, text, fight=job.fight)
    except OSError:
        pass
    job.status = "completed"


@app.post("/api/analysis/{task_id}")
async def start_analysis(
    task_id: str,
    ignore_cache: bool = False,
    _: str = Depends(_require_auth),
) -> dict[str, Any]:
    """启动整场复盘。立即返回状态，不等 DeepSeek 跑完。

    命中缓存或任务已完成时直接返回 `{"status": "done", "analysis": ...}`，
    否则返回 `{"status": "running"}`，客户端用 GET 同一个路径轮询。
    """
    job = jobs.get(task_id)
    if job is None:
        raise HTTPException(status_code=404, detail="分析任务不存在或已过期，请重新提交 WCL 链接")

    if not ignore_cache and job.status == "completed" and job.deepseek_full_text:
        return {"status": "done", "analysis": job.deepseek_full_text, "cached": True}

    if not ignore_cache:
        cached = analysis_cache.get(job.report_code, job.fight_id)
        if cached:
            job.status = "completed"
            job.deepseek_full_text = cached
            return {"status": "done", "analysis": cached, "cached": True}

    if job.status == "running":
        # 已经在跑了（多半是重复提交或刷新了页面），让客户端接着轮询就好
        return _job_state(job)

    if not settings.deepseek_api_key:
        raise HTTPException(
            status_code=500, detail="后端未配置 DEEPSEEK_API_KEY，请检查 backend/.env"
        )

    job.deepseek_full_text = ""
    job.error_message = ""
    job.status = "running"

    task = asyncio.create_task(_run_analysis_job(job))
    # 保住强引用，否则任务可能在跑完前被 GC 掉
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return _job_state(job)


@app.get("/api/analysis/{task_id}")
async def get_analysis(
    task_id: str,
    _: str = Depends(_require_auth),
) -> dict[str, Any]:
    """轮询整场复盘的进度。"""
    job = jobs.get(task_id)
    if job is None:
        raise HTTPException(status_code=404, detail="分析任务不存在或已过期，请重新提交 WCL 链接")
    return _job_state(job)


# ---------------------------------------------------------------------------
# 报告库：侧边栏列表、分享用的只读接口、玩家报告生成
#
# 读接口**不要求登录** —— 分享链接要能被对方直接打开，而 WCL report code 本身就是
# 16 位随机串，充当了那个不可猜测的凭据（和 WCL 自己 unlisted report 的模型一致）。
# 列表和生成仍然要登录：前者会暴露「有哪些报告」，后者要烧 token。
#
# 注意：这几条必须声明在文件末尾的 StaticFiles 挂载**之前**。
# FastAPI 按声明顺序匹配，挂在 `/` 上的 StaticFiles 会吞掉之后注册的一切。
# ---------------------------------------------------------------------------


def _load_fight_view(
    report_code: str,
    fight_id: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]]]:
    """读出单场的元数据 / 聚合摘要 / 玩家名册。

    要 json.load 一个最大 56MB 的文件（实测 0.57s）+ 聚合 0.31s，所以调用方一律
    用 `run_in_threadpool` 包起来，别阻塞事件循环。侧边栏列表不走这里 —— 那会变成 31 倍。
    """
    data = wcl_data_store.load(report_code, fight_id)
    if data is None:
        return None, None, []

    fight = data.get("fight") or {}
    fight_start_ms = max(0.0, float(fight.get("start_time") or 0))
    duration_ms = max(0.0, float(fight.get("end_time") or 0) - fight_start_ms)

    summary = summarize_tables(
        data.get("tables") or {},
        timeline_limit=settings.timeline_preview_limit,
        duration_ms=duration_ms,
        fight_start_ms=fight_start_ms,
        fight=fight,
    )
    return fight, summary, build_roster(data)


@app.get("/api/reports")
async def list_reports(_: str = Depends(_require_auth)) -> dict[str, Any]:
    """侧边栏的报告库：每场已分析过的战斗，以及该场已生成的玩家报告。"""
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for record in player_report_store.list_all():
        key = (str(record.get("report_code")), int(record.get("fight_id") or 0))
        grouped.setdefault(key, []).append(record)

    items: list[dict[str, Any]] = []
    for row in analysis_cache.list_all():
        key = (str(row.get("report_code")), int(row.get("fight_id") or 0))
        items.append(
            {
                "report_code": row.get("report_code"),
                "fight_id": row.get("fight_id"),
                "created_at": row.get("created_at"),
                # 系统提示词/模型改过之后旧报告会变 stale。刻意仍然列出来并打标记，
                # 不隐藏 —— 否则改一次提示词侧边栏就空了。
                "stale": bool(row.get("stale")),
                "fight": row.get("fight"),
                "player_reports": grouped.get(key, []),
            }
        )
    return {"reports": items}


@app.get("/api/reports/{report_code}/{fight_id}")
async def get_fight_report(report_code: str, fight_id: int) -> dict[str, Any]:
    """单场战斗报告（公开只读，用于分享链接）。"""
    record = analysis_cache.read_record(report_code, fight_id)
    if record is None:
        raise HTTPException(status_code=404, detail="这场战斗还没有分析报告")

    fight, summary, roster = await run_in_threadpool(_load_fight_view, report_code, fight_id)

    return {
        "report_code": report_code,
        "fight_id": fight_id,
        "fight": fight or record.get("fight"),
        "created_at": record.get("created_at"),
        "stale": bool(record.get("stale")),
        # 缓存里正文开头常带一段模型前言（实测 31 份里 30 份有），不清掉会顶在报告最上面
        "analysis": strip_preamble(str(record.get("analysis") or "")),
        "summary": summary,
        "roster": roster,
        # 分析正文在很小的 analysis_cache 里，逐场数据却在 1GB 上限、被 gitignore 的
        # wcl_data 里 —— 那份文件被清掉时这里会是 None。**必须显式告诉前端**，
        # 否则前端把 `summary?.death_count || 0` 渲染成「死亡数量 0 ·
        # 本场战斗没有死亡记录」，把「数据缺失」断言成「这场没死人」。
        "data_missing": summary is None,
        "player_reports": player_report_store.list_for_fight(report_code, fight_id),
    }


@app.get("/api/reports/{report_code}/{fight_id}/players/{kind}/{slug}")
async def get_player_report(
    report_code: str,
    fight_id: int,
    kind: str,
    slug: str,
) -> dict[str, Any]:
    """单份玩家报告（公开只读，用于分享链接）。"""
    record = player_report_store.get(report_code, fight_id, kind, slug)
    if record is None:
        raise HTTPException(status_code=404, detail="这份玩家报告不存在")

    return {
        "report_code": record.get("report_code"),
        "fight_id": record.get("fight_id"),
        "kind": record.get("kind"),
        "slug": record.get("slug"),
        "players": record.get("players") or [],
        "specs": record.get("specs") or [],
        "created_at": record.get("created_at"),
        # 玩家报告也有提示词版本了（report_store._signature）—— 改了玩家提示词之后，
        # 旧报告会在这里被标出来，而不是继续冒充当前版本的输出
        "stale": bool(record.get("stale")),
        "analysis": strip_preamble(str(record.get("analysis") or "")),
    }


def _persist_player_report(
    report_code: str,
    fight_id: int,
    kind: str,
    players: list[str],
    payload: dict[str, Any],
    text: str,
) -> dict[str, Any]:
    if kind == "single":
        specs = [str(payload.get("spec") or "")]
        player_ids = [int(payload.get("player_id") or 0)]
    else:
        sides = (payload.get("a") or {}, payload.get("b") or {})
        specs = [str(side.get("spec") or "") for side in sides]
        player_ids = [int(side.get("player_id") or 0) for side in sides]

    return player_report_store.save(
        report_code,
        fight_id,
        kind,
        players,
        player_ids=player_ids,
        specs=specs,
        model=settings.deepseek_model,
        analysis=text,
        payload=payload,
    )


@app.post("/api/player-reports/analyze")
async def generate_player_report(
    request: PlayerReportRequest,
    _: str = Depends(_require_auth),
) -> dict[str, Any]:
    """生成玩家报告并一次性返回正文。1 个玩家 = 单人复盘，2 个 = 对比。"""
    unique = list(dict.fromkeys(name.strip() for name in request.players if name.strip()))
    if not unique:
        raise HTTPException(status_code=400, detail="请至少选择一名玩家")
    if len(unique) > 2:
        raise HTTPException(status_code=400, detail="最多只能选两名玩家")

    if not settings.deepseek_api_key:
        raise HTTPException(status_code=500, detail="后端未配置 DEEPSEEK_API_KEY，请检查 backend/.env")

    data = await run_in_threadpool(wcl_data_store.load, request.report_code, request.fight_id)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail="本地没有这场的原始战斗数据，请先重新分析一次这场战斗",
        )

    # 先把名字解析掉：解析不了立刻返回带在场名册的错误，不要等 DeepSeek 跑完才失败
    for name in unique:
        try:
            await run_in_threadpool(resolve_player, data, name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    kind = "single" if len(unique) == 1 else "comparison"
    if kind == "single":
        payload = await run_in_threadpool(build_single_payload, data, unique[0])
    else:
        # 网页入口只能选同一场的两个人，所以两边是同一份 data
        payload = await run_in_threadpool(
            build_comparison_payload, data, unique[0], data, unique[1]
        )

    try:
        text = strip_preamble(await run_player_report(payload, kind))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"DeepSeek 生成失败：{exc}") from exc

    record = await run_in_threadpool(
        _persist_player_report,
        request.report_code,
        request.fight_id,
        kind,
        unique,
        payload,
        text,
    )

    return {
        "kind": kind,
        "players": unique,
        "slug": record.get("slug"),
        "analysis": text,
    }


if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")

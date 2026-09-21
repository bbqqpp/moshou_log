#!/usr/bin/env python3
"""WCL 战斗日志分析的 MCP 服务器。

把本项目的 WCL 取数、查询、深度分析和 DeepSeek 复盘暴露成 MCP 工具，
让 Claude 能在分析过程中主动查数据，而不是只能一次性跑完拿一份报告。

跑在自己的虚拟环境里（`mcp_server/.venv`），只 import 后端中**不依赖 FastAPI**
的模块 —— 后端的 FastAPI/Starlette 版本被锁得很死，在这个环境里装会冲突。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from mcp.server.mcpserver import MCPServer  # noqa: E402

from app.aggregation import (  # noqa: E402
    COUNTED_ENTRY_KEYS,
    _build_actor_catalog,
    _entries,
    _number,
    summarize_tables,
)
from app.boss_guides import find_boss_guide  # noqa: E402
from app.config import settings  # noqa: E402
from app.deep_analysis import (  # noqa: E402
    ability_counts,
    arcane_charge_curve,
    cast_sequence,
    cooldown_uses,
)
from app.player_compare import (  # noqa: E402
    build_comparison_from_inputs,
    build_player_payload,
    load_fight,
    resolve_player,
)
from app.wcl_data_store import WCLDataStore  # noqa: E402
from app.wow import is_mythic_plus  # noqa: E402

server = MCPServer(
    name="wcl-analyzer",
    version="0.1.0",
    instructions=(
        "魔兽世界 WCL 战斗日志分析。所有工具都基于本地缓存的完整战斗数据，"
        "缺少数据时会自动从 WCL 下载（约 20 秒）。玩家名必须是游戏内原名。"
    ),
)


def _dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=1)


@server.tool()
def fetch_fight(report_code: str, fight_id: int) -> str:
    """下载（或确认已缓存）一场战斗的完整 WCL 数据。

    第一次分析某场战斗必须先调用它，约 20 秒；之后同一场会直接用本地缓存。
    返回战斗的基本信息（Boss、难度、人数、时长、是否击杀）。
    """
    data = load_fight(report_code, fight_id)
    fight = data.get("fight") or {}
    duration_ms = max(0.0, _number(fight.get("end_time")) - _number(fight.get("start_time")))
    return _dumps(
        {
            "report_code": report_code,
            "fight_id": fight_id,
            "name": fight.get("name"),
            "boss": fight.get("boss"),
            "difficulty": fight.get("difficulty"),
            "size": fight.get("size"),
            "kill": fight.get("kill"),
            "duration_seconds": round(duration_ms / 1000),
            # 只数真正的战斗事件。`tables` 里还有 rankings / survivability /
            # player_details 这几张按人一行的派生表，它们也是 {"entries": [...]}
            # 形状但**不是事件**，混进来会让这个数字比实际多出好几倍人数。
            # aggregation._total_entries 用的是同一份白名单。
            "event_count": sum(
                len(_entries(payload))
                for key, payload in (data.get("tables") or {}).items()
                if key in COUNTED_ENTRY_KEYS
            ),
        }
    )


@server.tool()
def list_players(report_code: str, fight_id: int) -> str:
    """列出这场战斗的所有参战玩家及其职业专精和角色（坦克/治疗/输出）。"""
    data = load_fight(report_code, fight_id)
    catalog = _build_actor_catalog(data.get("tables") or {})
    players = sorted(
        (
            {
                "player_id": actor_id,
                "name": info.get("name"),
                "spec": info.get("role_label"),
                "spec_id": info.get("spec_id"),
            }
            for actor_id, info in catalog.items()
            # 只列玩家角色 —— 目录里还有 BOSS / NPC / 宠物（它们的 class 解析不出
            # 职业）。实测不筛的话 LLM 会被告知在场有 25 名"玩家"，其中两个
            # spec 是 "Boss"
            if info.get("name") and info.get("class")
        ),
        key=lambda row: str(row["spec"]),
    )
    return _dumps(players)


@server.tool()
def query_data(
    report_code: str,
    fight_id: int,
    data_type: str,
    player: str = "",
    start_time: float = 0,
    end_time: float = 0,
    limit: int = 200,
) -> str:
    """查询本地缓存的原始战斗数据。

    data_type 可选：damage-done / healing / damage-taken / deaths / buffs / debuffs /
    casts / interrupts / dispels / summons / buff_debuff_events / cast_events /
    death_events / interrupt_events / dispel_events / resource_events /
    spawn_events / combatant_info_events / fight / rankings / survivability /
    player_details。

    rankings 是每人的 parse 百分位（同装等区间内的水平，**灭团场次为空**）；
    survivability 是 0-1 的生存分（相对同专精算）；player_details 含专精、装等
    与爆发药水／治疗石使用次数。这三项是迁移到 WCL V2 之后才有的 —— 分析迁移前
    拉下来的旧战斗会返回「这份缓存太旧」而不是空列表。

    player 可给名字（支持模糊匹配）；start_time / end_time 单位毫秒，
    小于战斗开始时间的输入会被当作「战斗相对时间」自动换算。limit 上限 1000。
    """
    store = WCLDataStore(BACKEND_DIR / "storage" / "wcl_data")
    result = store.query(
        report_code,
        fight_id,
        data_type,
        player=player or None,
        start_time=start_time or None,
        end_time=end_time or None,
        limit=limit,
    )
    return _dumps(result)


@server.tool()
def player_rotation(report_code: str, fight_id: int, player: str) -> str:
    """取一名玩家的技能释放序列、技能次数、大招真实使用次数和资源曲线。

    比原始事件流更适合分析手法：
    - 已排除平砍（自动攻击不是玩家的技能决策）
    - 引导类技能（如神圣赞美诗）已按 8 秒窗口归并，不会把一次引导算成五六次
    - 资源曲线（奥术充能）已把「产生」和「消耗」两条流合并，
      并给出消耗时层数分布和满层浪费次数
    """
    data = load_fight(report_code, fight_id)
    fight = data.get("fight") or {}
    fight_start = _number(fight.get("start_time"))
    tables = data.get("tables") or {}

    actor_id, info = resolve_player(data, player)
    payload = build_player_payload(data, player)

    charges = arcane_charge_curve(tables, actor_id, fight_start)
    result = {
        "player": payload["player"],
        "spec": payload["spec"],
        "item_level": payload["item_level"],
        "output_kind": payload["output_kind"],
        "cast_count": payload["cast_count"],
        "ability_counts": payload["ability_counts"],
        "cooldown_uses": payload["cooldown_uses"],
        "rotation": payload["rotation"],
        "arcane_charge": charges if charges["消耗次数"] else None,
    }
    return _dumps(result)


@server.tool()
def compare_players(
    a_report: str,
    a_fight: int,
    a_player: str,
    b_report: str,
    b_fight: int,
    b_player: str,
) -> str:
    """深度对比两名玩家的手法与输出/治疗。

    返回包含：两人的输出或治疗数据（含治疗职业的过量率）、技能构成、技能序列、
    大招真实使用次数、资源曲线，以及两项需要交叉计算才能得到的判定：
    - `idle_comparison`：剔除「两人同时静默的机制时段」后的个人空窗。
      不剔除会把团队机制造成的停手算成个人问题，得出相反结论。
    - `fairness`：专精/装等/阵亡/击杀等客观事实，用于判断这个对比公不公平。
    """
    payload = build_comparison_from_inputs(
        a_report, a_fight, a_player, b_report, b_fight, b_player
    )
    return _dumps(payload)


@server.tool()
def deepseek_review(report_code: str, fight_id: int, player: str = "") -> str:
    """跑完整的 DeepSeek 复盘（网页版应用的流程），返回一份中文 Markdown 报告。

    流程：拉取全量数据 → 聚合摘要 → 交给 DeepSeek，模型按需调用工具查询本地数据
    → 输出整场复盘。单场分析约需 1-3 分钟，会消耗 DeepSeek token。

    同一场战斗的分析结果会缓存；player 参数当前不参与分析范围，仅用于确认玩家在场。

    **这是同步工具，不能改成 `async def`**：`load_fight` 在本地缓存未命中时会自己
    调 `asyncio.run(...)` 去下载，而 `asyncio.run` 在已经运行的事件循环里必然抛
    `RuntimeError`。MCP 会把同步工具派发到线程池，所以同步版本才是对的——
    改成 async 会让「未缓存的战斗」这条路径彻底跑不通（实测确认）。
    """
    if not settings.deepseek_api_key:
        return "错误：backend/.env 里没有配置 DEEPSEEK_API_KEY"

    data = load_fight(report_code, fight_id)
    fight = data.get("fight") or {}
    fight_id = int(data.get("fight_id") or fight_id)
    tables = data.get("tables") or {}

    if player:
        resolve_player(data, player)  # 找不到会直接抛错

    fight_start = _number(fight.get("start_time"))
    duration_ms = max(0.0, _number(fight.get("end_time")) - fight_start)
    summary = summarize_tables(
        tables,
        timeline_limit=settings.timeline_preview_limit,
        duration_ms=duration_ms,
        fight_start_ms=fight_start,
        fight=fight,
    )

    from app.deepseek_agent import run_deepseek_tool_loop

    store = WCLDataStore(BACKEND_DIR / "storage" / "wcl_data")
    report = asyncio.run(run_deepseek_tool_loop(store, report_code, fight_id, fight, summary))
    return report


@server.tool()
def boss_guide(report_code: str, fight_id: int) -> str:
    """取这场战斗对应 BOSS 的本地攻略（阶段划分、机制、常见灭团点、检查清单）。

    **只适用于团本 BOSS 战。** 大秘境没有攻略库 —— 副本的 `encounterID`
    是最终 BOSS 的 NPC id，拿它去匹配会得到一个语义完全不对的答案。
    """
    data = load_fight(report_code, fight_id)
    fight = data.get("fight") or {}

    if is_mythic_plus(fight):
        return (
            f"这是一场大秘境（+{fight.get('keystoneLevel')} "
            f"{fight.get('name') or '未知'}），不是团本 BOSS 战。\n"
            "本地攻略库只覆盖团本 BOSS，没有副本的路线/小怪/词缀数据。\n"
            "大秘境的分析请看 fetch_fight 返回的逐段拉怪与时间账，"
            "或用 deepseek_review 跑一次大秘境复盘。"
        )

    guide = find_boss_guide(fight)
    if guide is None:
        return "这个 BOSS 还没有本地攻略数据（backend/storage/boss_guides/）"
    return _dumps(guide)


if __name__ == "__main__":
    server.run("stdio")

from __future__ import annotations

import json
from typing import Any, Mapping

import httpx

from .config import settings
from .boss_guides import find_boss_guide
from .prompt import _sanitize, _light_fight, system_prompt_for
from .wow import is_mythic_plus
from .wcl_data_store import WCLDataStore

MAX_TOOL_ROUNDS = 20

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_wcl_data",
            "description": (
                "查询服务器本地保存的完整 WCL tables 数据。"
                "可以按数据类型、玩家名和时间范围获取详细数据。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "data_type": {
                        "type": "string",
                        "enum": [
                            "fight",
                            "damage-done",
                            "healing",
                            "damage-taken",
                            "deaths",
                            "buffs",
                            "debuffs",
                            "casts",
                            "interrupts",
                            "dispels",
                            "summons",
                            "buff_debuff_events",
                            "cast_events",
                            "death_events",
                            "interrupt_events",
                            "dispel_events",
                            "resource_events",
                            "spawn_events",
                            "combatant_info_events",
                            "rankings",
                            "survivability",
                            "player_details",
                        ],
                    },
                    "player": {
                        "type": "string",
                        "description": "可选，玩家名或游戏内 ID；事件流会按 sourceID/targetID 自动匹配",
                    },
                    "start_time": {
                        "type": "number",
                        "description": "可选，开始时间戳（毫秒）；小于 fight_start_time 的输入会按战斗相对时间换算",
                    },
                    "end_time": {
                        "type": "number",
                        "description": "可选，结束时间戳（毫秒）；小于 fight_start_time 的输入会按战斗相对时间换算",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1000,
                        "description": "最多返回多少条数据",
                    },
                },
                "required": ["data_type"],
            },
        },
    }
]


def _build_initial_messages(
    report_code: str,
    fight_id: int,
    fight: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> list[dict[str, Any]]:
    system_content = (
        system_prompt_for(fight)
        + "\n\n"
        + "完整数据已经保存在服务器本地，不要试图一次读取所有数据。"
        + "请先分析前端发来的摘要，按需调用 query_wcl_data 查询具体时间段、玩家或数据类型。"
        + "查询事件流时必须给尽量短的时间窗口，避免只取到前 1000 条而误判数据不完整。"
        + f"最多查询 {MAX_TOOL_ROUNDS} 轮，然后在已有数据范围内输出最终中文 Markdown 复盘报告。"
    )

    death_windows: list[dict[str, Any]] = []
    fight_start_ms = float(fight.get("start_time") or 0)
    for death in summary.get("deaths", []):
        raw = death.get("raw") or {}
        death_time = raw.get("timestamp") or raw.get("deathTime")
        if death_time is None and death.get("timestamp") is not None:
            death_time = fight_start_ms + float(death.get("timestamp"))
        death_windows.append(
            {
                "player": death.get("player"),
                "player_id": raw.get("id"),
                "death_time": death_time,
                "death_time_relative_ms": death.get("timestamp"),
                "pre_window_ms": 15000,
                "post_window_ms": 3000,
            }
        )

    # 大秘境**完全不写 `boss_guide` 这个键**（不是写 null）——连键名带概念
    # 都不该出现在大秘境的分析上下文里。团本照旧（没有攻略时保留 null，
    # 提示词里本来就是按「如果附带 boss_guide」措辞的）。
    guide_fields = (
        {} if is_mythic_plus(fight) else {"boss_guide": find_boss_guide(fight)}
    )

    user_payload = {
        "report_code": report_code,
        "fight_id": fight_id,
        "fight": _light_fight(fight),
        "summary": _sanitize(dict(summary)),
        "data_inventory": {
            "aggregate_tables": [
                "damage-done",
                "healing",
                "damage-taken",
                "deaths",
                "buffs",
                "debuffs",
                "casts",
                "interrupts",
                "dispels",
                "summons",
            ],
            "detailed_event_streams_used": [
                "buff_debuff_events",
                "cast_events",
                "death_events",
                "interrupt_events",
                "dispel_events",
                "resource_events",
                "spawn_events",
                "combatant_info_events",
            ],
            # V2 独有。灭团场次 rankings 为空（WCL 只给击杀排名）。
            "derived_tables": [
                "rankings",
                "survivability",
                "player_details",
            ],
            "not_fetched_candidates_for_future_optimization": [
                "high-frequency positional sampling for movement reconstruction",
            ],
        },
        "death_windows": death_windows,
        **guide_fields,
        "query_hint": {
            "priority": [
                "deaths",
                "death_events",
                "buff_debuff_events",
                "cast_events",
                "buffs",
                "debuffs",
                "casts",
                "interrupts",
                "dispels",
                "interrupt_events",
                "dispel_events",
                "resource_events",
                "spawn_events",
                "combatant_info_events",
            ],
            "available_time_unit": "milliseconds",
            "max_results_per_call": 1000,
            "time_semantics": "WCL timestamp 以 report 起点为 0；小于 fight.start_time 的输入会按战斗相对时间自动换算",
        },
    }
    user_text = (
        "请根据下面的战斗摘要开始复盘。"
        "如需更多证据，请调用 query_wcl_data 工具查询本地完整数据。\n\n"
        # 紧凑序列化：这一整块每轮 tool call 都会重发，缩进纯属白烧 token。
        # 模型读紧凑 JSON 没有障碍，语义也完全等价。
        f"```json\n{json.dumps(user_payload, ensure_ascii=False, separators=(',', ':'))}\n```"
    )

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_text},
    ]


def _first_message(response: dict[str, Any]) -> dict[str, Any]:
    choices = response.get("choices") or []
    if not choices:
        raise RuntimeError("DeepSeek 未返回 choices")
    message = choices[0].get("message") or {}
    if not isinstance(message, dict):
        raise RuntimeError("DeepSeek message 格式异常")
    return message


def _parse_tool_args(tool_call: dict[str, Any]) -> dict[str, Any]:
    function = tool_call.get("function") or {}
    raw = function.get("arguments") or "{}"
    try:
        args = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return args if isinstance(args, dict) else {}


async def run_deepseek_tool_loop(
    data_store: WCLDataStore,
    report_code: str,
    fight_id: int,
    fight: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> str:
    messages = _build_initial_messages(report_code, fight_id, fight, summary)
    endpoint = f"{settings.deepseek_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }

    timeout = httpx.Timeout(600.0, connect=20.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for _ in range(MAX_TOOL_ROUNDS):
            response = await client.post(
                endpoint,
                headers=headers,
                json={
                    "model": settings.deepseek_model,
                    "messages": messages,
                    "tools": TOOLS,
                    "stream": False,
                    "thinking": {"type": "disabled"},
                    "max_tokens": settings.deepseek_max_tokens,
                },
            )

            if response.status_code != 200:
                body = response.text[:1200]
                raise RuntimeError(f"DeepSeek 返回 {response.status_code}：{body}")

            payload = response.json()
            message = _first_message(payload)
            content = str(message.get("content") or "")
            tool_calls = message.get("tool_calls") or []

            if not tool_calls:
                if not content:
                    raise RuntimeError("DeepSeek 未返回最终分析内容")
                return content

            messages.append(
                {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls,
                }
            )

            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                args = _parse_tool_args(tool_call)
                query = {
                    "report_code": report_code,
                    "fight_id": fight_id,
                    "data_type": str(args.get("data_type") or "fight"),
                }
                if args.get("player") is not None:
                    query["player"] = str(args["player"])
                if args.get("start_time") is not None:
                    query["start_time"] = float(args["start_time"])
                if args.get("end_time") is not None:
                    query["end_time"] = float(args["end_time"])
                if args.get("limit") is not None:
                    query["limit"] = int(args["limit"])

                result = data_store.query(**query)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id") or "",
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

    raise RuntimeError(f"DeepSeek 查询轮次超过 {MAX_TOOL_ROUNDS} 次，请简化分析数据范围")

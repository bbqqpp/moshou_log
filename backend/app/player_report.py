"""玩家维度的报告：单人复盘与双人对比。

网页端和 `.claude/skills/wcl-rotation-compare` 共用这里的 payload 构建和提示词。
两边各存一份必然漂移 —— 和 `player_compare.py` 存在的理由一样。

和整场复盘的区别：**不走 DeepSeek Tool Call 循环**。payload 已经把该算的都算好了
（`build_player_payload` 的输出），模型不需要再去查本地数据，一次非流式调用即可。
"""
from __future__ import annotations

import json
from typing import Any, Mapping

import httpx

from .aggregation import (
    _build_actor_catalog,
    _item_levels_by_actor,
    _number,
    _rank_rows,
)
from .config import settings
from .player_compare import build_comparison, build_player_payload

SINGLE_SYSTEM_PROMPT = """你是一名魔兽世界正式服（The War Within）战斗日志分析师，正在复盘**一名玩家**的这场战斗表现。

用户会给你这名玩家的施法序列与输出/治疗数据。`output_kind` 字段说明该看哪一边：
`"damage"` 看 `output_total` / `ability_breakdown` / `per_second_by_active_time` 等伤害字段；
`"healing"` 看治疗字段以及 `overheal_percent`。

**这是单人复盘，没有对比对象**，不要编造"比谁打得好"这类结论，也不要把团队层面的问题
（灭团、团队承伤高）算到这个人头上。只评估这个人在自己可控范围内的表现。

分析必须按以下结构输出中文 Markdown：

## 一、结论
先用一段话给这名玩家的整体表现下判断，再列 2-4 条要点。必须引用具体数字
（技能次数、占比、时间点），不要写"表现不错"这类空话。

## 二、输出 / 治疗构成
拆解 `ability_breakdown`：主力技能占比是否合理、有没有明显异常的构成
（例如某个核心技能占比过低）。**治疗职业必须重点看 `overheal_percent`（过量治疗率）**——
原始治疗量高但过量率高，等于把治疗丢在满血目标身上，有效治疗反而更低。

注意 `healing_effective` 已经是**扣过过量的有效治疗**，`healing_raw` 是有效+过量。
不要再用 `output_total` 减 `overheal`，那会重复扣一次。

## 三、技能释放与节奏
按时间顺序看 `rotation`（`t` 是战斗相对毫秒，请换算成"分:秒"来叙述），分析起手、
爆发窗口、平稳期的技能顺序。

- 序列里带 `"pet": true` 的是宠物施法，不是玩家本人的决策，要区分开。
- **引导类技能在原始施法记录里一秒一跳**（如神圣赞美诗、奥术飞弹），
  `cooldown_uses` 已经按 8 秒窗口归并过，**以它为准**，不要把一次引导说成用了很多次。
- **治疗没有固定循环**，不要硬套"循环对错"；改看技能构成、大招使用次数与时机、
  以及对团队高压段的应对。

## 四、冷却与资源
`cooldown_uses` 给出各关键技能的**真实使用次数与时间点**，据此判断有没有空转
（一场该开 N 次的技能只开了 M 次）。

资源曲线**不是每个专精都有**。payload 里出现 `arcane_charge` 时（奥术法师的奥术充能），
它给出了消耗时的层数分布和满层浪费次数，据此判断资源有没有溢出。
**没有这个字段就说明本专精没有可用的资源曲线数据**，直接说"数据不足"，
不要凭空推测资源利用情况。

## 五、可改进项
具体到技能名、时间窗口、次数。**区分两类问题**：
- 本人可控的：技能顺序、冷却空转、资源溢出、过量治疗、站位导致的死亡
- 本人不可控的：装备等级、专精特性、团队安排（例如嗜血/英勇全团共享冷却，谁开是安排不是手法）、
  被机制点名、治疗缺口

不要把不可控的因素写成个人问题。数据不足以判断时，直接说"数据不足"，不要编。
"""

# 与 `.claude/skills/wcl-rotation-compare/scripts/ask.py` 共用（那边改成 import 这里），
# 避免两份对比提示词各自演化。
COMPARISON_SYSTEM_PROMPT = """你是一名魔兽世界正式服（The War Within）战斗日志分析师，专长是技能循环与治疗手法对比。

用户会给你两名玩家的施法序列与输出数据，请对比他们的技能释放顺序并判断是否存在问题。
每个玩家的 `output_kind` 字段说明该看伤害还是治疗：`"damage"` 看 `output_total` 等伤害字段，
`"healing"` 看同理的治疗字段以及 `overheal_percent`。

分析必须按以下结构输出中文 Markdown：

## 一、对比是否公平
先根据 `fairness` 字段（以及两名玩家各自的专精、装备等级、战斗时长、是否阵亡、是否击杀）
判断这个对比能得出什么结论、不能得出什么结论。**如果不公平，要明确说出哪些结论不能下。**
例如两人专精不同，就不能比较技能循环，只能比较资源利用与输出效率。

## 二、技能释放顺序对比
按阶段切分两人的施法序列（起手 / 爆发窗口 / 平稳期 / 收尾），对比他们在每个阶段的技能顺序差异。
引用具体的技能名和时间点（用 `rotation` 里的 `t`，单位毫秒，请换算成"分:秒"来叙述）。
注意序列里带 `"pet": true` 的是宠物施法，不是玩家本人的决策，对比时要区分开。

**如果 `output_kind` 是 `"healing"`**：治疗没有固定循环，不要硬套"循环对错"。改为对比：
技能构成（谁更依赖高消耗/低效率的治疗技能）、大招与团队减伤的**使用次数与时机**
（注意引导类技能在原始施法记录里会连续出现多跳，**同一技能 8 秒内只算一次使用**，
不要把它当成「用了很多次」）、以及应对团队高压段的能力。

## 三、输出构成对比
对比两人 `ability_breakdown` 里各技能的占比、`per_second_by_active_time`、
`output_share_percent`（在各自团队中的占比，比绝对值更公平）。输出职业另看宠物伤害占比。

**治疗职业必须重点对比 `overheal_percent`（过量治疗率）**：原始治疗量高但过量率高，
等于把大量治疗丢在满血目标身上，有效治疗反而更低。要指出这是否是主要差距来源。

## 四、差异点
逐条列出 A 相对 B 的具体差异：技能优先级、冷却对齐、资源管理、空窗期处理、治疗目标选择等。
每条都要有数据支撑（引用技能名和次数/时间）。

## 五、结论
明确区分两类差异：
- **技术/手法问题**：可以通过练习改进的（技能顺序、冷却空转、过量治疗、资源溢出）
- **环境差异**：装备等级、专精、战斗时长、团队增益、是否阵亡等造成的，**不是技术问题**

不要把环境差异误判成技术问题。特别地：
- 嗜血类技能（时间扭曲/英勇/嗜血）全团共享冷却，一场只能开一次，**谁开是团队安排，不是个人手法差异**。
- 饰品特效/主动饰品取决于各人装备，装等不同时不能当作手法差异。
- 不要把数据不足的地方编造出结论；数据不够判断某一项时直接说"数据不足"。
"""


def build_single_payload(data: Mapping[str, Any], player: str) -> dict[str, Any]:
    """单人报告的输入 payload。

    `build_player_payload` 只读 `data["fight"]` / `data["tables"]`，不依赖第二个玩家，
    可以直接复用；它自己不写 `report_code`/`fight_id`，这里补上。
    """
    payload = build_player_payload(data, player)
    fight = data.get("fight") or {}
    payload["report_code"] = data.get("report_code")
    payload["fight_id"] = data.get("fight_id") or fight.get("id")
    return payload


def build_comparison_payload(
    a_data: Mapping[str, Any],
    a_player: str,
    b_data: Mapping[str, Any],
    b_player: str,
) -> dict[str, Any]:
    """双人对比的输入 payload（含 fairness 与 idle_comparison）。"""
    a = build_player_payload(a_data, a_player)
    b = build_player_payload(b_data, b_player)
    a["report_code"], a["fight_id"] = a_data.get("report_code"), a_data.get("fight_id")
    b["report_code"], b["fight_id"] = b_data.get("report_code"), b_data.get("fight_id")
    return build_comparison(a, b)


def build_roster(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """这场战斗的玩家名册，供前端勾选要分析谁。

    从 damage-done / healing 两张榜合并出来，顺便判定这个人该看伤害还是治疗
    （和 `player_compare._metric_entry` 同一个判据，取两者中较大的那个）。
    """
    fight = data.get("fight") or {}
    tables = data.get("tables") or {}
    duration_seconds = (
        max(0.0, _number(fight.get("end_time")) - _number(fight.get("start_time"))) / 1000
    )

    catalog = _build_actor_catalog(tables)
    item_levels = _item_levels_by_actor(tables)

    amounts: dict[int, dict[str, float]] = {}
    rows: dict[int, dict[str, Any]] = {}
    for view, kind in (("damage-done", "damage"), ("healing", "healing")):
        for row in _rank_rows(tables.get(view), duration_seconds, catalog, item_levels):
            actor_id = row.get("id")
            if not isinstance(actor_id, int):
                continue
            bucket = amounts.setdefault(actor_id, {"damage": 0.0, "healing": 0.0})
            bucket[kind] = max(bucket[kind], float(row.get("amount") or 0.0))
            rows.setdefault(actor_id, row)

    roster: list[dict[str, Any]] = []
    for actor_id, bucket in amounts.items():
        row = rows[actor_id]
        total = bucket["healing"] if bucket["healing"] > bucket["damage"] else bucket["damage"]
        roster.append(
            {
                "player_id": actor_id,
                "player": row.get("actor"),
                "spec": row.get("role_label") or row.get("spec") or "",
                "class": row.get("class") or "",
                "item_level": row.get("item_level"),
                "output_kind": "healing" if bucket["healing"] > bucket["damage"] else "damage",
                "output_total": round(total),
                "output_percent": row.get("percent"),
            }
        )

    roster.sort(key=lambda item: item["output_total"], reverse=True)
    return roster


def render_user_message(payload: Mapping[str, Any], kind: str) -> str:
    if kind == "single":
        player = payload.get("player")
        spec = payload.get("spec") or "未知专精"
        header = (
            f"请复盘 {player}（{spec}）在这场战斗中的表现。"
            f"这是单人复盘，没有对比对象。\n\n"
        )
    else:
        a, b = payload["a"], payload["b"]
        header = (
            f"请对比 {a['player']}（{a['spec']}）与 {b['player']}（{b['spec']}）的"
            f"技能释放顺序和输出差异。\n\n"
        )

    # 紧凑序列化：整块 payload 一次发送，缩进是纯浪费 token（和 deepseek_agent 同样的理由）
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"{header}```json\n{body}\n```"


async def run_player_report(payload: Mapping[str, Any], kind: str) -> str:
    """调用 DeepSeek 生成玩家报告正文。

    单次非流式调用 —— payload 已自包含，不需要 tool loop。
    """
    system_prompt = SINGLE_SYSTEM_PROMPT if kind == "single" else COMPARISON_SYSTEM_PROMPT
    endpoint = f"{settings.deepseek_base_url.rstrip('/')}/chat/completions"

    async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=20.0)) as client:
        response = await client.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {settings.deepseek_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.deepseek_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": render_user_message(payload, kind)},
                ],
                "stream": False,
                "thinking": {"type": "disabled"},
                "max_tokens": settings.deepseek_max_tokens,
            },
        )

    if response.status_code != 200:
        raise RuntimeError(f"DeepSeek 返回 {response.status_code}：{response.text[:1200]}")

    choices = response.json().get("choices") or []
    if not choices:
        raise RuntimeError("DeepSeek 未返回 choices")

    analysis = str((choices[0].get("message") or {}).get("content") or "")
    if not analysis:
        raise RuntimeError("DeepSeek 返回了空内容")
    return analysis

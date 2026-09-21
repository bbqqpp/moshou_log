"""两名玩家的对比数据构建。

这个模块是 `.claude/skills/wcl-rotation-compare` 和 MCP server 的共用底座 ——
两边都要「取某个玩家在这一场的输出/治疗、技能序列、环境事实」，放两份必然漂移。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Mapping

from .aggregation import (
    _build_actor_catalog,
    is_player_entry,
    _entries,
    _extras_index,
    _number,
    keystone_summary,
    phase_timeline,
    pull_summary,
)
from .wow import is_mythic_plus
from .config import settings
from .deep_analysis import (
    ability_counts,
    arcane_charge_curve,
    cast_sequence,
    cooldown_uses,
    compare_idle_windows,
)
from .errors import WCLApiError, WCLAuthError
from .wcl import WCLClient
from .wow import localize_spec_id

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "storage" / "wcl_data"

# 关注度较高的冷却/大招，用于统计真实使用次数（引导类会按 8 秒归并）
NOTABLE_COOLDOWN_HINTS = (
    "神圣化身", "神圣赞美诗", "守护之魂", "绝望祷言", "纳鲁的赐福",
    "能量灌注", "奥术涌动", "大法师之触", "奥术宝珠",
)


def load_fight(
    report_code: str,
    fight_id: int,
    *,
    data_dir: Path | None = None,
    allow_fetch: bool = True,
) -> dict[str, Any]:
    """本地有就用本地的，没有就下载。"""
    from .wcl_data_store import WCLDataStore

    store = WCLDataStore(data_dir or DEFAULT_DATA_DIR)
    data = store.load(report_code, fight_id)
    if data is not None:
        return data

    if not allow_fetch:
        raise FileNotFoundError(
            f"本地没有 {report_code} fight={fight_id} 的数据，且未允许下载"
        )

    try:
        fight, tables, _ = asyncio.run(WCLClient(settings).extract_fight(report_code, fight_id))
    except (WCLApiError, WCLAuthError) as exc:
        raise RuntimeError(f"获取 WCL 数据失败：{exc}") from exc

    store.save(report_code, fight_id, fight, tables)
    return store.load(report_code, fight_id) or {
        "report_code": report_code,
        "fight_id": fight_id,
        "fight": fight,
        "tables": tables,
    }


def resolve_player(data: Mapping[str, Any], player: str) -> tuple[int, dict[str, Any]]:
    """把玩家名解析成 actor id。匹配不到时把在场的人列出来。"""
    from .wcl_data_store import _normalize_player_ids

    catalog = _build_actor_catalog(data.get("tables") or {})
    ids = _normalize_player_ids(player, catalog)

    if not ids:
        known = sorted({str(i.get("name")) for i in catalog.values() if i.get("name")})
        raise ValueError(f"找不到玩家「{player}」。在场的有：{'、'.join(known)}")
    if len(ids) > 1:
        names = [str(catalog.get(i, {}).get("name")) for i in sorted(ids)]
        raise ValueError(f"「{player}」匹配到多个人：{'、'.join(names)}，请输入完整角色名")

    actor_id = next(iter(ids))
    return actor_id, catalog.get(actor_id, {})


def _metric_entry(tables: Mapping[str, Any], actor_id: int) -> tuple[str, dict[str, Any]]:
    """决定看哪张表：输出职业看 damage-done，治疗职业看 healing。

    治疗职业在 damage-done 里的 activeTime 会退化成毫秒级（实测见过 2ms），
    拿它当分母算「每秒输出」会得到几亿的荒谬数字。
    """
    damage = next((e for e in _entries(tables.get("damage-done")) if e.get("id") == actor_id), {})
    healing = next((e for e in _entries(tables.get("healing")) if e.get("id") == actor_id), {})
    if _number(healing.get("total")) > _number(damage.get("total")):
        return "healing", healing
    return "damage", damage


def build_player_payload(data: Mapping[str, Any], player: str) -> dict[str, Any]:
    """构建单个玩家的对比数据（输出/治疗、技能序列、冷却、资源曲线）。"""
    fight = data.get("fight") or {}
    tables = data.get("tables") or {}
    fight_start = _number(fight.get("start_time"))

    actor_id, info = resolve_player(data, player)
    kind, entry = _metric_entry(tables, actor_id)

    pet_ids = {
        pet["id"]
        for pet in (entry.get("pets") or [])
        if isinstance(pet, dict) and isinstance(pet.get("id"), int)
    }

    duration_ms = max(0.0, _number(fight.get("end_time")) - fight_start)
    active_ms = _number(entry.get("activeTime")) or duration_ms
    total = _number(entry.get("total"))
    view = "healing" if kind == "healing" else "damage-done"
    # 分母只算玩家：榜单（summarize_tables 的 damage_done/healing_done）已经筛掉了
    # BOSS/NPC/宠物，这里不筛的话 output_share_percent 会和 summary 里的 percent
    # 用两个不同的分母，同一个玩家在两个地方占比不一样。
    raid_total = sum(
        _number(e.get("total")) for e in _entries(tables.get(view)) if is_player_entry(e)
    )

    abilities = [
        {
            "name": a.get("name"),
            "total": round(_number(a.get("total"))),
            "percent": round(_number(a.get("total")) / total * 100, 1) if total else 0.0,
        }
        for a in (entry.get("abilities") or [])
        if isinstance(a, dict) and _number(a.get("total")) > 0
    ]
    abilities.sort(key=lambda row: row["total"], reverse=True)

    death = next(
        (
            _number(e.get("timestamp") or e.get("deathTime"))
            for e in _entries(tables.get("deaths"))
            if e.get("id") == actor_id
        ),
        None,
    )

    spec_id = info.get("spec_id")
    payload: dict[str, Any] = {
        "player": info.get("name") or player,
        "player_id": actor_id,
        "spec_id": spec_id,
        "spec": info.get("role_label") or localize_spec_id(spec_id)["label"],
        "output_kind": kind,
        # 大秘境和团本走两套提示词，这个标识由 run_player_report 读取
        "mythic_plus": is_mythic_plus(fight),
        "item_level": entry.get("itemLevel") or info.get("item_level"),
        "fight_name": fight.get("name"),
        "difficulty": fight.get("difficulty"),
        "size": fight.get("size"),
        "kill": fight.get("kill"),
        "fight_duration_ms": round(duration_ms),
        "active_time_ms": round(active_ms),
        "output_total": round(total),
        "output_share_percent": round(total / raid_total * 100, 1) if raid_total else 0.0,
        "per_second_by_fight_duration": round(total / (duration_ms / 1000), 1) if duration_ms else 0.0,
        "per_second_by_active_time": round(total / (active_ms / 1000), 1) if active_ms else 0.0,
        "death_at_ms": round(death - fight_start) if death else None,
        "ability_breakdown": abilities[:20],
    }

    if kind == "healing":
        overheal = _number(entry.get("overheal"))
        raw = total + overheal
        # WCL 的 healing.total 本身就是**有效治疗**（已扣过量），overheal 是独立字段。
        # 不给三个量分别命名，模型会误以为 output_total 是原始量再减一次 overheal，
        # 实测导致过「有效治疗差 56%」这种双重扣减的错误结论（实际是 12%）。
        payload["healing_effective"] = round(total)
        payload["healing_overheal"] = round(overheal)
        payload["healing_raw"] = round(raw)
        payload["overheal_percent"] = round(overheal / raw * 100, 1) if raw else 0.0
        payload["field_note"] = (
            "output_total / healing_effective = 有效治疗（已扣过量）；"
            "healing_raw = 有效 + 过量 = 原始治疗量。不要再用 output_total 减 overheal。"
        )
    else:
        pet_damage = sum(_number(p.get("total")) for p in (entry.get("pets") or []))
        payload["pet_damage"] = round(pet_damage)
        payload["pet_damage_percent"] = round(pet_damage / total * 100, 1) if total else 0.0

    casts = cast_sequence(
        tables, actor_id, fight_start_ms=fight_start, pet_ids=pet_ids, include_pets=False
    )
    payload["cast_count"] = len(casts)
    payload["rotation"] = casts
    payload["ability_counts"] = ability_counts(
        cast_sequence(tables, actor_id, fight_start_ms=fight_start, pet_ids=pet_ids),
        include_pets=False,
    )

    cooldowns = {}
    for hint in NOTABLE_COOLDOWN_HINTS:
        uses = cooldown_uses(tables, actor_id, hint, fight_start_ms=fight_start)
        if uses:
            cooldowns[hint] = {"次数": len(uses), "时间点ms": uses}
    payload["cooldown_uses"] = cooldowns

    # 资源曲线只有部分专精有（目前是奥术法师的奥术充能），没有消耗就不放这个键。
    # MCP 的 player_rotation 一直是这么给的，这里必须对齐 —— 否则提示词里提到的
    # arcane_charge 在网页端和 CLI 两条链路的 payload 里根本不存在。
    charges = arcane_charge_curve(tables, actor_id, fight_start)
    if charges["消耗次数"]:
        payload["arcane_charge"] = charges

    # --- V2 独有数据（V1 历史缓存里没有，取不到就不放这些键）---
    ranking = _extras_index(tables, "rankings").get(actor_id)
    if ranking:
        payload["parse"] = {
            "rank_percent": ranking.get("rankPercent"),
            "bracket_percent": ranking.get("bracketPercent"),
            "rank": ranking.get("rank"),
            "total_parses": ranking.get("totalParses"),
            # 同装等区间。用于把「装备差异」和「手法差异」分开
            "bracket_ilvl": ranking.get("bracketData"),
            "note": "rank_percent 是同装等区间内的百分位，不是绝对水平；"
                    "它衡量的是这一场相对同专精同装等所有记录的位置",
        }

    detail = _extras_index(tables, "player_details").get(actor_id)
    if detail:
        payload["consumables"] = {
            "potion_use": detail.get("potionUse"),
            "healthstone_use": detail.get("healthstoneUse"),
            "note": "爆发药水使用次数。少于战斗时长允许的次数通常意味着漏开",
        }

    survival = _extras_index(tables, "survivability").get(actor_id)
    if survival and survival.get("survivability") is not None:
        payload["survivability"] = {
            "score": survival.get("survivability"),
            "note": "0-1 的生存分，相对同专精计算；比承伤总量公平——"
                    "承伤高可能只是因为你是坦克",
        }

    fight = data.get("fight") or {}

    # 大秘境和团本是两套分析框架，payload 也要跟着分叉：
    # 大秘境给「拉怪分段 + 钥匙信息」，团本给「阶段划分」。
    # 注意 `parse` 在大秘境下本来就不会出现（rankings 是空的），不用特殊处理。
    if is_mythic_plus(fight):
        payload["keystone"] = keystone_summary(fight)
        pulls = pull_summary(fight, tables)
        if pulls:
            payload["pull_summary"] = pulls
    else:
        phases = phase_timeline(fight, tables)
        if phases:
            payload["phases"] = phases

    return payload


def build_fairness(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
    """把「这个对比公不公平」的客观事实先摆出来，避免把环境差异当成技术差异。"""
    notes: list[str] = []

    if a["output_kind"] != b["output_kind"]:
        notes.append("一人看治疗、一人看伤害，两者量纲不可直接比较")
    elif a["output_kind"] == "healing":
        notes.append(
            "治疗职业不适用「技能循环」概念——治疗是应对伤害的，没有固定顺序；"
            "应重点看过量治疗率、技能构成、大招时机与应对高压段的能力"
        )
        delta = abs(a.get("overheal_percent", 0) - b.get("overheal_percent", 0))
        if delta >= 5:
            notes.append(
                f"过量治疗率相差 {delta:.1f} 个百分点（{a['player']} "
                f"{a.get('overheal_percent')}% vs {b['player']} {b.get('overheal_percent')}%）"
            )

    if a["spec"] != b["spec"]:
        notes.append(
            f"两人专精不同（{a['spec']} vs {b['spec']}），技能循环没有可比性，"
            "只能对比输出效率与资源利用"
        )

    a_ilvl, b_ilvl = a.get("item_level"), b.get("item_level")
    if isinstance(a_ilvl, int) and isinstance(b_ilvl, int):
        delta = abs(a_ilvl - b_ilvl)
        if delta >= 3:
            notes.append(f"装备等级相差 {delta} 点（{a_ilvl} vs {b_ilvl}），约合 {delta * 1.5:.0f}% 输出差距")

    if a.get("fight_name") != b.get("fight_name"):
        notes.append("两人来自不同战斗，战斗时长与机制压力不同，数值不能直接相减")
    if a.get("kill") != b.get("kill"):
        notes.append("一场击杀、一场灭团，灭团场的输出会被压缩")
    for side in (a, b):
        if side.get("death_at_ms") is not None:
            notes.append(
                f"{side['player']} 在 {side['death_at_ms'] / 1000:.0f}s 阵亡，"
                "其输出与活跃时间被压低"
            )

    return {
        "same_spec": a["spec"] == b["spec"],
        "item_level_delta": (a_ilvl or 0) - (b_ilvl or 0),
        "duration_ratio": round(a["fight_duration_ms"] / b["fight_duration_ms"], 2)
        if b.get("fight_duration_ms")
        else None,
        "notes": notes,
    }


def build_comparison(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """把两边拼成完整对比数据，含空窗交叉判定。"""
    payload: dict[str, Any] = {"a": a, "b": b}
    # `run_player_report` 从**顶层**读这个标识来选提示词。不提到顶层的话
    # 双人对比会退回团本提示词（两边各自的标识埋在 a/b 里读不到）。
    # 两边不一致时取「或」：网页入口两边必然同一场；跨场对比如果一边是副本，
    # 用大秘境那套至少能正确处理拉怪分段。
    payload["mythic_plus"] = bool(a.get("mythic_plus") or b.get("mythic_plus"))
    payload["fairness"] = build_fairness(a, b)
    payload["idle_comparison"] = compare_idle_windows(
        a.get("rotation", []),
        b.get("rotation", []),
        a_name=str(a["player"]),
        b_name=str(b["player"]),
    )
    return payload


def build_comparison_from_inputs(
    a_report: str, a_fight: int, a_player: str,
    b_report: str, b_fight: int, b_player: str,
    *,
    data_dir: Path | None = None,
    allow_fetch: bool = True,
) -> dict[str, Any]:
    data_a = load_fight(a_report, a_fight, data_dir=data_dir, allow_fetch=allow_fetch)
    data_b = load_fight(b_report, b_fight, data_dir=data_dir, allow_fetch=allow_fetch)
    a = build_player_payload(data_a, a_player)
    b = build_player_payload(data_b, b_player)
    a["report_code"], a["fight_id"] = a_report, a_fight
    b["report_code"], b["fight_id"] = b_report, b_fight
    return build_comparison(a, b)


def to_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=1)

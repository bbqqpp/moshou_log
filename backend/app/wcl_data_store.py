from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable

TABLE_KEYS = (
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
    # V2 独有（见 wcl.py 的 `_fetch_extras`）。
    # ⚠️ V1 时代存下来的历史缓存里没有这几个键，查询会返回 found=False 而不是空列表
    # —— 那是「这份缓存是迁移前拉的」，不是「这场没有 parse 排名」。
    "rankings",
    "survivability",
    "player_details",
)

EVENT_TYPES = {
    "buff_debuff_events",
    "cast_events",
    "death_events",
    "interrupt_events",
    "dispel_events",
    "resource_events",
    "spawn_events",
    "combatant_info_events",
}

ACTOR_CATALOG_SOURCES = (
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
)


def _entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("entries", "events", "data", "auras", "deaths", "casts", "buffs", "debuffs"):
            value = payload.get(key)
            if not isinstance(value, list):
                continue
            items = [item for item in value if isinstance(item, dict)]
            if key == "entries" and len(items) == 1:
                nested = items[0].get("entries")
                if isinstance(nested, list):
                    return [item for item in nested if isinstance(item, dict)]
            return items
    return []


def _contains_text(value: Any, player: str) -> bool:
    """条目里是否有**字符串字段**正好等于 `player`。

    **只比字符串**。以前是 `str(value) == player`，于是数字字段也会命中：
    实测 `player="100"` 返回 9485 条（真值 581），其中 8904 条是
    `classResources[0].max == 100`（怒气/能量的默认上限）这类与玩家无关的字段。
    调用方拿到的 `matched_count` 看起来很权威，会把别人的施法当成这个人的。
    """
    if isinstance(value, dict):
        return any(_contains_text(item, player) for item in value.values())
    if isinstance(value, list):
        return any(_contains_text(item, player) for item in value)
    if not isinstance(value, str):
        return False
    return value == player


def _time_value(entry: dict[str, Any]) -> float | None:
    for key in ("timestamp", "deathTime", "time", "lastTime"):
        value = entry.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def _normalize_boundary(value: float | None, fight_start: float) -> float | None:
    if value is None:
        return None
    timestamp = float(value)
    if fight_start > 0 and timestamp < fight_start:
        return fight_start + timestamp
    return timestamp


def _visit_actor_catalog(value: Any, catalog: dict[int, dict[str, Any]]) -> None:
    if isinstance(value, list):
        for item in value:
            _visit_actor_catalog(item, catalog)
        return
    if not isinstance(value, dict):
        return

    actor_id = value.get("id")
    name = value.get("name")
    if isinstance(actor_id, int) and isinstance(name, str) and name:
        existing = catalog.get(actor_id)
        if existing is None:
            catalog[actor_id] = {
                "name": name,
                "type": value.get("type") or value.get("icon") or "",
            }

    target = value.get("target")
    if isinstance(target, dict):
        _visit_actor_catalog(target, catalog)

    for key, item in value.items():
        # `gear` / `talents` 里也带 `{id, name}`，但那是**装备和天赋**不是
        # actor —— 不跳过的话它们会被当成"在场的人"。实测一场团本的目录因此
        # 从 23 人膨胀到 270 个条目，里面混着「伤员外衣」「充能沙石指环」。
        if key in {
            "ability", "abilities", "targets", "sources", "damageAbilities",
            "healing", "damage", "gear", "talents",
        }:
            continue
        if isinstance(item, (dict, list)):
            _visit_actor_catalog(item, catalog)


def _build_actor_catalog(data: dict[str, Any]) -> dict[int, dict[str, Any]]:
    catalog: dict[int, dict[str, Any]] = {}
    tables = data.get("tables") or {}
    for table_name in ACTOR_CATALOG_SOURCES:
        _visit_actor_catalog(tables.get(table_name), catalog)
    return catalog


def _event_actor_ids(entry: dict[str, Any]) -> set[int]:
    ids: set[int] = set()
    for key in ("sourceID", "targetID", "sourceInstance", "targetInstance"):
        value = entry.get(key)
        if isinstance(value, int):
            ids.add(value)
    return ids


def _normalize_player_ids(player: str, catalog: dict[int, dict[str, Any]]) -> set[int]:
    stripped = player.strip()
    numeric_ids = {int(part) for part in stripped.split(",") if part.isdigit()}
    if numeric_ids:
        # 数字输入按 actor id 解释，**且必须是这一场真实存在的 actor**。
        # 不在目录里就返回空集（由调用方报「找不到该玩家」），不要退回到
        # 值相等匹配 —— 那正是把 `classResources.max` 当成玩家 id 的来源。
        return numeric_ids & set(catalog)

    lowered = stripped.casefold()
    exact_ids = {
        actor_id
        for actor_id, info in catalog.items()
        if str(info.get("name", "")).casefold() == lowered
    }
    if exact_ids:
        return exact_ids

    return {
        actor_id
        for actor_id, info in catalog.items()
        if lowered and lowered in str(info.get("name", "")).casefold()
    }


class WCLDataStore:
    """Stores complete WCL table data for one report/fight on disk."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)

    def _file(self, report_code: str, fight_id: int) -> Path:
        safe_report = "".join(
            char for char in report_code if char.isalnum() or char in "_-"
        )
        return self.base_dir / f"{safe_report}__{int(fight_id)}.json"

    def save(
        self,
        report_code: str,
        fight_id: int,
        fight: dict[str, Any],
        tables: dict[str, Any],
    ) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "report_code": report_code,
            "fight_id": int(fight_id),
            "saved_at": time.time(),
            "fight": fight,
            "tables": tables,
        }
        tmp = self._file(report_code, fight_id).with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._file(report_code, fight_id))

    def load(
        self,
        report_code: str,
        fight_id: int,
    ) -> dict[str, Any] | None:
        path = self._file(report_code, fight_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def query(
        self,
        report_code: str,
        fight_id: int,
        data_type: str,
        player: str | None = None,
        start_time: float | None = None,
        end_time: float | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        data = self.load(report_code, fight_id)
        if data is None:
            return {"found": False, "message": "本地 WCL 数据不存在，请先重新获取该战斗数据"}

        fight = data.get("fight") or {}
        fight_start = float(fight.get("start_time") or 0)
        fight_end = float(fight.get("end_time") or 0)

        if data_type == "fight":
            return {
                "found": True,
                "data_type": "fight",
                "fight": fight,
                "fight_start_time": fight_start,
                "fight_end_time": fight_end,
                "available_data_types": list(TABLE_KEYS),
            }

        tables = data.get("tables") or {}
        if data_type not in TABLE_KEYS:
            return {
                "found": False,
                "message": f"不支持的数据类型：{data_type}",
                "available_data_types": list(TABLE_KEYS),
            }

        # 键**完全不在文件里**与「键在但内容为空」是两回事，不能都返回 0 条了事。
        #
        # `rankings` / `survivability` / `player_details` 是迁移到 V2 之后才有的，
        # V1 时代拉下来的历史缓存里没有这三个键。若静默返回 0 条，模型会得出
        # 「这场没有 parse 排名」的结论，而事实是「这份缓存太旧，重新拉一次就有」。
        if data_type not in tables:
            return {
                "found": False,
                "data_type": data_type,
                "message": (
                    f"这份本地缓存里没有 {data_type} —— 它可能是迁移到 WCL V2 之前"
                    "拉下来的旧数据。重新分析一次这场战斗就会有。"
                    "（注意：这与「这场确实没有这项数据」不是一回事）"
                ),
                "available_data_types": sorted(tables),
            }

        payload = tables.get(data_type)
        # 键在但值是错误信封（取数失败）——同样不能返回 0 条了事。
        # `aggregation.table_errors` 认这个信封，这里也必须认。
        if isinstance(payload, dict) and payload.get("available") is False:
            return {
                "found": False,
                "data_type": data_type,
                "message": (
                    f"{data_type} 这一项当时没有取到："
                    f"{payload.get('error') or '未知错误'}。重新分析一次这场战斗可以重试。"
                ),
            }

        entries = _entries(payload)
        catalog = _build_actor_catalog(data)

        if player:
            raw_player = str(player).strip()
            # 纯数字的输入是 actor id，不是名字 —— 不要对它做文本兜底匹配
            numeric_input = bool(raw_player) and all(
                part.isdigit() for part in raw_player.split(",") if part.strip()
            )
            actor_ids = _normalize_player_ids(raw_player, catalog)
            player_filtered: list[dict[str, Any]] = []
            for entry in entries:
                if actor_ids and actor_ids.intersection(_event_actor_ids(entry)):
                    player_filtered.append(entry)
                elif not numeric_input and _contains_text(entry, raw_player):
                    player_filtered.append(entry)
            entries = player_filtered

            # 找不到人时**必须说清楚**，不能返回一个空的 0 条。
            # 那样模型会把它读成「这个人全程没有施法/没有输出」——
            # 一个纯粹由查询失败编出来的结论。
            if not entries:
                known = sorted(
                    str(info.get("name"))
                    for info in catalog.values()
                    if info.get("name")
                )
                preview = "、".join(known[:15])
                if len(known) > 15:
                    preview += f" 等 {len(known)} 个"
                return {
                    "found": False,
                    "data_type": data_type,
                    "message": (
                        f"没有匹配到玩家「{raw_player}」。"
                        f"在场的是：{preview or '（无）'}。"
                        "名字要写游戏内原名；也可以直接给 actor id。"
                    ),
                    "available_players": known,
                }

        start_time = _normalize_boundary(start_time, fight_start)
        end_time = _normalize_boundary(end_time, fight_start)
        time_window_requested = start_time is not None or end_time is not None
        time_filter_effective = False
        if time_window_requested:
            filtered: list[dict[str, Any]] = []
            for entry in entries:
                timestamp = _time_value(entry)
                if timestamp is None:
                    # 没有时间戳的条目一律保留（它们本来就不属于某个时刻）
                    filtered.append(entry)
                    continue
                time_filter_effective = True
                if start_time is not None and timestamp < float(start_time):
                    continue
                if end_time is not None and timestamp > float(end_time):
                    continue
                filtered.append(entry)
            entries = filtered

        safe_limit = min(max(int(limit), 1), 1000)
        returned = entries[:safe_limit]

        result: dict[str, Any] = {
            "found": True,
            "data_type": data_type,
            "matched_count": len(entries),
            "returned_count": len(returned),
            "items": returned,
            "fight_start_time": fight_start,
            "fight_end_time": fight_end,
            "time_unit": "milliseconds",
            "time_note": "WCL timestamp 以 report 起点为 0；输入小于 fight_start_time 的时间会被当作战斗相对时间并自动转换。",
        }

        # 时间窗在**没有时间戳字段的表**上无法生效（`damage-done` / `healing` 这类
        # 聚合表就是）。原来静默返回整场数据，调用方会把它当成「那 10 秒的数据」——
        # 实测对 damage-done 传时间窗返回全部 23 行、`matched_count: 23`，
        # 返回体里没有任何提示。
        if time_window_requested and not time_filter_effective:
            result["time_filter_ignored"] = True
            result["message"] = (
                f"{data_type} 表没有时间戳字段，时间窗过滤对它无效 —— "
                "返回的是整场数据，不要当成某个时间段的数据来解读。"
                "需要按时间切片请查事件流（*_events）。"
            )

        if data_type in EVENT_TYPES:
            event_actor_ids: set[int] = set()
            for entry in returned:
                event_actor_ids.update(_event_actor_ids(entry))
            actors = {
                actor_id: catalog[actor_id]
                for actor_id in event_actor_ids
                if actor_id in catalog
            }
            result["actors"] = actors

        return result

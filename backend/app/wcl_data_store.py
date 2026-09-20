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
    if isinstance(value, dict):
        return any(_contains_text(item, player) for item in value.values())
    if isinstance(value, list):
        return any(_contains_text(item, player) for item in value)
    return str(value) == player


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
        if key in {"ability", "abilities", "targets", "sources", "damageAbilities", "healing", "damage"}:
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
        return numeric_ids

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

        entries = _entries(tables.get(data_type))
        catalog = _build_actor_catalog(data)

        if player:
            actor_ids = _normalize_player_ids(str(player), catalog)
            player_filtered: list[dict[str, Any]] = []
            for entry in entries:
                if actor_ids and actor_ids.intersection(_event_actor_ids(entry)):
                    player_filtered.append(entry)
                elif _contains_text(entry, str(player)):
                    player_filtered.append(entry)
            entries = player_filtered

        start_time = _normalize_boundary(start_time, fight_start)
        end_time = _normalize_boundary(end_time, fight_start)
        if start_time is not None or end_time is not None:
            filtered: list[dict[str, Any]] = []
            for entry in entries:
                timestamp = _time_value(entry)
                if timestamp is None:
                    filtered.append(entry)
                    continue
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

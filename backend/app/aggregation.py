from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Mapping

from .wow import localize_role, localize_spec_id

RANK_VIEWS = {
    "damage-done": "damage_done",
    "healing": "healing_done",
    "damage-taken": "damage_taken",
}

ERROR_KEYS = {"available", "error"}

CORROSIVE_WAVE_ABILITIES = {"腐蚀浪潮"}
CORROSIVE_WAVE_GUIDS = {1292403}
EGG_CARRIER_ABILITIES = {"厄鳞外壳"}
EGG_CARRIER_GUIDS = {1300312}
HATCH_CAST_ABILITIES = {"孵化厄运"}
GESTATION_CAST_ABILITIES = {"蠕动孕育"}
DEADLY_AURA_RULES = {
    "腐臭薄膜": {1301268},
    "剧毒撕咬": {1287036},
    "酸液爆发": {1301800},
}
AURA_APPLY_TYPES = {"applydebuff", "refreshdebuff", "applydebuffstack"}
AURA_REMOVE_TYPES = {"removedebuff", "removedebuffstack"}

# 「关键减益」= 致命光环 + 背蛋机制，用于判断某人在死亡瞬间是否正带着机制减益。
KEY_DEBUFF_ABILITIES = set(DEADLY_AURA_RULES) | EGG_CARRIER_ABILITIES
KEY_DEBUFF_GUIDS = set().union(*DEADLY_AURA_RULES.values()) | EGG_CARRIER_GUIDS

# 玩家死亡时游戏会统一剥离其身上的光环，所以「死时带着减益」在原始数据里必然
# 表现为减益在死亡前几毫秒被移除（实测集中在 1~51ms）。用严格的时间区间判断会
# 漏掉几乎所有真实案例，这里给一个容差把这些移除算作「死亡时仍在生效」。
DEATH_AURA_STRIP_TOLERANCE_MS = 500


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("entries", "events", "data", "auras", "deaths", "casts", "buffs", "debuffs"):
            value = payload.get(key)
            if isinstance(value, list):
                items = [item for item in value if isinstance(item, dict)]
                if key == "entries" and len(items) == 1:
                    nested = items[0].get("entries")
                    if isinstance(nested, list):
                        return [item for item in nested if isinstance(item, dict)]
                return items
    return []


def _entry_name(entry: Mapping[str, Any]) -> str:
    for key in (
        "name",
        "sourceName",
        "source",
        "targetName",
        "target",
        "character",
        "player",
        "actor",
        "unit",
        "abilityName",
        "spell",
        "aura",
    ):
        value = entry.get(key)
        if value not in (None, ""):
            return str(value)
    return "Unknown"


def _entry_source(entry: Mapping[str, Any]) -> str:
    return str(
        entry.get("sourceName")
        or entry.get("source")
        or entry.get("actor")
        or entry.get("player")
        or "Unknown"
    )


def _role_source(entry: Mapping[str, Any]) -> str:
    for key in ("type", "guidType", "class", "spec", "role"):
        value = entry.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _entry_ability(entry: Mapping[str, Any]) -> str:
    ability = entry.get("ability") or {}
    if isinstance(ability, dict):
        ability = ability.get("name") or ""
    return str(
        ability
        or entry.get("abilityName")
        or entry.get("spellName")
        or entry.get("guidType")
        or entry.get("type")
        or ""
    )


def _role_for_entry(
    entry: Mapping[str, Any],
    catalog: Mapping[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Prefer the catalog's role (which has the authoritative spec id) when we have it."""
    actor_id = entry.get("id")
    if catalog is not None and isinstance(actor_id, int):
        info = catalog.get(actor_id)
        if info and info.get("role_label"):
            return {
                "class": info.get("class", ""),
                "spec": info.get("spec", ""),
                "label": info["role_label"],
            }
    return localize_role(_role_source(entry))


def _item_levels_by_actor(tables: Mapping[str, Any]) -> dict[int, int]:
    """每个友方玩家的平均装备等级。

    取自施法事件里的 ``itemLevel``（客户端自己上报的平均装等），而不是
    combatant_info 的 gear 列表平均 —— 后者会混进不该计入的槽位，实测同一名
    玩家能差出 20 点以上。取出现次数最多的那个值。
    """
    levels: dict[int, Counter[int]] = defaultdict(Counter)
    for event in _entries(tables.get("cast_events")):
        if event.get("sourceIsFriendly") is not True:
            continue
        actor_id = event.get("sourceID")
        if not isinstance(actor_id, int):
            continue
        try:
            level = int(float(event.get("itemLevel")))
        except (TypeError, ValueError):
            continue
        if level > 0:
            levels[actor_id][level] += 1
    return {actor_id: counts.most_common(1)[0][0] for actor_id, counts in levels.items()}


def _rank_rows(
    payload: Any,
    duration_seconds: float = 0,
    catalog: Mapping[int, dict[str, Any]] | None = None,
    item_levels: Mapping[int, int] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in _entries(payload):
        if entry.get("available") is False:
            continue
        name = _entry_name(entry)
        amount = _number(entry.get("total"))
        if amount == 0:
            for key in ("amount", "damage", "healing", "damageTaken", "totalDamage", "totalHealing"):
                amount = _number(entry.get(key))
                if amount:
                    break
        if amount <= 0:
            continue

        raw = dict(entry)
        role = _role_for_entry(entry, catalog)
        per_second = round(amount / duration_seconds, 2) if duration_seconds > 0 else 0.0
        rows.append(
            {
                "actor": name,
                "ability": _entry_ability(entry) or str(raw.get("type") or ""),
                "amount": round(amount, 2),
                "per_second": per_second,
                "id": entry.get("id"),
                "guid": entry.get("guid"),
                "type": entry.get("type"),
                "class": role["class"],
                "spec": role["spec"],
                "role_label": role["label"],
                "raw": raw,
            }
        )

    # 占团队总量的百分比：夸人时要看的是「装等 vs 贡献占比」，不是绝对值
    total = sum(row["amount"] for row in rows)
    for row in rows:
        row["percent"] = round(row["amount"] / total * 100, 1) if total > 0 else 0.0
        if item_levels:
            row["item_level"] = item_levels.get(row["id"])

    rows.sort(key=lambda row: row["amount"], reverse=True)
    return rows


def _death_rows(
    payload: Any,
    fight_start_ms: float = 0,
    catalog: Mapping[int, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in _entries(payload):
        if entry.get("available") is False:
            continue
        raw = dict(entry)
        overkill = _number(entry.get("overkill"))
        killing_blow = entry.get("killingBlow") or {}
        if not isinstance(killing_blow, dict):
            killing_blow = {}

        source = (
            killing_blow.get("name")
            or entry.get("killerName")
            or entry.get("sourceName")
            or entry.get("source")
            or entry.get("actor")
            or "Unknown"
        )

        killing_ability = killing_blow.get("ability")
        if isinstance(killing_ability, dict):
            killing_ability = killing_ability.get("name") or ""
        else:
            killing_ability = killing_blow.get("abilityName") or killing_ability

        ability = (
            killing_ability
            or entry.get("killerAbilityName")
            or entry.get("killingAbility")
            or _entry_ability(entry)
        )
        role = _role_for_entry(entry, catalog)
        raw_timestamp = entry.get("timestamp")
        if raw_timestamp is None:
            raw_timestamp = entry.get("deathTime")
        if raw_timestamp is None:
            raw_timestamp = entry.get("time")

        elapsed_ms = max(0.0, _number(raw_timestamp) - _number(fight_start_ms))

        rows.append(
            {
                "timestamp": elapsed_ms,
                "player": _entry_name(entry),
                "source": str(source),
                "ability": str(ability or ""),
                "class": role["class"],
                "spec": role["spec"],
                "role_label": role["label"] or str(ability or ""),
                "overkill": round(overkill, 2),
                "raw": raw,
            }
        )
    return rows


def _behavior_rows(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in _entries(payload):
        if entry.get("available") is False:
            continue
        raw = dict(entry)
        rows.append(
            {
                "source": _entry_source(entry),
                "target": _entry_name(entry),
                "ability": _entry_ability(entry),
                "type": entry.get("type"),
                "total": entry.get("total"),
                "totalTime": entry.get("totalTime"),
                "lastTime": entry.get("lastTime"),
                "timestamp": entry.get("timestamp") or entry.get("time"),
                "raw": raw,
            }
        )
    return rows[:500]


def _timeline(
    death_rows: list[dict[str, Any]],
    cast_rows: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    for death in death_rows:
        items.append(
            {
                "timestamp": death.get("timestamp"),
                "type": "death",
                "actor": death.get("source"),
                "target": death.get("player"),
                "ability": death.get("ability"),
                "detail": f"{death.get('player')} 死亡，击杀者：{death.get('source') or 'Unknown'}",
            }
        )

    for cast in cast_rows:
        items.append(
            {
                "timestamp": cast.get("timestamp") or cast.get("lastTime") or cast.get("raw", {}).get("timestamp"),
                "type": "cast",
                "actor": cast.get("source"),
                "target": cast.get("target") or "",
                "ability": cast.get("ability"),
                "detail": f"{cast.get('source')} 施放 {cast.get('ability')}",
            }
        )

    items.sort(key=lambda item: item.get("timestamp") or 0)
    return items[:limit]


def _total_entries(tables: Mapping[str, Any]) -> int:
    total = 0
    for payload in tables.values():
        total += len(_entries(payload))
    return total


def _event_ability_name(event: Mapping[str, Any]) -> str:
    ability = event.get("ability") or {}
    if isinstance(ability, dict):
        ability = ability.get("name") or ""
    if ability:
        return str(ability)
    extra = event.get("extraAbility") or {}
    if isinstance(extra, dict):
        return str(extra.get("name") or "")
    return ""


def _event_ability_guid(event: Mapping[str, Any]) -> int | None:
    ability = event.get("ability") or {}
    if isinstance(ability, dict):
        value = ability.get("guid")
        if isinstance(value, int):
            return value
    return None


def _ability_matches(
    event: Mapping[str, Any],
    names: set[str],
    guids: set[int],
) -> bool:
    return _event_ability_name(event) in names or _event_ability_guid(event) in guids


def _event_source_label(event: Mapping[str, Any], catalog: Mapping[int, dict[str, Any]]) -> str:
    source = event.get("source")
    if isinstance(source, dict):
        name = source.get("name")
        if name:
            return str(name)
        source_id = source.get("id")
        if isinstance(source_id, int) and source_id in catalog:
            return str(catalog[source_id].get("name") or source_id)
    source_id = event.get("sourceID")
    if isinstance(source_id, int):
        return str(catalog.get(source_id, {}).get("name") or f"单位{source_id}")
    return "未知"


def _event_target_label(event: Mapping[str, Any], catalog: Mapping[int, dict[str, Any]]) -> str:
    target = event.get("target")
    if isinstance(target, dict):
        name = target.get("name")
        if name:
            return str(name)
        target_id = target.get("id")
        if isinstance(target_id, int) and target_id in catalog:
            return str(catalog[target_id].get("name") or target_id)
    target_id = event.get("targetID")
    if isinstance(target_id, int):
        return str(catalog.get(target_id, {}).get("name") or f"单位{target_id}")
    return "未知"


def _spec_ids_by_actor(tables: Mapping[str, Any]) -> dict[int, Any]:
    """Collect the spec ids clients reported in ``combatant_info`` events."""
    specs: dict[int, Any] = {}
    for event in _entries(tables.get("combatant_info_events")):
        actor_id = event.get("sourceID")
        spec_id = event.get("specID")
        if isinstance(actor_id, int) and spec_id is not None:
            specs[actor_id] = spec_id
    return specs


def _build_actor_catalog(tables: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    catalog: dict[int, dict[str, Any]] = {}
    for view in ("damage-done", "healing", "damage-taken", "deaths", "summons", "casts"):
        for entry in _entries(tables.get(view)):
            actor_id = entry.get("id")
            name = entry.get("name")
            if not isinstance(actor_id, int) or not isinstance(name, str) or not name:
                continue
            raw_type = entry.get("type") or entry.get("icon") or ""
            role = localize_role(raw_type)
            catalog[actor_id] = {
                "name": name,
                "type": raw_type,
                "class": role["class"],
                "spec": role["spec"],
                "role_label": role["label"],
            }

    # combatant_info 带的是客户端上报的专精 ID，比上面解析 type 字符串可靠得多
    # （那个字段其实是职业名，专精永远判不出来），有就覆盖。
    for actor_id, spec_id in _spec_ids_by_actor(tables).items():
        info = catalog.get(actor_id)
        if info is None:
            continue
        role = localize_spec_id(spec_id)
        if not role["label"]:
            continue
        info["spec_id"] = spec_id
        info["class"] = role["class"]
        info["spec"] = role["spec"]
        info["role_label"] = role["label"]

    return catalog


def _player_event_summaries(
    tables: Mapping[str, Any],
    ability_limit: int = 12,
) -> list[dict[str, Any]]:
    catalog = _build_actor_catalog(tables)
    cast_totals: Counter[int] = Counter()
    aura_totals: Counter[int] = Counter()
    cast_abilities: dict[int, Counter[str]] = defaultdict(Counter)
    aura_abilities: dict[int, Counter[str]] = defaultdict(Counter)

    for event in _entries(tables.get("cast_events")):
        source_id = event.get("sourceID")
        if not isinstance(source_id, int) or source_id not in catalog:
            continue
        cast_totals[source_id] += 1
        ability = _event_ability_name(event)
        if ability:
            cast_abilities[source_id][ability] += 1

    for event in _entries(tables.get("buff_debuff_events")):
        target_id = event.get("targetID")
        source_id = event.get("sourceID")
        actor_id = target_id if event.get("targetIsFriendly") is True else source_id
        if not isinstance(actor_id, int) or actor_id not in catalog:
            continue
        aura_totals[actor_id] += 1
        ability = _event_ability_name(event)
        if ability:
            aura_abilities[actor_id][ability] += 1

    rows: list[dict[str, Any]] = []
    for actor_id, info in catalog.items():
        cast_count = cast_totals.get(actor_id, 0)
        aura_count = aura_totals.get(actor_id, 0)
        if cast_count == 0 and aura_count == 0:
            continue

        top_casts = cast_abilities[actor_id].most_common(ability_limit)
        top_auras = aura_abilities[actor_id].most_common(ability_limit)
        rows.append(
            {
                "id": actor_id,
                "name": info["name"],
                "class": info.get("class", ""),
                "spec": info.get("spec", ""),
                "role_label": info.get("role_label", ""),
                "total_cast_events": cast_count,
                "total_aura_events": aura_count,
                "top_cast_abilities": [
                    {"ability": ability, "count": count} for ability, count in top_casts
                ],
                "top_auras": [
                    {"ability": ability, "count": count} for ability, count in top_auras
                ],
            }
        )

    rows.sort(key=lambda row: (row["total_cast_events"], row["total_aura_events"]), reverse=True)
    return rows


def _elapsed(value: Any, fight_start_ms: float) -> int:
    return max(0, round(_number(value) - fight_start_ms))


def _aura_table_summary(
    tables: Mapping[str, Any],
    fight_start_ms: float,
    buff_limit: int = 120,
    debuff_limit: int = 80,
) -> dict[str, list[dict[str, Any]]]:
    summaries: dict[str, list[dict[str, Any]]] = {}
    for view, key, limit in (
        ("buffs", "buffs", buff_limit),
        ("debuffs", "debuffs", debuff_limit),
    ):
        rows: list[dict[str, Any]] = []
        for aura in _entries(tables.get(view)):
            bands = aura.get("bands")
            if not isinstance(bands, list):
                bands = []

            first_seen: int | None = None
            last_seen: int | None = None
            for band in bands:
                if not isinstance(band, dict):
                    continue
                start = _number(band.get("startTime") or band.get("start"))
                end = _number(band.get("endTime") or band.get("end"))
                if start > 0:
                    first_seen = _elapsed(start, fight_start_ms)
                if end > 0:
                    last_seen = _elapsed(end, fight_start_ms)

            rows.append(
                {
                    "ability": _entry_name(aura),
                    "guid": aura.get("guid"),
                    "type": aura.get("type"),
                    "total_uptime_ms": _number(aura.get("totalUptime")),
                    "total_uses": _number(aura.get("totalUses")),
                    "first_seen_ms": first_seen,
                    "last_seen_ms": last_seen,
                    "band_count": len(bands),
                }
            )

        rows.sort(key=lambda row: row["total_uptime_ms"], reverse=True)
        summaries[key] = rows[:limit]
    return summaries


def _player_aura_coverage(
    tables: Mapping[str, Any],
    fight_start_ms: float,
    fight_end_ms: float,
    per_player_limit: int = 8,
) -> list[dict[str, Any]]:
    catalog = _build_actor_catalog(tables)
    events = sorted(
        _entries(tables.get("buff_debuff_events")),
        key=lambda event: _number(event.get("timestamp")),
    )
    active: dict[tuple[int, str, str], dict[str, Any]] = {}
    stats: dict[tuple[int, str, str], dict[str, Any]] = {}

    def close_interval(key: tuple[int, str, str], end_time: float) -> None:
        interval = active.pop(key, None)
        if interval is None:
            return
        duration = max(0, end_time - interval["start"])
        row = stats.setdefault(
            key,
            {
                "active_ms": 0,
                "max_stacks": 0,
                "first_seen_ms": None,
                "last_seen_ms": None,
                "kind": interval["kind"],
            },
        )
        row["active_ms"] += duration
        row["max_stacks"] = max(row["max_stacks"], interval["stacks"])
        row["first_seen_ms"] = (
            interval["start"] if row["first_seen_ms"] is None else min(row["first_seen_ms"], interval["start"])
        )
        row["last_seen_ms"] = (
            interval["last"] if row["last_seen_ms"] is None else max(row["last_seen_ms"], interval["last"])
        )

    for event in events:
        actor_id = event.get("targetID")
        if event.get("targetIsFriendly") is not True or not isinstance(actor_id, int) or actor_id not in catalog:
            continue
        ability = _event_ability_name(event)
        if not ability:
            continue
        timestamp = _number(event.get("timestamp"))
        if timestamp <= 0:
            continue
        event_type = str(event.get("type") or "")
        kind = "debuff" if "debuff" in event_type else "buff"
        key = (actor_id, ability, kind)
        interval = active.get(key)

        if event_type in {"applybuff", "refreshbuff", "applydebuff", "refreshdebuff"}:
            if interval is None or interval["stacks"] <= 0:
                active[key] = {"start": timestamp, "stacks": 1, "last": timestamp, "kind": kind}
            else:
                interval["stacks"] = max(1, interval["stacks"])
                interval["last"] = timestamp
        elif event_type in {"removebuff", "removedebuff"}:
            if interval is not None:
                interval["last"] = timestamp
                close_interval(key, timestamp)
        elif event_type in {"applybuffstack", "applydebuffstack"}:
            if interval is None or interval["stacks"] <= 0:
                active[key] = {"start": timestamp, "stacks": 1, "last": timestamp, "kind": kind}
            else:
                interval["stacks"] += 1
                interval["last"] = timestamp
        elif event_type in {"removebuffstack", "removedebuffstack"}:
            if interval is None:
                continue
            interval["stacks"] -= 1
            interval["last"] = timestamp
            if interval["stacks"] <= 0:
                close_interval(key, timestamp)

    for key in list(active.keys()):
        close_interval(key, fight_end_ms)

    grouped: dict[int, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {"buff": [], "debuff": []})
    for (actor_id, ability, kind), row in stats.items():
        if row["active_ms"] <= 0:
            continue
        grouped[actor_id][kind].append(
            {
                "ability": ability,
                "active_ms": row["active_ms"],
                "active_ratio": round(row["active_ms"] / max(1, fight_end_ms - fight_start_ms), 4),
                "max_stacks": row["max_stacks"],
                "first_seen_ms": _elapsed(row["first_seen_ms"], fight_start_ms),
                "last_seen_ms": _elapsed(row["last_seen_ms"], fight_start_ms),
            }
        )

    players: list[dict[str, Any]] = []
    for actor_id, kinds in grouped.items():
        info = catalog[actor_id]
        buff_coverage = sorted(kinds.get("buff", []), key=lambda row: row["active_ms"], reverse=True)[:per_player_limit]
        debuff_coverage = sorted(kinds.get("debuff", []), key=lambda row: row["active_ms"], reverse=True)[:per_player_limit]
        players.append(
            {
                "id": actor_id,
                "name": info["name"],
                "class": info.get("class", ""),
                "spec": info.get("spec", ""),
                "buffs": buff_coverage,
                "debuffs": debuff_coverage,
            }
        )
    players.sort(
        key=lambda row: sum(item["active_ms"] for item in row["buffs"])
        + sum(item["active_ms"] for item in row["debuffs"]),
        reverse=True,
    )
    return players


def _resource_summary(tables: Mapping[str, Any], per_player_limit: int = 20) -> list[dict[str, Any]]:
    catalog = _build_actor_catalog(tables)
    stats: dict[int, dict[str, Any]] = {}
    ability_stats: dict[int, dict[str, dict[str, Any]]] = defaultdict(
        lambda: defaultdict(lambda: {"count": 0, "waste": 0.0})
    )

    for event in _entries(tables.get("resource_events")):
        source_id = event.get("sourceID")
        if not isinstance(source_id, int) or source_id not in catalog:
            continue
        waste = _number(event.get("waste"))
        ability = _event_ability_name(event)
        player = stats.setdefault(source_id, {"changes": 0, "waste": 0.0})
        player["changes"] += 1
        player["waste"] += waste
        if ability:
            ability_stats[source_id][ability]["count"] += 1
            ability_stats[source_id][ability]["waste"] += waste

    rows: list[dict[str, Any]] = []
    for source_id, player in stats.items():
        info = catalog[source_id]
        top_abilities = sorted(
            ability_stats[source_id].items(),
            key=lambda item: (item[1]["waste"], item[1]["count"]),
            reverse=True,
        )[:12]
        rows.append(
            {
                "id": source_id,
                "name": info["name"],
                "class": info.get("class", ""),
                "spec": info.get("spec", ""),
                "total_changes": player["changes"],
                "total_waste": round(player["waste"], 2),
                "top_resource_abilities": [
                    {"ability": ability, "count": value["count"], "waste": round(value["waste"], 2)}
                    for ability, value in top_abilities
                ],
            }
        )

    rows.sort(key=lambda row: row["total_waste"], reverse=True)
    return rows[:per_player_limit]


def _aura_intervals(
    tables: Mapping[str, Any],
    ability_names: set[str],
    ability_guids: set[int],
) -> list[dict[str, Any]]:
    """Reconstruct an actor's active-debuff windows from aura events."""
    intervals: list[dict[str, Any]] = []
    active: dict[tuple[int, str], dict[str, Any]] = {}
    events = sorted(
        _entries(tables.get("buff_debuff_events")),
        key=lambda event: _number(event.get("timestamp")),
    )

    for event in events:
        if event.get("targetIsFriendly") is not True:
            continue
        target_id = event.get("targetID")
        if not isinstance(target_id, int):
            continue
        if not _ability_matches(event, ability_names, ability_guids):
            continue

        timestamp = _number(event.get("timestamp"))
        if timestamp <= 0:
            continue

        ability_name = _event_ability_name(event)
        if not ability_name:
            # 事件缺技能名时按 GUID 区分；回退到 ability_names 会把所有无名事件
            # 混进同一个窗口（那是个 set，字符串化后人人相同）。
            guid = _event_ability_guid(event)
            ability_name = f"guid:{guid}" if guid is not None else ""
        key = (target_id, ability_name)
        event_type = str(event.get("type") or "")

        if event_type in AURA_APPLY_TYPES:
            interval = active.get(key)
            if interval is None:
                active[key] = {
                    "target_id": target_id,
                    "ability": str(ability_name),
                    "start": timestamp,
                    "last": timestamp,
                    "stacks": 1,
                }
            else:
                interval["last"] = timestamp
                if event_type == "applydebuffstack":
                    interval["stacks"] = interval.get("stacks", 0) + 1
                else:
                    interval["stacks"] = max(1, interval.get("stacks", 1))
        elif event_type in AURA_REMOVE_TYPES:
            interval = active.get(key)
            if interval is None:
                continue
            if event_type == "removedebuffstack":
                interval["stacks"] = max(0, interval.get("stacks", 1) - 1)
                if interval["stacks"] > 0:
                    interval["last"] = timestamp
                    continue
            intervals.append(
                {
                    "target_id": interval["target_id"],
                    "ability": interval["ability"],
                    "start": interval["start"],
                    "end": timestamp,
                }
            )
            active.pop(key, None)

    for interval in active.values():
        intervals.append(
            {
                "target_id": interval["target_id"],
                "ability": interval["ability"],
                "start": interval["start"],
                "end": None,
            }
        )

    intervals.sort(key=lambda interval: interval["start"])
    return intervals


def _mechanic_cast_timeline(
    tables: Mapping[str, Any],
    fight_start_ms: float,
    ability_names: set[str],
) -> list[dict[str, Any]]:
    catalog = _build_actor_catalog(tables)
    rows: list[dict[str, Any]] = []
    for event in sorted(
        _entries(tables.get("cast_events")),
        key=lambda event: _number(event.get("timestamp")),
    ):
        ability_name = _event_ability_name(event)
        if ability_name not in ability_names:
            continue
        timestamp = _number(event.get("timestamp"))
        if timestamp <= 0:
            continue
        rows.append(
            {
                "ability": ability_name,
                "guid": _event_ability_guid(event),
                "type": event.get("type"),
                "source": _event_source_label(event, catalog),
                "target": _event_target_label(event, catalog),
                "timestamp": round(timestamp),
                "relative_ms": _elapsed(timestamp, fight_start_ms),
                "source_instance": event.get("sourceInstance"),
                "target_instance": event.get("targetInstance"),
            }
        )
    return rows


def _deadly_aura_peaks(
    tables: Mapping[str, Any],
    fight_start_ms: float,
    limit: int = 40,
) -> list[dict[str, Any]]:
    catalog = _build_actor_catalog(tables)
    peaks: dict[tuple[int, str], dict[str, Any]] = {}

    for event in sorted(
        _entries(tables.get("buff_debuff_events")),
        key=lambda event: _number(event.get("timestamp")),
    ):
        if event.get("targetIsFriendly") is not True:
            continue
        target_id = event.get("targetID")
        if not isinstance(target_id, int):
            continue
        ability_name = _event_ability_name(event)
        ability_guid = _event_ability_guid(event)
        matched_name = None
        for candidate, guids in DEADLY_AURA_RULES.items():
            if ability_name == candidate or ability_guid in guids:
                matched_name = candidate
                break
        if matched_name is None:
            continue

        timestamp = _number(event.get("timestamp"))
        if timestamp <= 0:
            continue
        event_type = str(event.get("type") or "")
        key = (target_id, matched_name)
        peak = peaks.setdefault(
            key,
            {
                "player_id": target_id,
                "player": catalog.get(target_id, {}).get("name") or "未知",
                "ability": matched_name,
                "max_stacks": 0,
                "first_seen": timestamp,
                "last_seen": timestamp,
            },
        )
        peak["first_seen"] = min(peak["first_seen"], timestamp)
        peak["last_seen"] = max(peak["last_seen"], timestamp)

        if event_type in AURA_APPLY_TYPES:
            if event_type == "applydebuff":
                current = int(event.get("stack") or 1)
            elif event_type == "applydebuffstack":
                current = int(event.get("stack") or (peak.get("current_stacks", 0) + 1))
            else:
                current = max(1, peak.get("current_stacks", 0))
            peak["current_stacks"] = current
            peak["max_stacks"] = max(peak["max_stacks"], current)
        elif event_type in AURA_REMOVE_TYPES:
            current = peak.get("current_stacks", 0)
            if event_type == "removedebuffstack":
                current = max(0, current - 1)
            else:
                current = 0
            peak["current_stacks"] = current

    rows = []
    for peak in peaks.values():
        peak.pop("current_stacks", None)
        peak["first_seen_relative_ms"] = _elapsed(peak["first_seen"], fight_start_ms)
        peak["last_seen_relative_ms"] = _elapsed(peak["last_seen"], fight_start_ms)
        rows.append(peak)

    rows.sort(key=lambda row: (row["max_stacks"], row["last_seen"]), reverse=True)
    return rows[:limit]


def _deaths_overlapping_mechanics(
    tables: Mapping[str, Any],
    fight_start_ms: float,
) -> list[dict[str, Any]]:
    catalog = _build_actor_catalog(tables)
    intervals = _aura_intervals(tables, KEY_DEBUFF_ABILITIES, KEY_DEBUFF_GUIDS)
    rows: list[dict[str, Any]] = []

    for death in _entries(tables.get("deaths")):
        player_id = death.get("id")
        death_time = death.get("timestamp") or death.get("deathTime")
        if not isinstance(player_id, int) or death_time is None:
            continue
        death_time = _number(death_time)
        if death_time <= 0:
            continue
        for interval in intervals:
            if interval["target_id"] != player_id:
                continue
            if death_time < interval["start"]:
                continue
            end = interval["end"]
            # 容差之内结束的减益算作「死时仍在身上」——那是死亡剥离光环，不是提前消失
            if end is not None and death_time - end > DEATH_AURA_STRIP_TOLERANCE_MS:
                continue
            rows.append(
                {
                    "player_id": player_id,
                    "player": catalog.get(player_id, {}).get("name") or "未知",
                    "ability": interval["ability"],
                    "death_time": round(death_time),
                    "death_relative_ms": _elapsed(death_time, fight_start_ms),
                    "aura_start_relative_ms": _elapsed(interval["start"], fight_start_ms),
                    "aura_end_relative_ms": _elapsed(end, fight_start_ms) if end is not None else None,
                    # 正数 = 减益在死亡前多少毫秒被移除（接近 0 就是死亡剥离光环的
                    # 特征）；负数 = 减益在死亡时仍生效、之后才结束；None = 一直到战斗结束
                    "aura_removed_before_death_ms": round(death_time - end) if end is not None else None,
                }
            )
    return rows


def _mechanic_hit_summary(
    tables: Mapping[str, Any],
    fight_start_ms: float,
    fight_end_ms: float,
) -> dict[str, Any]:
    catalog = _build_actor_catalog(tables)
    wave_hits: list[dict[str, Any]] = []

    for event in sorted(
        _entries(tables.get("buff_debuff_events")),
        key=lambda event: _number(event.get("timestamp")),
    ):
        if event.get("targetIsFriendly") is not True:
            continue
        target_id = event.get("targetID")
        if not isinstance(target_id, int):
            continue
        if not _ability_matches(event, CORROSIVE_WAVE_ABILITIES, CORROSIVE_WAVE_GUIDS):
            continue
        if str(event.get("type") or "") not in AURA_APPLY_TYPES:
            continue
        timestamp = _number(event.get("timestamp"))
        if timestamp <= 0:
            continue
        wave_hits.append(
            {
                "player_id": target_id,
                "player": catalog.get(target_id, {}).get("name") or "未知",
                "ability": _event_ability_name(event),
                "guid": _event_ability_guid(event),
                "timestamp": round(timestamp),
                "relative_ms": _elapsed(timestamp, fight_start_ms),
            }
        )

    seen_wave_hits: set[tuple[int, float, str]] = set()
    deduped_wave_hits: list[dict[str, Any]] = []
    for hit in wave_hits:
        key = (hit["player_id"], hit["timestamp"], str(hit["ability"]))
        if key in seen_wave_hits:
            continue
        seen_wave_hits.add(key)
        deduped_wave_hits.append(hit)

    carrier_intervals = _aura_intervals(
        tables,
        EGG_CARRIER_ABILITIES,
        EGG_CARRIER_GUIDS,
    )
    end_time = fight_end_ms if fight_end_ms > fight_start_ms else None

    def is_carrying_egg(player_id: int, timestamp: float) -> bool:
        for interval in carrier_intervals:
            if interval["target_id"] != player_id:
                continue
            if timestamp < interval["start"]:
                continue
            interval_end = interval["end"] or end_time
            if interval_end is not None and timestamp > interval_end:
                continue
            return True
        return False

    egg_carrier_hits: list[dict[str, Any]] = []
    for hit in deduped_wave_hits:
        carrying_egg = is_carrying_egg(hit["player_id"], hit["timestamp"])
        if carrying_egg:
            egg_carrier_hits.append(
                {
                    **hit,
                    "egg_carrier": True,
                }
            )
        hit["egg_carrier"] = bool(carrying_egg)

    carriers_seen: list[dict[str, Any]] = []
    for interval in carrier_intervals:
        player_id = interval["target_id"]
        carriers_seen.append(
            {
                "player_id": player_id,
                "player": catalog.get(player_id, {}).get("name") or "未知",
                "ability": interval["ability"],
                "start": round(interval["start"]),
                "relative_start_ms": _elapsed(interval["start"], fight_start_ms),
                "end": round(interval["end"]) if interval["end"] is not None else None,
                "relative_end_ms": _elapsed(interval["end"], fight_start_ms) if interval["end"] is not None else None,
            }
        )

    hatch_events = _mechanic_cast_timeline(tables, fight_start_ms, HATCH_CAST_ABILITIES)
    gestation_events = _mechanic_cast_timeline(tables, fight_start_ms, GESTATION_CAST_ABILITIES)
    deadly_aura_peaks = _deadly_aura_peaks(tables, fight_start_ms)
    deaths_overlapping_mechanics = _deaths_overlapping_mechanics(tables, fight_start_ms)

    return {
        "corrosive_wave_ability": "腐蚀浪潮",
        "egg_carrier_ability": "厄鳞外壳",
        "wave_hits": deduped_wave_hits,
        "egg_carrier_wave_hits": egg_carrier_hits,
        "egg_carriers_seen": carriers_seen,
        "hatch_events": hatch_events,
        "gestation_events": gestation_events,
        "deadly_aura_peaks": deadly_aura_peaks,
        "deaths_overlapping_mechanics": deaths_overlapping_mechanics,
    }


def _event_stream_counts(tables: Mapping[str, Any]) -> dict[str, int]:
    views = (
        "buff_debuff_events",
        "cast_events",
        "death_events",
        "interrupt_events",
        "dispel_events",
        "resource_events",
        "spawn_events",
        "combatant_info_events",
    )
    return {view: len(_entries(tables.get(view))) for view in views}


def _boss_cast_timeline(
    tables: Mapping[str, Any],
    fight_start_ms: float = 0,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    friendly_ids = set(_build_actor_catalog(tables).keys())
    rows: list[dict[str, Any]] = []

    for event in _entries(tables.get("cast_events")):
        timestamp = _number(event.get("timestamp"))
        event_type = event.get("type")
        source_id = event.get("sourceID")
        if event_type not in ("cast", "begincast"):
            continue
        if event.get("sourceIsFriendly") is True or source_id in friendly_ids:
            continue
        ability = _event_ability_name(event)
        if not ability:
            continue
        elapsed_ms = max(0.0, timestamp - _number(fight_start_ms))
        rows.append(
            {
                "timestamp": round(elapsed_ms),
                "type": event_type,
                "source": "Boss",
                "target": "",
                "ability": ability,
                "detail": f"Boss 施放 {ability}",
            }
        )

    rows.sort(key=lambda row: row["timestamp"])
    return rows[:limit]


def _behavior_detail_summary(payload: Any, cap_players: int = 20) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in _entries(payload):
        if entry.get("available") is False:
            continue
        spell = _entry_name(entry)
        players: list[dict[str, Any]] = []
        for detail in entry.get("details") or []:
            if not isinstance(detail, dict):
                continue
            abilities = [
                ability.get("name")
                for ability in detail.get("abilities") or []
                if isinstance(ability, dict) and ability.get("name")
            ]
            players.append(
                {
                    "player": detail.get("name") or "Unknown",
                    "id": detail.get("id"),
                    "total": detail.get("total"),
                    "abilities": abilities,
                }
            )
        if not players:
            continue
        players.sort(key=lambda row: _number(row.get("total")), reverse=True)
        rows.append({"ability": spell, "players": players[:cap_players]})
    return rows[:80]


def table_errors(tables: Mapping[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for view, payload in tables.items():
        if isinstance(payload, dict) and payload.get("available") is False:
            errors.append({"view": view, "message": str(payload.get("error") or "未知错误")})
    return errors


def summarize_tables(
    tables: Mapping[str, Any],
    timeline_limit: int = 1000,
    duration_ms: float | None = None,
    fight_start_ms: float | None = None,
) -> dict[str, Any]:
    duration_seconds = max(0.0, _number(duration_ms)) / 1000
    fight_start = _number(fight_start_ms)
    fight_end = fight_start + _number(duration_ms)
    # 建一次共用，避免每处辅助函数各建一遍；榜单和死亡记录也靠它拿到准确专精
    catalog = _build_actor_catalog(tables)
    item_levels = _item_levels_by_actor(tables)
    deaths = _death_rows(tables.get("deaths"), fight_start, catalog)
    casts = _behavior_rows(tables.get("casts"))
    aura_summary = _aura_table_summary(tables, fight_start)
    boss_timeline = _boss_cast_timeline(tables, fight_start, timeline_limit)
    player_behavior = _player_event_summaries(tables)
    player_aura_coverage = _player_aura_coverage(tables, fight_start, fight_end)
    resource_summary = _resource_summary(tables)
    event_stream_counts = _event_stream_counts(tables)
    interrupt_summary = _behavior_detail_summary(tables.get("interrupts"))
    dispel_summary = _behavior_detail_summary(tables.get("dispels"))
    mechanic_hits = _mechanic_hit_summary(tables, fight_start, fight_end)

    return {
        "event_count": _total_entries(tables),
        "damage_done": _rank_rows(tables.get("damage-done"), duration_seconds, catalog, item_levels),
        "healing_done": _rank_rows(tables.get("healing"), duration_seconds, catalog, item_levels),
        "damage_taken": _rank_rows(tables.get("damage-taken"), duration_seconds, catalog, item_levels),
        "deaths": deaths,
        "death_count": len(deaths),
        "buffs": aura_summary["buffs"],
        "debuffs": aura_summary["debuffs"],
        "casts": casts,
        "player_behavior": player_behavior,
        "player_aura_coverage": player_aura_coverage,
        "resource_summary": resource_summary,
        "event_stream_counts": event_stream_counts,
        "boss_timeline": boss_timeline,
        "interrupts": interrupt_summary,
        "dispels": dispel_summary,
        "mechanic_hits": mechanic_hits,
        "timeline": _timeline(deaths, boss_timeline, timeline_limit),
        "table_errors": table_errors(tables),
    }

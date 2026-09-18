from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Mapping

from .wow import localize_role

RANK_VIEWS = {
    "damage-done": "damage_done",
    "healing": "healing_done",
    "damage-taken": "damage_taken",
}

ERROR_KEYS = {"available", "error"}


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


def _rank_rows(payload: Any, duration_seconds: float = 0) -> list[dict[str, Any]]:
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
        role = localize_role(_role_source(entry))
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

    rows.sort(key=lambda row: row["amount"], reverse=True)
    return rows


def _death_rows(payload: Any, fight_start_ms: float = 0) -> list[dict[str, Any]]:
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
        role = localize_role(_role_source(entry))
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


def _event_stream_counts(tables: Mapping[str, Any]) -> dict[str, int]:
    views = (
        "buff_debuff_events",
        "cast_events",
        "death_events",
        "interrupt_events",
        "dispel_events",
        "resource_events",
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
    deaths = _death_rows(tables.get("deaths"), fight_start)
    casts = _behavior_rows(tables.get("casts"))
    aura_summary = _aura_table_summary(tables, fight_start)
    boss_timeline = _boss_cast_timeline(tables, fight_start, timeline_limit)
    player_behavior = _player_event_summaries(tables)
    player_aura_coverage = _player_aura_coverage(tables, fight_start, fight_end)
    resource_summary = _resource_summary(tables)
    event_stream_counts = _event_stream_counts(tables)
    interrupt_summary = _behavior_detail_summary(tables.get("interrupts"))
    dispel_summary = _behavior_detail_summary(tables.get("dispels"))

    return {
        "event_count": _total_entries(tables),
        "damage_done": _rank_rows(tables.get("damage-done"), duration_seconds),
        "healing_done": _rank_rows(tables.get("healing"), duration_seconds),
        "damage_taken": _rank_rows(tables.get("damage-taken"), duration_seconds),
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
        "timeline": _timeline(deaths, boss_timeline, timeline_limit),
        "table_errors": table_errors(tables),
    }

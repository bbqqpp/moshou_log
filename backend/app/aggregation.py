from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Mapping

from .wow import (
    is_mythic_plus,
    load_affix_names,
    localize_role,
    localize_spec_id,
)

RANK_VIEWS = {
    "damage-done": "damage_done",
    "healing": "healing_done",
    "damage-taken": "damage_taken",
}

ERROR_KEYS = {"available", "error"}

#: 真正算「事件/条目」的数据键。
#:
#: `tables` 里除了这 18 个，还会有 `rankings` / `player_details` / `survivability`
#: 这类 V2 附加数据（见 wcl.py 的 `_fetch_extras`）。它们也是 `{"entries": [...]}`
#: 形状、能被 `_entries` 读到，但**不是战斗事件**，不能计进前端显示的「事件数量」。
COUNTED_ENTRY_KEYS = frozenset(
    {
        "damage-done", "healing", "damage-taken", "deaths", "buffs", "debuffs",
        "casts", "interrupts", "dispels", "summons",
        "buff_debuff_events", "cast_events", "interrupt_events", "dispel_events",
        "resource_events", "combatant_info_events", "spawn_events", "death_events",
    }
)

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


#: 判断「这一行是不是玩家角色」时按顺序看的字段。
#: `icon` 兜底是因为它形如 `Mage-Arcane` / `Shaman-Restoration` ——
#: `_build_actor_catalog` 也把 icon 当作 type 的备选。
_PLAYER_HINT_KEYS = ("type", "guidType", "class", "spec", "icon")


def is_player_entry(entry: Mapping[str, Any]) -> bool:
    """这一行是不是**玩家角色**，而不是 BOSS / NPC / 宠物。

    WCL 排行榜条目里的 `type` 对玩家是职业名（`Shaman`/`Mage`/…），
    对非玩家是 `Boss`/`NPC`/`Pet`。实测 36 份缓存里两者**零重叠** ——
    所以「能解析出职业」就是玩家，这是最干净的判据。

    不筛掉的后果（实测）：会自我治疗的 BOSS（妖术领主玛拉卡斯，988 万）
    以 `type: "Boss"` 挤进治疗榜第 10/25 位，还能被勾选生成「玩家报告」，
    输出 `spec: "Boss"`、`cast_count: 0` 这种荒谬结果；宠物（`光诞鞭笞者`）
    同样出现在伤害榜和名册里。

    顺带修掉一个数值失真：`percent` 的分母是「榜单总量」，
    混进 BOSS 的自我治疗会把所有玩家的占比压低。
    """
    for key in _PLAYER_HINT_KEYS:
        value = entry.get(key)
        if value in (None, ""):
            continue
        if localize_role(str(value))["class"]:
            return True
    return False


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
        # 榜单是「谁打了多少」，BOSS / NPC / 宠物不该混在里面
        if not is_player_entry(entry):
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

        # 承伤构成：WCL 自己按技能统计的「死前吃了什么伤害」，按总量降序。
        # 这是死亡归因里最可靠的一项，而且**每条死亡都有**。
        damage = entry.get("damage") or {}
        breakdown = [
            {"ability": item.get("name"), "total": round(_number(item.get("total")), 2)}
            for item in (damage.get("abilities") or [])
            if isinstance(item, dict) and item.get("name")
        ][:3]

        # ⚠️ `killingBlow.name` 是**致死技能的名字**，不是击杀者。
        #
        # WCL 的 deaths 表里**根本没有击杀者字段**（只有承伤统计和承伤构成）。
        # 原来这段把它当成 `source`（击杀者）：前端「击杀来源」列显示的是技能名，
        # 送进模型的句子是「赵小帅 死亡，击杀者：毒液爆裂」，而 `ability` 字段
        # 一路回退到 `entry["type"]`（**职业名**，实测是 "Shaman"/"Warlock"）。
        # 实测 5 条死亡里还有 2 条没有 `killingBlow`，此时退回承伤构成的首位技能。
        #
        # 刻意**不去猜击杀者**：`deaths[].events` 里最后一条伤害的 sourceID 看着像
        # 凶手，但实测有死者自己（折射）和明显不是致命一击的情况 —— 猜错比留空更糟。
        killing_ability = killing_blow.get("name") or (
            breakdown[0]["ability"] if breakdown else None
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
                # 致死技能（不是击杀者）
                "killing_ability": killing_ability,
                # 死前承伤构成 Top 3，用于区分「被一发秒了」和「被多段磨死」
                "damage_breakdown": breakdown,
                "class": role["class"],
                "spec": role["spec"],
                "role_label": role["label"],
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
                # 致死技能，不是击杀者（deaths 表里没有击杀者字段）
                "actor": death.get("killing_ability"),
                "target": death.get("player"),
                "ability": death.get("killing_ability"),
                "detail": f"{death.get('player')} 死亡，致死技能：{death.get('killing_ability') or '未知'}",
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


def _pull_timeline(
    death_rows: list[dict[str, Any]],
    pulls: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """大秘境的时间轴：死亡 + **拉怪段边界**。

    不能用 `_timeline` —— 它把敌方施法拼进去，而在副本里「敌方」是满地小怪，
    会产出几千条 `"Boss 施放 X"` 的错误叙事（实测 3,996 条）。
    这里只放死亡和段落起始，条数天然就少。
    """
    items: list[dict[str, Any]] = []

    for death in death_rows:
        items.append(
            {
                "timestamp": death.get("timestamp"),
                "type": "death",
                # 致死技能，不是击杀者（deaths 表里没有击杀者字段）
                "actor": death.get("killing_ability"),
                "target": death.get("player"),
                "ability": death.get("killing_ability"),
                "detail": f"{death.get('player')} 死亡，致死技能：{death.get('killing_ability') or '未知'}",
            }
        )

    for pull in pulls:
        label = pull.get("name") or "未知"
        kind = "首领" if pull.get("is_boss") else "小怪"
        items.append(
            {
                "timestamp": pull.get("start_ms"),
                "type": "pull",
                "actor": "",
                "target": "",
                "ability": label,
                "detail": f"第 {pull.get('index')} 段（{kind}）：{label}",
            }
        )

    items.sort(key=lambda item: item.get("timestamp") or 0)
    return items[:limit]


def _extras_index(tables: Mapping[str, Any], key: str) -> dict[int, dict[str, Any]]:
    """把 V2 附加数据（rankings / player_details / survivability）按 actor id 索引。

    这些键只在走 V2 取数时有；V1 时代的历史缓存里没有，所以要容忍缺失。
    """
    payload = tables.get(key)
    if not isinstance(payload, dict) or payload.get("available") is False:
        return {}

    index: dict[int, dict[str, Any]] = {}
    for row in payload.get("entries") or []:
        if not isinstance(row, dict):
            continue
        actor_id = row.get("id")
        if isinstance(actor_id, int):
            index[actor_id] = row
    return index


def phase_timeline(
    fight: Mapping[str, Any], tables: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """把真实阶段数据拼成时间轴。

    WCL 把这件事拆在两个地方：

    - `fight.phaseTransitions`：`[{id, startTime}]` —— 哪个阶段从哪一毫秒开始
    - `tables["phases"]`：`{phases: [{id, name, isIntermission}]}` —— 阶段叫什么

    拼起来就是**带名字的完整时间轴**，可以直接替代 `boss_guides.py` 里手写的阶段表。

    返回的时间都是**相对战斗开始**的毫秒。同一个阶段 id 可能出现多次
    （转阶段型 BOSS 会反复进出），所以这里是逐段列出而不是按 id 去重。
    """
    # `phaseTransitions` 是 V2 的原生键名；V1 存下来的历史缓存把它放在 `phases`
    # 下（形状完全相同，实测都是 `[{id, startTime}]`）。两个都读，历史战斗的
    # 阶段时间才不会被白白丢掉 —— 它们缺的只是阶段**名字**（V1 没有 report.phases）。
    transitions = fight.get("phaseTransitions") or fight.get("phases")
    if not isinstance(transitions, list) or not transitions:
        return []

    names: dict[Any, dict[str, Any]] = {}
    payload = tables.get("phases")
    if isinstance(payload, dict):
        for phase in payload.get("phases") or []:
            if isinstance(phase, dict):
                names[phase.get("id")] = phase

    try:
        fight_start = float(fight.get("start_time") or 0)
        fight_end = float(fight.get("end_time") or 0)
    except (TypeError, ValueError):
        return []

    segments: list[dict[str, Any]] = []
    for index, item in enumerate(transitions):
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("startTime"))
        except (TypeError, ValueError):
            continue

        next_start = fight_end
        for following in transitions[index + 1 :]:
            if isinstance(following, dict):
                try:
                    next_start = float(following.get("startTime"))
                    break
                except (TypeError, ValueError):
                    continue

        meta = names.get(item.get("id")) or {}
        segments.append(
            {
                "phase_id": item.get("id"),
                "name": meta.get("name"),
                "is_intermission": bool(meta.get("isIntermission")),
                "start_ms": int(start - fight_start),
                "end_ms": int(next_start - fight_start),
                "duration_ms": int(max(0.0, next_start - start)),
            }
        )

    return segments


def _total_entries(tables: Mapping[str, Any]) -> int:
    total = 0
    for key, payload in tables.items():
        if key not in COUNTED_ENTRY_KEYS:
            continue
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
            # id < 0 是 WCL 的哨兵（-1 = Environment），既不是玩家也不是宠物。
            # 放进来会让它出现在按人索引的地方 —— 实测它会带着 WCL 的
            # `subType: "Boss"` 混进 `player_behavior`（一份按人统计的表）。
            if actor_id < 0:
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

            # 一个光环可能有多段 band（掉了又补），必须取**最早**的起点和
            # **最晚**的终点。这里原来写成了直接赋值，于是 first_seen 恒等于
            # 最后一段的起点 —— 实测全库 35 份缓存里 2341 行的这个字段都是错的
            # （例如「水之护盾」三段 band 起于 0/447982/449613，报出来是 449613）。
            first_seen: int | None = None
            last_seen: int | None = None
            for band in bands:
                if not isinstance(band, dict):
                    continue
                start = _number(band.get("startTime") or band.get("start"))
                end = _number(band.get("endTime") or band.get("end"))
                if start > 0:
                    elapsed = _elapsed(start, fight_start_ms)
                    first_seen = elapsed if first_seen is None else min(first_seen, elapsed)
                if end > 0:
                    elapsed = _elapsed(end, fight_start_ms)
                    last_seen = elapsed if last_seen is None else max(last_seen, elapsed)

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
    catalog = _build_actor_catalog(tables)
    friendly_ids = set(catalog.keys())
    rows: list[dict[str, Any]] = []

    for event in _entries(tables.get("cast_events")):
        timestamp = _number(event.get("timestamp"))
        event_type = event.get("type")
        source_id = event.get("sourceID")
        if event_type not in ("cast", "begincast"):
            continue

        # 优先信事件自带的阵营标志。**只有在它缺失时**才退回目录判断 ——
        # 目录是从 damage-done/healing/… 取的，会混进**自我治疗的 BOSS**
        # （实测：玛拉卡斯靠自愈进 healing 表 → 被当友方 → 177+134 次真 BOSS
        # 施法整段丢弃，时间轴只剩无名小怪，却每行都写着「Boss 施放」）。
        friendly_flag = event.get("sourceIsFriendly")
        if friendly_flag is True:
            continue
        if friendly_flag is None and source_id in friendly_ids:
            continue

        ability = _event_ability_name(event)
        if not ability:
            continue

        # 能查到名字就用名字 —— 一场战斗可能有多个敌方单位，
        # 全部写成「Boss」会让模型以为只有一个施法者。
        name = (catalog.get(source_id) or {}).get("name") if isinstance(source_id, int) else None
        source_label = str(name) if name else "敌方单位"

        elapsed_ms = max(0.0, timestamp - _number(fight_start_ms))
        rows.append(
            {
                "timestamp": round(elapsed_ms),
                "type": event_type,
                "source": source_label,
                "target": "",
                "ability": ability,
                "detail": f"{source_label} 施放 {ability}",
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


#: 已知不可靠、不该当成「取数失败」上报的派生数据。
#:
#: `report.graph` 实测时有时无（见 wcl.py 的 `_adapt_graph`），它拿不到数据是常态
#: 而不是故障。若把它算进 `table_errors`，每次分析都会给用户挂一条
#: 「部分 WCL tables 获取失败」的假警告，还会让模型去解释一处根本不存在的缺失。
_NOT_ERROR_REPORTED = frozenset({"graph"})


def table_errors(tables: Mapping[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for view, payload in tables.items():
        if view in _NOT_ERROR_REPORTED:
            continue
        if isinstance(payload, dict) and payload.get("available") is False:
            errors.append({"view": view, "message": str(payload.get("error") or "未知错误")})
    return errors


def parse_ranking_note(tables: Mapping[str, Any]) -> str:
    """`summary.parse_ranking` 为空的原因（有的话）。

    空有三种完全不同的原因，不能都当成「没有排名」：
    - 灭团场次：WCL 只给击杀排名
    - 大秘境：WCL 给的百分位恒为 100，没有比较意义（见 wcl._adapt_rankings）
    - 迁移前拉下来的旧缓存：根本没有 `rankings` 这个键
    """
    if "rankings" not in tables:
        return "本地缓存是迁移前拉的，没有 parse 排名这项数据；重新分析一次这场战斗就会有"
    payload = tables.get("rankings")
    if isinstance(payload, dict) and payload.get("note"):
        return str(payload["note"])
    return ""


def parse_ranking_rows(tables: Mapping[str, Any]) -> list[dict[str, Any]]:
    """每人的 parse 百分位，按百分位从高到低。

    **只有击杀场次有**：灭团不产生排名；大秘境也没有可比的百分位。
    """
    payload = tables.get("rankings")
    if not isinstance(payload, dict) or payload.get("available") is False:
        return []

    rows: list[dict[str, Any]] = []
    for row in payload.get("entries") or []:
        if not isinstance(row, dict):
            continue
        # WCL 这里返回的是英文（class = "DeathKnight"、spec = "Blood"），而项目
        # 其余部分的 `class` 都是中文——前端的职业配色表和死亡记录表都按中文查。
        # 不转的话这一列的颜色会掉成兜底色、「专精」列会显示英文。
        raw_class = str(row.get("class") or "")
        raw_spec = str(row.get("spec") or "")
        localized = localize_role(f"{raw_spec} {raw_class}".strip())
        rows.append(
            {
                "player": row.get("name"),
                "role": row.get("role"),
                "spec": localized.get("spec") or raw_spec,
                "class": localized.get("class") or raw_class,
                "rank_percent": row.get("rankPercent"),
                "total_parses": row.get("totalParses"),
                "bracket_ilvl": row.get("bracketData"),
            }
        )
    rows.sort(key=lambda r: _number(r.get("rank_percent")), reverse=True)
    return rows


def _pull_field(pull: Mapping[str, Any], *names: str) -> Any:
    """按顺序取第一个非空字段。

    V1 和 V2 的 `dungeonPulls` 键名不同：V1 是 `start_time`/`end_time`/`boss`，
    V2 是 `startTime`/`endTime`/`encounterID`。磁盘上 33 份历史缓存全是 V1 形状，
    只认 V2 键名会让它们的分段时长全变 0、首领段全判成小怪。
    """
    for name in names:
        value = pull.get(name)
        if value is not None:
            return value
    return None


def pull_summary(fight: Mapping[str, Any], tables: Mapping[str, Any]) -> list[dict[str, Any]]:
    """大秘境的拉怪分段。

    这是大秘境分析的基本单位——团本按「阶段」组织，大秘境按「拉怪」组织。

    **BOSS 段的判据是 encounterID 非 0，不是 `kill`**：实测杂兵段的 `kill`
    恒为 false（哪怕顺利清掉），只有首领段的 `kill` 才有意义。

    时间都是相对副本开始的毫秒，与 `summary` 里其他地方一致。
    """
    pulls = fight.get("dungeonPulls")
    if not isinstance(pulls, list) or not pulls:
        return []

    fight_start = _number(fight.get("start_time"))

    # 死亡事件按时间戳落进对应的拉怪窗口
    death_points: list[tuple[float, str]] = []
    for death in _entries(tables.get("deaths")):
        raw = death.get("timestamp")
        if raw is None:
            raw = death.get("deathTime")
        stamp = _number(raw)
        if stamp:
            death_points.append((stamp, str(death.get("name") or death.get("player") or "?")))

    rows: list[dict[str, Any]] = []
    for index, pull in enumerate(pulls, start=1):
        if not isinstance(pull, dict):
            continue
        start = _number(_pull_field(pull, "startTime", "start_time"))
        end = _number(_pull_field(pull, "endTime", "end_time"))
        in_window = [name for stamp, name in death_points if start <= stamp <= end]
        rows.append(
            {
                "index": index,
                "name": pull.get("name"),
                # encounterID / boss 非 0 才是首领段
                "is_boss": bool(_pull_field(pull, "encounterID", "boss")),
                "start_ms": int(start - fight_start),
                "end_ms": int(end - fight_start),
                "duration_ms": int(max(0.0, end - start)),
                "death_count": len(in_window),
                "deaths": in_window,
                # 这一波拉了几只。V2 的 key 是 `enemyNPCs`，V1 是 `enemies`
                "enemy_count": len(
                    _pull_field(pull, "enemyNPCs", "enemies") or []
                ),
            }
        )
    return rows


def time_accounting(fight: Mapping[str, Any]) -> dict[str, Any]:
    """大秘境的时间账：拉怪 vs 非战斗（跑图、等待、复活）。

    M+ 的成败就是时间，所以「时间花在哪了」是核心指标。实测一场 12 层：
    拉怪 1325s / 总 1432s → 107s 非战斗时间。
    """
    pulls = fight.get("dungeonPulls")
    if not isinstance(pulls, list) or not pulls:
        return {}

    total_ms = max(
        0.0, _number(fight.get("end_time")) - _number(fight.get("start_time"))
    )
    pull_ms = sum(
        max(
            0.0,
            _number(_pull_field(p, "endTime", "end_time"))
            - _number(_pull_field(p, "startTime", "start_time")),
        )
        for p in pulls
        if isinstance(p, dict)
    )
    # 拉怪之间与末尾的空隙都算非战斗时间
    downtime_ms = max(0.0, total_ms - pull_ms)

    slowest = sorted(
        (
            {
                "index": index,
                "name": p.get("name"),
                "duration_ms": int(
                    max(
                        0.0,
                        _number(_pull_field(p, "endTime", "end_time"))
                        - _number(_pull_field(p, "startTime", "start_time")),
                    )
                ),
            }
            for index, p in enumerate(pulls, start=1)
            if isinstance(p, dict)
        ),
        key=lambda row: row["duration_ms"],
        reverse=True,
    )[:3]

    return {
        "total_ms": int(total_ms),
        "pull_ms": int(pull_ms),
        "downtime_ms": int(downtime_ms),
        "pull_count": len([p for p in pulls if isinstance(p, dict)]),
        "slowest_pulls": slowest,
    }


def keystone_summary(fight: Mapping[str, Any]) -> dict[str, Any]:
    """钥匙信息。`timed` 的判据是 `keystoneBonus > 0`（0 = 超时/黑掉）。

    ⚠️ V1 时代的历史缓存**没有 `keystoneBonus`**（那时叫 `medal`，且语义不同），
    所以 `timed` 会是 `None`（未知），而不是 False —— 把未知显示成「超时」是错的。
    """
    if not is_mythic_plus(fight):
        return {}

    bonus = fight.get("keystoneBonus")
    rating = fight.get("rating")

    # 词缀：V2 取数时已经把中文名写进 `keystoneAffixNames`；V1 历史缓存只有 id，
    # 拿落盘的对照表解析（见 wow.load_affix_names）。两样都没有就退回 id 字符串，
    # 但绝不显示成空白。
    names = list(fight.get("keystoneAffixNames") or [])
    if not names:
        raw_ids = fight.get("keystoneAffixes") or fight.get("affixes") or []
        table = load_affix_names()
        names = [table.get(int(i), str(i)) for i in raw_ids]

    return {
        "level": fight.get("keystoneLevel"),
        "affixes": names,
        "bonus": bonus,
        # keystoneBonus 0 = 超时，>=1 = 限时（+1/+2/+3）；缺失 = 未知（V1 缓存）
        "timed": None if bonus is None else bool(bonus > 0),
        # V2 是 `keystoneTime`，V1 历史缓存是 `completionTime`（实测值就在里面）
        "completion_ms": fight.get("keystoneTime") or fight.get("completionTime"),
        "rating": round(float(rating), 1) if isinstance(rating, (int, float)) else None,
        "count_reached": fight.get("countReached"),
        "count_required": fight.get("countRequired"),
        "average_item_level": fight.get("averageItemLevel"),
    }


def summarize_tables(
    tables: Mapping[str, Any],
    timeline_limit: int = 1000,
    duration_ms: float | None = None,
    fight_start_ms: float | None = None,
    fight: Mapping[str, Any] | None = None,
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
    player_behavior = _player_event_summaries(tables)
    player_aura_coverage = _player_aura_coverage(tables, fight_start, fight_end)
    resource_summary = _resource_summary(tables)
    event_stream_counts = _event_stream_counts(tables)
    interrupt_summary = _behavior_detail_summary(tables.get("interrupts"))
    dispel_summary = _behavior_detail_summary(tables.get("dispels"))

    # 大秘境走一套独立的输出。团本专有的键**整个不产出**（不是产出后再让
    # 提示词去忽略）—— 留下空壳会被读成「本场没有 X」，而那是假事实。
    # 详见 docs/wcl-api-alternative：mechanic_hits 由某个团本 BOSS 的技能名
    # 硬编码驱动，boss_timeline 会把每只小怪的施法标成「Boss 施放」。
    mythic_plus = is_mythic_plus(fight or {})
    pulls = pull_summary(fight or {}, tables) if mythic_plus else []

    if mythic_plus:
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
            "interrupts": interrupt_summary,
            "dispels": dispel_summary,
            "timeline": _pull_timeline(deaths, pulls, timeline_limit),
            "table_errors": table_errors(tables),
            # --- 大秘境专有 ---
            "keystone": keystone_summary(fight or {}),
            "pull_summary": pulls,
            "time_accounting": time_accounting(fight or {}),
        }

    boss_timeline = _boss_cast_timeline(tables, fight_start, timeline_limit)
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
        # --- V2 独有（V1 历史缓存里没有，会是空列表）---
        # 真实阶段时间轴，来自 WCL 而不是手写攻略
        "phases": phase_timeline(fight or {}, tables),
        # 每人的 parse 百分位；灭团场次与 M+ 为空
        "parse_ranking": parse_ranking_rows(tables),
        # 为空时说明原因（灭团 / 大秘境 / 旧缓存），前端拿它当空状态文案
        "parse_ranking_note": parse_ranking_note(tables) if not parse_ranking_rows(tables) else "",
    }

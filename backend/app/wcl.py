"""WCL 取数层。

对外只暴露 V1 时代的数据形状：`WCLClient.extract_fight()` 仍然返回
`(fight, tables, warnings)`，其中 `fight` 用 `start_time` / `boss` 这类 V1 键名，
`tables` 仍然是那 18 个键、事件流仍然是 `{"available": True, "events": [...], "truncated": bool}`。

这样做的原因见 `docs/wcl-api-roadmap.md`：下游（聚合 / 提示词 / MCP / 玩家报告）
一行不用改，磁盘上已有的历史缓存也继续可用。

底层传输是 V2 GraphQL（`wcl_v2.py`）。V2 与 V1 的形状差异、以及三个必须显式
指定的查询参数（`translate` / `useActorIDs` / `useAbilityIDs`），都在实测对拍中
确定，见 `backend/scripts/verify_v2_parity.py`。
"""
from __future__ import annotations

import asyncio
import re
import urllib.parse
from typing import Any, Iterable, Mapping

import httpx

from .config import Settings
from .errors import WCLApiError, WCLAuthError, WCLUrlError
from .wcl_v2 import WCLV2Client, errors_by_alias
from .wow import is_mythic_plus

ALLOWED_HOSTS = {"www.warcraftlogs.com", "warcraftlogs.com", "cn.warcraftlogs.com"}
ARCHON_HOSTS = {"www.archon.gg", "archon.gg"}
REPORT_PATH_RE = re.compile(r"(?:^|/)reports/([A-Za-z0-9]{8,64})(?:/|$)")
FIGHT_RE = re.compile(r"(?:[#?&])fight=(\d+)")
LAST_FIGHT_RE = re.compile(r"(?:[#?&])fight=last", re.IGNORECASE)
# archon.gg 是 WCL 的第三方前端，战斗 ID 在路径里而不是 fragment：
# /wow/reports/{code}/fights/{id}/raid
ARCHON_FIGHT_PATH_RE = re.compile(r"/reports/([A-Za-z0-9]{8,64})(?:/fights/(\d+))?")

#: 内部键名沿用 V1 的写法，下游按这些名字查询
TABLE_VIEWS = (
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

#: V1 视图名 → V2 TableDataType 枚举
TABLE_DATA_TYPES = {
    "damage-done": "DamageDone",
    "healing": "Healing",
    "damage-taken": "DamageTaken",
    "deaths": "Deaths",
    "buffs": "Buffs",
    "debuffs": "Debuffs",
    "casts": "Casts",
    "interrupts": "Interrupts",
    "dispels": "Dispels",
    "summons": "Summons",
}

#: 事件流 → V1 当年用的原始过滤表达式，**原样复用**。
#:
#: 用 `dataType: All` + 这个表达式，而不是 V2 的类型化 dataType（`Casts` /
#: `Buffs` / …）。原因是类型化的 dataType 各自带着网站面板的隐含过滤，实测：
#:
#: - `dataType: Casts` **排除平砍**（V1 的 cast_events 含 `Melee`，V2 的 Casts 不含），
#:   用 `All` + 表达式才拿得回那两条
#: - 类型化 dataType 默认只返回 Friendlies（`hostilityType` 默认值），
#:   实测 `cast_events` 会从 10 条掉到 5 条、光环流会丢 20%
#: - `All` 默认就含双方，不需要拆阵营再合并
#:
#: `All` + 表达式返回的事件集合与「四 alias 合并」的原始结果逐条一致
#: （小战斗 51、大战斗 118,276），但只需要一个查询。
EVENT_STREAM_FILTERS: dict[str, str] = {
    "buff_debuff_events": (
        'type in ("applybuff","removebuff","refreshbuff","applydebuff",'
        '"removedebuff","refreshdebuff","applybuffstack","removebuffstack",'
        '"applydebuffstack","removedebuffstack")'
    ),
    "cast_events": 'type in ("cast","begincast")',
    "interrupt_events": 'type in ("interrupt")',
    "dispel_events": 'type in ("dispel")',
    "resource_events": 'type in ("resourcechange")',
    "combatant_info_events": 'type in ("combatantinfo")',
    "spawn_events": 'type in ("summon","create")',
}

#: 需要 `includeResources: true` 的事件流。
#:
#: ⚠️ V2 的事件**默认只返回 9 个字段**（timestamp/type/sourceID/targetID/ability/
#: fight/sourceIsFriendly/targetIsFriendly/sourceInstance），而 V1 返回 24 个。
#: 少掉的里面有一个是代码真正依赖的：**`cast_events[].itemLevel`**
#: （`aggregation._item_levels_by_actor` 靠它算全团装等，缺了会让名册的
#: 装等静默变成 None）。开了 `includeResources` 才能拿回 itemLevel、
#: hitPoints、classResources、x/y 坐标这些。
#:
#: 只给 cast_events 开：光环流一场能有十万条事件，给每条都挂上这些字段
#: 会让缓存文件成倍膨胀，而聚合层并不读光环事件的资源字段。
EVENT_STREAMS_WITH_RESOURCES = frozenset({"cast_events"})

#: V1 的 aura 事件类型，用于形状守卫
AURA_EVENT_TYPES = frozenset(
    {
        "applybuff", "removebuff", "refreshbuff", "applybuffstack", "removebuffstack",
        "applydebuff", "removedebuff", "refreshdebuff", "applydebuffstack",
        "removedebuffstack",
    }
)

DEATH_WINDOW_PRE_MS = 15_000
DEATH_WINDOW_POST_MS = 3_000
#: V1 的死亡窗口过滤条件，V2 里用 filterExpression 原样复用
DEATH_EVENT_FILTER = 'type in ("damage","heal","absorbed")'

#: 一个请求里塞多少个 alias。死亡窗口最多可以有二十几个，分批发。
MAX_ALIASES_PER_REQUEST = 8

# A big raid fight needs well over a hundred WCL requests. Firing them strictly
# one at a time took ~3 minutes, which is long enough for a reverse proxy in
# front of us (Cloudflare drops at ~100s) to kill the request. Keep some
# concurrency, but bounded so we don't trip WCL's own rate limiting.
MAX_CONCURRENT_WCL_REQUESTS = 6

#: 事件去重用的身份字段。跨 alias 的重复事件是完全相同的对象，
#: 用这组字段足以识别；不必对整条事件做序列化比较。
_DEDUP_FIELDS = ("timestamp", "type", "sourceID", "targetID")


def normalize_url(url: str) -> str:
    value = url.strip()
    if not value:
        raise WCLUrlError("WCL 链接不能为空")
    if not re.match(r"^https?://", value, re.IGNORECASE):
        value = f"https://{value}"
    return value


def _parse_archon_url(parsed: urllib.parse.ParseResult) -> tuple[str, int]:
    """Extract report code and fight id from an archon.gg report URL."""
    path = urllib.parse.unquote(parsed.path or "")
    match = ARCHON_FIGHT_PATH_RE.search(path)
    if not match:
        raise WCLUrlError("无法从 archon.gg 链接中解析 WCL report code")

    report_code, fight_id = match.group(1), match.group(2)
    if not fight_id:
        raise WCLUrlError(
            "archon.gg 链接缺少战斗 ID，请先打开具体战斗再复制链接"
            "（形如 /reports/CODE/fights/12）"
        )

    return report_code, int(fight_id)


def parse_wcl_url(url: str) -> tuple[str, int | None]:
    """Return ``(report_code, fight_id)``.

    ``fight_id`` is ``None`` when the link asks for ``#fight=last``. That is a
    WCL frontend convenience the API does not understand, so the caller resolves
    it to the report's last boss fight once it has the fight list.
    """
    normalized = normalize_url(url)
    parsed = urllib.parse.urlparse(normalized)
    host = (parsed.hostname or "").lower()

    if host in ARCHON_HOSTS:
        return _parse_archon_url(parsed)

    if host not in ALLOWED_HOSTS:
        raise WCLUrlError(f"暂不支持 WCL 域名：{host or '空域名'}")

    path = urllib.parse.unquote(parsed.path or "")
    report_match = REPORT_PATH_RE.search(path)
    if not report_match:
        raise WCLUrlError("无法从链接中解析 WCL report code")

    report_code = report_match.group(1)

    if LAST_FIGHT_RE.search(url):
        return report_code, None

    fight_match = FIGHT_RE.search(url)
    if not fight_match:
        raise WCLUrlError("链接缺少 `#fight=<id>`，请从 WCL 具体战斗页面复制链接")

    return report_code, int(fight_match.group(1))


# --------------------------------------------------------------------------- #
# 形状适配：V2 → V1
# --------------------------------------------------------------------------- #
def _percent_to_v1(value: Any) -> int | None:
    """V1 的 fightPercentage/bossPercentage 是 ×100 的整数，V2 是浮点。

    实测 8563（V1） ↔ 85.63（V2）。
    """
    if value is None:
        return None
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return None


def _to_v1_fight(raw: Mapping[str, Any]) -> dict[str, Any]:
    """把 V2 的 ReportFight 翻成 V1 的键名。

    V1 的键名保留在 `boss` / `originalBoss` / `start_time` / `end_time`，是因为
    `boss_guides._boss_candidates` 和 `_is_boss_fight` 直接读它们，而这两个地方
    失效时**不会报错**——只会永远判定为非 Boss 战。
    """
    fight = dict(raw)

    fight["start_time"] = raw.get("startTime")
    fight["end_time"] = raw.get("endTime")
    fight["boss"] = raw.get("encounterID")
    fight["originalBoss"] = raw.get("originalEncounterID")
    fight["fightPercentage"] = _percent_to_v1(raw.get("fightPercentage"))
    fight["bossPercentage"] = _percent_to_v1(raw.get("bossPercentage"))

    game_zone = raw.get("gameZone") or {}
    fight["zoneID"] = game_zone.get("id")
    fight["zoneName"] = game_zone.get("name")

    # 类型归一化：V1 里这些字段可能是 None，而 player_compare.build_fairness
    # 会跨两场比较 kill，类型漂移会产生假的「一场击杀一场灭团」注记。
    fight["kill"] = None if raw.get("kill") is None else bool(raw["kill"])
    for key in ("size", "difficulty"):
        value = raw.get(key)
        fight[key] = None if value is None else int(value)

    return fight


def _unwrap_table(payload: Any) -> Any:
    """V2 的 `table` 比 V1 多包一层 `data`。

    实测 V1 是 ``{"entries": [...]}``，V2 是 ``{"data": {"entries": [...]}}``。
    `aggregation._entries` 只认顶层值为 list 的键、**不递归**，所以少解这一层
    会让每一张表静默返回空列表——没有异常、没有警告，还会被写进缓存。
    """
    if isinstance(payload, dict):
        inner = payload.get("data")
        if isinstance(inner, dict):
            return inner
    return payload


def _friendly_actor_ids(raw_fight: Mapping[str, Any]) -> set[int]:
    """从 fights 的名单里推导「友方 actor ID」集合。

    实测 31,378 条事件零不符（见 verify_v2_parity 的对拍）。注意**只信友方名单**：
    敌方名单不全（实测有 enemyNPCs 没列出、但事件里确实存在的敌方 actor），
    所以判定规则是「在友方集合里 = 友方，其余一律非友方」。
    """
    ids: set[int] = set()
    for value in raw_fight.get("friendlyPlayers") or []:
        if isinstance(value, int):
            ids.add(value)
    for key in ("friendlyPets", "friendlyNPCs"):
        for entry in raw_fight.get(key) or []:
            if isinstance(entry, dict) and isinstance(entry.get("id"), int):
                ids.add(entry["id"])
    return ids


def _error_envelope(payload: Any) -> str | None:
    """`field()` 在 alias 失败时塞进来的错误信封。

    适配函数必须先检查它——否则「取数失败」会被改写成「这场没有这项数据」，
    而后者是个会被信以为真的结论（提示词里还专门说了空 parse_ranking 是灭团常态）。
    """
    if isinstance(payload, dict) and payload.get("available") is False:
        return str(payload.get("error") or "WCL 未返回这项数据")
    return None


def _adapt_rankings(payload: Any, *, mythic_plus: bool = False) -> dict[str, Any]:
    """把 `report.rankings` 摊平成按人一行的表。

    原始形状是 ``{data: [{fightID, encounter, roles: {tanks: {characters: [...]}, ...}}]}``
    —— 按角色分组再嵌一层。摊平后可以直接走 `wcl_data_store.query` 和
    `aggregation._entries` 那套现成的读取路径。

    注意：**只有击杀场次有排名**，灭团时 `data` 是空列表。

    ⚠️ **大秘境要特殊处理**：实测 WCL 对一场限时通关的大秘境给每个人返回的都是
    `rankPercent: 100` / `rank: ~1`，而且 `bracketData` 是**钥匙层数**（实测 12）
    而不是装等区间。把 100 当成「同装等区间的前 1%」展示和叙述是错的——
    所以大秘境直接不产出百分位，只留一条说明。
    """
    if error := _error_envelope(payload):
        return {"available": False, "error": error}
    if not isinstance(payload, dict):
        return {"available": False, "error": "WCL 未返回排名数据"}

    if mythic_plus:
        return {
            "available": True,
            "entries": [],
            "mythic_plus": True,
            "note": (
                "大秘境不提供 raid 那种 parse 百分位：WCL 对限时通关的场次会给出"
                "恒为 100 的 rankPercent，且 bracketData 是钥匙层数而非装等区间，"
                "照搬会得出「全员完美发挥」的错误结论。"
            ),
        }

    fights = payload.get("data")
    if not isinstance(fights, list) or not fights:
        return {
            "available": True,
            "entries": [],
            "note": "这场没有 parse 排名（灭团场次不产生排名）",
        }

    entry = fights[0] if isinstance(fights[0], dict) else {}
    roles = entry.get("roles") or {}
    rows: list[dict[str, Any]] = []
    for role_key in ("tanks", "healers", "dps"):
        group = roles.get(role_key)
        if not isinstance(group, dict):
            continue
        for character in group.get("characters") or []:
            if not isinstance(character, dict):
                continue
            server = character.get("server") or {}
            rows.append(
                {
                    "id": character.get("id"),
                    "name": character.get("name"),
                    "role": role_key,
                    "class": character.get("class"),
                    "spec": character.get("spec"),
                    "server": server.get("name"),
                    "region": server.get("region"),
                    "amount": character.get("amount"),
                    # rankPercent 就是网站上的灰绿蓝紫橙
                    "rankPercent": character.get("rankPercent"),
                    "bracketPercent": character.get("bracketPercent"),
                    "rank": character.get("rank"),
                    "best": character.get("best"),
                    "totalParses": character.get("totalParses"),
                    # 同装等区间，用来区分「装备好」还是「手法好」
                    "bracketData": character.get("bracketData"),
                }
            )

    return {
        "available": True,
        "entries": rows,
        "encounter": entry.get("encounter"),
        "duration": entry.get("duration"),
        "deaths": entry.get("deaths"),
        "bracketData": entry.get("bracketData"),
        "damageTakenExcludingTanks": entry.get("damageTakenExcludingTanks"),
        "difficulty": entry.get("difficulty"),
        "size": entry.get("size"),
    }


def _adapt_player_details(payload: Any) -> dict[str, Any]:
    """摊平 `report.playerDetails`：专精、装等、爆发药水与治疗石使用次数。"""
    details = payload.get("data") if isinstance(payload, dict) else None
    # 这一项比别的多包一层：`{data: {playerDetails: {dps: [...], ...}}}`
    if isinstance(details, dict) and isinstance(details.get("playerDetails"), dict):
        details = details["playerDetails"]
    if not isinstance(details, dict):
        return {"available": False, "error": "WCL 未返回 playerDetails"}

    rows: list[dict[str, Any]] = []
    for role_key in ("tanks", "healers", "dps"):
        for player in details.get(role_key) or []:
            if not isinstance(player, dict):
                continue
            specs = player.get("specs") or []
            # 默认空字典而不是 None：没有 specs 的条目（宠物、日志不全的玩家）
            # 不能让整场取数崩掉
            primary: dict[str, Any] = {}
            if specs and isinstance(specs[0], dict):
                # 一个人可能中途切专精，取出现次数最多的那个
                primary = max(
                    (s for s in specs if isinstance(s, dict)),
                    key=lambda s: s.get("count") or 0,
                    default={},
                )
            rows.append(
                {
                    "id": player.get("id"),
                    # 角色全局 ID。rankings 用的是这个空间而不是战斗内的 actor ID，
                    # 靠它把两边对上（见 _link_rankings_to_actors）。
                    "guid": player.get("guid"),
                    "name": player.get("name"),
                    "role": role_key,
                    "class": player.get("type"),
                    "spec": primary.get("spec"),
                    "server": player.get("server"),
                    "region": player.get("region"),
                    "minItemLevel": player.get("minItemLevel"),
                    "maxItemLevel": player.get("maxItemLevel"),
                    # 这两项在 V1 里完全拿不到
                    "potionUse": player.get("potionUse"),
                    "healthstoneUse": player.get("healthstoneUse"),
                }
            )

    return {"available": True, "entries": rows}


def _link_rankings_to_actors(
    rankings: dict[str, Any], player_details: dict[str, Any]
) -> None:
    """把排名行里的 id 换成**战斗内的 actor id**（原地改）。

    ⚠️ `report.rankings` 的 `id` 是 WCL 的**角色 canonical ID**（实测阿唔 =
    112272952），而 `damage-done` / `deaths` 这类表和事件的 `sourceID` 用的是
    **战斗内的 actor ID**（实测阿唔 = 15）。两者不是一个空间，直接按 id 查会
    **全部查不到**——不报错，只会让名册里的 parse 百分位静默变成空。

    实测 `playerDetails[].guid`（87549933）**也不是** canonical ID，
    两者交集为 0，所以 guid 不能当桥。

    **唯一可靠的键是角色名**：`playerDetails` 同时带战斗内 `id` 和名字。
    重名（同一场里两个同名角色）时宁可不匹配也不猜——猜错会把别人的分数
    安到这个人头上，比缺失更糟。
    """
    by_name: dict[str, Any] = {}
    ambiguous: set[str] = set()
    for row in player_details.get("entries") or []:
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        if not name:
            continue
        if name in by_name:
            ambiguous.add(name)
            continue
        by_name[name] = row.get("id")

    for row in rankings.get("entries") or []:
        if not isinstance(row, dict):
            continue
        row["canonical_id"] = row.get("id")
        name = row.get("name")
        if name in ambiguous:
            row["match_note"] = "同名角色不止一个，无法确定对应关系"
            continue
        report_id = by_name.get(name)
        if report_id is not None:
            row["id"] = report_id


def _adapt_survivability(payload: Any, fight_id: Any) -> dict[str, Any]:
    """摊平 `table(dataType: Survivability)`。

    原始形状把分数埋在 ``players[].fights["<fightID>"]`` 里，这里提成一行一人。
    这个分数是**相对同专精**算的，比承伤总量公平——承伤高可能只是因为你是坦克。
    """
    if error := _error_envelope(payload):
        return {"available": False, "error": error}
    inner = _unwrap_table(payload)
    if not isinstance(inner, dict):
        return {"available": False, "error": "WCL 未返回生存数据"}

    key = str(fight_id)
    rows: list[dict[str, Any]] = []
    for player in inner.get("players") or []:
        if not isinstance(player, dict):
            continue
        per_fight = player.get("fights")
        score = per_fight.get(key) if isinstance(per_fight, dict) else None
        rows.append(
            {
                "id": player.get("id"),
                "name": player.get("name"),
                "class": player.get("type"),
                "survivability": score,
            }
        )

    return {"available": True, "entries": rows}


def _adapt_graph(payload: Any) -> dict[str, Any]:
    """整理 `report.graph` 的时间序列，供前端画曲线。

    每个 series 是 ``{name, id, pointStart, pointInterval, total, data: [数值...]}``，
    数值按 `pointInterval` 毫秒一格。落盘前截断，避免缓存文件膨胀。
    """
    if error := _error_envelope(payload):
        return {"available": False, "error": error, "series": []}
    graph = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(graph, dict):
        return {"available": False, "error": "WCL 未返回时间序列", "series": []}

    series: list[dict[str, Any]] = []
    for item in graph.get("series") or []:
        if not isinstance(item, dict):
            continue
        points = item.get("data")
        if not isinstance(points, list):
            points = []
        series.append(
            {
                "name": item.get("name"),
                "id": item.get("id"),
                "class": item.get("type"),
                "pointStart": item.get("pointStart"),
                "pointInterval": item.get("pointInterval"),
                "total": item.get("total"),
                "data": points[:MAX_GRAPH_POINTS],
            }
        )

    if not series:
        # ⚠️ `report.graph` 实测**不可靠**：同一个查询有时返回完整的逐时间片序列
        # （实测拿到过 22 条 series），有时持续返回空，跨 dataType、跨报告都是如此，
        # 且与点数配额无关（当时只用了 130/3600）。
        #
        # 所以这里显式标记为不可用而不是返回空列表——空列表会让前端画出一张
        # 没有数据的空图表，正是这一层最该避免的静默故障。
        return {
            "available": False,
            "error": "WCL 未返回时间序列（该接口实测不稳定，时有时无）",
            "series": [],
        }

    return {"available": True, "series": series}


def _adapt_phases(entries: Any, encounter_id: Any) -> dict[str, Any]:
    """取这个 BOSS 的阶段定义（id → 名字、是否转阶段）。

    入参是 `report.phases` 的原始列表。配合 `fight.phaseTransitions`
    （哪个阶段从哪一毫秒开始）就是完整的分阶段时间轴。

    这些是**真实数据**，可以直接替代 `boss_guides.py` 里手写的阶段表。
    """
    if error := _error_envelope(entries):
        return {"available": False, "error": error, "phases": []}
    if not isinstance(entries, list):
        return {"available": False, "error": "WCL 未返回阶段定义", "phases": []}

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if encounter_id is not None and str(entry.get("encounterID")) != str(encounter_id):
            continue
        phases = [
            {
                "id": phase.get("id"),
                "name": phase.get("name"),
                "isIntermission": phase.get("isIntermission"),
            }
            for phase in entry.get("phases") or []
            if isinstance(phase, dict)
        ]
        return {
            "available": True,
            "phases": phases,
            "separatesWipes": entry.get("separatesWipes"),
        }

    return {"available": True, "phases": []}


def _apply_friendliness(events: Iterable[dict[str, Any]], friendly: set[int]) -> None:
    """给 V2 事件补上 V1 的 sourceIsFriendly / targetIsFriendly。

    下游有四处依赖这两个标志（`_item_levels_by_actor`、`_player_aura_coverage`、
    `_boss_cast_timeline`、`_player_event_summaries`），缺了会静默给出错误结果。
    """
    for event in events:
        source_id = event.get("sourceID")
        target_id = event.get("targetID")
        if source_id is not None:
            event["sourceIsFriendly"] = source_id in friendly
        if target_id is not None:
            event["targetIsFriendly"] = target_id in friendly


def _dedup_key(event: Mapping[str, Any]) -> tuple[Any, ...]:
    ability = event.get("ability")
    guid = ability.get("guid") if isinstance(ability, dict) else ability
    return tuple(event.get(field) for field in _DEDUP_FIELDS) + (guid,)


def _merge_events(chunks: Iterable[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """按事件身份去重合并多个 alias 的结果，保持首次出现的顺序。"""
    merged: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for chunk in chunks:
        for event in chunk:
            key = _dedup_key(event)
            if key in seen:
                continue
            seen.add(key)
            merged.append(event)
    return merged


def _table_entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("entries", "events", "data", "auras", "deaths", "casts", "buffs", "debuffs"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _is_boss_fight(fight: Mapping[str, Any]) -> bool:
    """Whether a fight entry is a boss encounter rather than a trash pull.

    WCL marks boss fights with ``boss``, but on some reports a fight only
    carries ``originalBoss`` (observed on wipe attempts), and on others a boss
    attempt carries neither. So accept either field; trash pulls carry both
    unset. This is the best signal the API offers.
    """
    return bool(fight.get("boss")) or bool(fight.get("originalBoss"))


def _last_boss_fight(fights: list[Any]) -> dict[str, Any] | None:
    """Resolve ``#fight=last`` to the most recent boss encounter."""
    for fight in reversed(fights):
        if isinstance(fight, dict) and _is_boss_fight(fight):
            return fight
    return None


# --------------------------------------------------------------------------- #
# GraphQL 查询构造
# --------------------------------------------------------------------------- #
FIGHTS_QUERY = """
query($code: String!) {
  reportData { report(code: $code) {
    fights(translate: false) {
      id name encounterID originalEncounterID startTime endTime kill
      difficulty size fightPercentage bossPercentage inProgress
      gameZone { id name }
      friendlyPlayers
      friendlyPets { id }
      friendlyNPCs { id }
      lastPhase lastPhaseAsAbsoluteIndex
      phaseTransitions { id startTime }
      completeRaid averageItemLevel friendlySpecs
      boundingBox { minX maxX minY maxY }
      maps { id }
      keystoneLevel keystoneAffixes keystoneBonus keystoneTime rating
      countReached countRequired hardModeLevel layer
      npcCountMap
      dungeonPulls {
        id name startTime endTime kill encounterID
        x y maps { id }
        boundingBox { minX maxX minY maxY }
        enemyNPCs { id gameID }
      }
    }
  } }
}
"""

#: 一次请求取回所有"V2 独有"的附加数据。这些在 V1 里没有等价物。
#:
#: - `rankings`：战斗内每个人的 parse 百分位（灰绿蓝紫橙）
#: - `playerDetails`：角色/专精/装等/**爆发药水与治疗石使用次数**
#: - `phases`：BOSS 的阶段定义（id → 名字、是否转阶段）
#: - `graph`：逐时间片的伤害序列，给前端画曲线用
EXTRAS_QUERY = """
query($code: String!, $fightIDs: [Int]) {
  reportData { report(code: $code) {
    ex_rankings: rankings(compare: Parses, playerMetric: dps, fightIDs: $fightIDs)
    ex_player_details: playerDetails(fightIDs: $fightIDs, includeCombatantInfo: true, translate: false)
    ex_phases: phases { encounterID separatesWipes phases { id name isIntermission } }
    ex_graph: graph(dataType: DamageDone, viewBy: Source, fightIDs: $fightIDs)
    ex_survivability: table(dataType: Survivability, fightIDs: $fightIDs, translate: false)
  } }
}
"""

#: `graph` 的序列可能很长，落盘前按这个长度截断，避免缓存文件膨胀
MAX_GRAPH_POINTS = 1200

_TABLE_FIELD = (
    "table(dataType: %s, fightIDs: $fightIDs, translate: false)"
)


def _tables_query() -> str:
    """10 张表合并成一个请求（alias 批量化）。

    V2 按请求数扣点，实测单表查询约 0.9 点——10 次分开发就是 9 点，
    合并成一次只有零头。
    """
    fields = "\n    ".join(
        f"t_{view.replace('-', '_')}: {_TABLE_FIELD % TABLE_DATA_TYPES[view]}"
        for view in TABLE_VIEWS
    )
    return (
        "query($code: String!, $fightIDs: [Int]) {\n"
        "  reportData { report(code: $code) {\n    "
        f"{fields}\n"
        "  } }\n}"
    )


def _events_field(
    alias: str,
    data_type: str,
    start: float,
    end: float,
    *,
    filter_expression: str | None = None,
    include_resources: bool = False,
) -> str:
    """构造一个 events alias。start/end 是数值，直接内联进查询。

    不传 `hostilityType`：事件流一律走 `dataType: All` + 原始表达式，那个组合
    默认就含双方。类型化 dataType 才会按阵营过滤，而我们已经不用它们了。
    """
    args = [
        f"dataType: {data_type}",
        "fightIDs: $fightIDs",
        f"startTime: {int(start)}",
        f"endTime: {int(end)}",
        "translate: false",
        # 见 wcl_v2 模块 docstring：这两个参数不传就会静默出错
        "useActorIDs: true",
        "useAbilityIDs: false",
    ]
    if include_resources:
        args.append("includeResources: true")
    if filter_expression:
        # filterExpression 用的是 WCL 表达式语言，内部有双引号
        escaped = filter_expression.replace("\\", "\\\\").replace('"', '\\"')
        args.append(f'filterExpression: "{escaped}"')

    return f"{alias}: events({', '.join(args)}) {{ data nextPageTimestamp }}"


def _events_query(fields: list[str]) -> str:
    body = "\n    ".join(fields)
    return (
        "query($code: String!, $fightIDs: [Int]) {\n"
        "  reportData { report(code: $code) {\n    "
        f"{body}\n"
        "  } }\n}"
    )


# --------------------------------------------------------------------------- #
class WCLClient:
    """Fetches one fight from WCL V2 and returns it in the V1 shape."""

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.v2 = WCLV2Client(settings, transport=transport)
        self.timeout = settings.request_timeout
        self.max_event_pages = settings.max_wcl_event_pages
        self.table_views = TABLE_VIEWS

    def fetch_fights(self, report_code: str) -> list[dict[str, Any]]:
        """同步取报告的战斗列表（原始 V2 形状）。

        同步是必须的：`resolve_fight_reference` 被两个 skill 脚本同步调用。
        """
        data = self.v2.graphql_sync(FIGHTS_QUERY, {"code": report_code}, label="fights")
        return self._extract_fights(data, report_code)

    @staticmethod
    def _extract_fights(data: dict[str, Any], report_code: str) -> list[dict[str, Any]]:
        report = (data.get("reportData") or {}).get("report")
        # report 为 null 时 GraphQL **不产生 error**，必须显式处理
        if report is None:
            raise WCLApiError(
                f"WCL 里找不到报告 {report_code}，可能是 code 拼错了，"
                "或者这是一份私密报告（读私密报告需要用户 OAuth 授权）"
            )
        fights = report.get("fights")
        if not isinstance(fights, list):
            raise WCLApiError("WCL fights 响应格式异常")
        return [f for f in fights if isinstance(f, dict)]

    # ------------------------------------------------------------------ #
    async def extract_fight(
        self,
        report_code: str,
        fight_id: int | None,
    ) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
        """Return ``(fight, tables, warnings)`` for one WCL fight.

        Every table and event stream is independent, so they are fetched
        concurrently behind a semaphore rather than one after another.
        """
        timeout = httpx.Timeout(self.timeout, connect=15.0)
        async with httpx.AsyncClient(timeout=timeout, transport=self.v2.transport) as client:
            raw_fight, fights = await self._get_fight(client, report_code, fight_id)
            fight = _to_v1_fight(raw_fight)
            friendly = _friendly_actor_ids(raw_fight)

            if is_mythic_plus(fight):
                # 词缀 id 换成中文名。取不到时**降级为英文/原 id**，
                # 但绝不静默丢掉 —— 词缀是大秘境分析的核心背景。
                names = await self.v2.affix_names(client)
                fight["keystoneAffixNames"] = [
                    names.get(int(affix), str(affix))
                    for affix in (fight.get("keystoneAffixes") or [])
                ]

            semaphore = asyncio.Semaphore(MAX_CONCURRENT_WCL_REQUESTS)
            warnings: list[str] = []

            tables, table_warnings = await self._fetch_tables(
                client, semaphore, report_code, fight
            )
            warnings.extend(table_warnings)

            event_results = await asyncio.gather(
                *(
                    self._fetch_event_stream(
                        client, semaphore, report_code, fight, stream_key, friendly
                    )
                    for stream_key in EVENT_STREAM_FILTERS
                )
            )
            for stream_key, payload, warning in event_results:
                tables[stream_key] = payload
                if warning:
                    warnings.append(warning)

            await self._extract_death_events(
                client, semaphore, report_code, fight, tables, friendly, warnings
            )

            # 附加数据（parse 排名 / 角色档案 / 阶段 / 时间序列 / 生存分）。
            # 失败不影响主流程——这些都是锦上添花，缺了照样能复盘。
            try:
                extras = await self._fetch_extras(
                    client, semaphore, report_code, fight, friendly
                )
            except WCLApiError as exc:
                warnings.append(f"无法获取附加数据（parse 排名 / 阶段 / 时间序列）：{exc}")
            else:
                tables.update(extras)

        # 放在返回前而不是各调用点：三条取数路径（网页 / MCP / skill 脚本）
        # 都会经过这里，而坏数据一旦落盘就会被 load_fight 永久优先返回。
        self.validate_extract(tables)

        return fight, tables, warnings

    # ------------------------------------------------------------------ #
    async def _get_fight(
        self,
        client: httpx.AsyncClient,
        report_code: str,
        fight_id: int | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        data = await self.v2.graphql(client, FIGHTS_QUERY, {"code": report_code}, label="fights")
        raw_fights = self._extract_fights(data, report_code)

        if fight_id is None:
            # #fight=last 是 WCL 前端的语法糖，API 不认，得自己挑最后一场 Boss 战
            adapted = [_to_v1_fight(f) for f in raw_fights]
            index = next(
                (i for i, f in reversed(list(enumerate(adapted))) if _is_boss_fight(f)),
                None,
            )
            if index is None:
                raise WCLApiError("该 report 中找不到任何 Boss 战，无法解析 `fight=last`")
            return raw_fights[index], raw_fights

        target = next((f for f in raw_fights if str(f.get("id")) == str(fight_id)), None)
        if target is None:
            available = ", ".join(str(f.get("id")) for f in raw_fights[:10])
            raise WCLApiError(
                f"该 report 中找不到 fight id={fight_id}，可用战斗：{available or '无'}"
            )
        return target, raw_fights

    # ------------------------------------------------------------------ #
    async def _fetch_tables(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        report_code: str,
        fight: Mapping[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        """一次请求取全部 10 张表，失败按 alias 归因。"""
        warnings: list[str] = []
        tables: dict[str, Any] = {}

        async with semaphore:
            try:
                data = await self.v2.graphql(
                    client,
                    _tables_query(),
                    {"code": report_code, "fightIDs": [fight["id"]]},
                    label="tables",
                )
            except WCLApiError as exc:
                # 整批都失败了，逐张表写下错误信封
                for view in TABLE_VIEWS:
                    tables[view] = {"available": False, "error": str(exc)}
                    warnings.append(f"无法获取 {view} 表：{exc}")
                return tables, warnings

        report = (data.get("reportData") or {}).get("report") or {}
        failed = errors_by_alias(data)

        for view in TABLE_VIEWS:
            alias = f"t_{view.replace('-', '_')}"
            payload = report.get(alias)
            if payload is None:
                message = failed.get(alias, "WCL 未返回这张表")
                tables[view] = {"available": False, "error": message}
                warnings.append(f"无法获取 {view} 表：{message}")
            else:
                tables[view] = _unwrap_table(payload)

        return tables, warnings

    # ------------------------------------------------------------------ #
    async def _fetch_extras(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        report_code: str,
        fight: Mapping[str, Any],
        friendly: set[int],
    ) -> dict[str, Any]:
        """取 V2 独有的附加数据。一次请求全部拿回（alias 批量化）。

        这些在 V1 里没有等价物：
        parse 百分位、角色档案（含爆发药水使用）、真实阶段划分、输出时间序列、生存分。
        """
        async with semaphore:
            data = await self.v2.graphql(
                client,
                EXTRAS_QUERY,
                {"code": report_code, "fightIDs": [fight["id"]]},
                label="extras",
            )

        report = (data.get("reportData") or {}).get("report") or {}
        failed = errors_by_alias(data)

        def field(alias: str) -> Any:
            payload = report.get(alias)
            if payload is not None:
                return payload
            return {"available": False, "error": failed.get(alias, "WCL 未返回这项数据")}

        mythic_plus = is_mythic_plus(fight)
        rankings = _adapt_rankings(field("ex_rankings"), mythic_plus=mythic_plus)
        player_details = _adapt_player_details(field("ex_player_details"))
        # 排名用角色全局 ID，其余表用战斗内 actor ID —— 必须把前者换成后者，
        # 否则名册和玩家 payload 里的 parse 百分位会静默查不到
        _link_rankings_to_actors(rankings, player_details)

        return {
            "rankings": rankings,
            "player_details": player_details,
            "survivability": _adapt_survivability(field("ex_survivability"), fight.get("id")),
            "graph": _adapt_graph(field("ex_graph")),
            # 用 `boss or originalBoss`：杂兵战和部分灭团场次的 encounterID 是 0，
            # 真正的 BOSS 在 originalEncounterID 里。只取前者会让这些场次
            # （恰恰是复盘最常看的灭团场）拿不到阶段名。
            "phases": _adapt_phases(
                report.get("ex_phases"), fight.get("boss") or fight.get("originalBoss")
            ),
        }

    # ------------------------------------------------------------------ #
    async def _fetch_event_stream(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        report_code: str,
        fight: Mapping[str, Any],
        stream_key: str,
        friendly: set[int],
    ) -> tuple[str, dict[str, Any], str]:
        """取一条事件流（可能由多个 alias 组成），失败转成警告。"""
        start = fight.get("start_time")
        end = fight.get("end_time")
        if start is None or end is None:
            message = "WCL 未返回 fight 的 start/end 时间"
            return stream_key, {"available": False, "error": message}, f"无法获取 {stream_key}：{message}"

        specs = (
            (
                "e",
                EVENT_STREAM_FILTERS[stream_key],
                int(start),
                int(end),
                stream_key in EVENT_STREAMS_WITH_RESOURCES,
            ),
        )

        async with semaphore:
            try:
                chunks, truncated, failures = await self._paginate_aliases(
                    client, report_code, fight["id"], specs
                )
            except WCLApiError as exc:
                return (
                    stream_key,
                    {"available": False, "error": str(exc)},
                    f"无法获取 {stream_key}：{exc}",
                )

        # 这条 alias 报了错就不能返回空列表充数 —— 那会被读成「这场没有施法」
        if "e" in failures:
            message = failures["e"]
            return (
                stream_key,
                {"available": False, "error": message},
                f"无法获取 {stream_key}：{message}",
            )

        events = _merge_events(chunks.values())
        _apply_friendliness(events, friendly)

        warning = ""
        if truncated:
            warning = f"{stream_key} 达到分页上限，个别明细可能不完整"
        elif not self._types_are_known(stream_key, events):
            warning = f"{stream_key} 出现了预期之外的事件类型，数据可能不完整"

        return (
            stream_key,
            {"available": True, "events": events, "truncated": truncated},
            warning,
        )

    @staticmethod
    def _types_are_known(stream_key: str, events: list[dict[str, Any]]) -> bool:
        """光环流的 type 必须落在已知十种里。

        聚合层有六处按 `applybuff` / `removedebuffstack` 这类字面量分支，
        类型名变了会静默产出空区间而不是报错。
        """
        if stream_key != "buff_debuff_events":
            return True
        return all(event.get("type") in AURA_EVENT_TYPES for event in events)

    # ------------------------------------------------------------------ #
    async def _paginate_aliases(
        self,
        client: httpx.AsyncClient,
        report_code: str,
        fight_id: int,
        specs: tuple[tuple[str, str, int, int, bool], ...],
    ) -> tuple[dict[str, list[dict[str, Any]]], bool, dict[str, str]]:
        """多个 alias 共用一个请求，各自独立翻页。

        specs 是 ``(alias, filter_expression, start, end, include_resources)`` 的序列。
        返回 ``(每个 alias 的原始事件列表, 是否触顶, 每个失败 alias 的错误)``
        ——**不做合并**，因为死亡窗口需要按 alias 分别统计条数。
        """
        chunks: dict[str, list[dict[str, Any]]] = {spec[0]: [] for spec in specs}
        # cursor 为 None 表示这条 alias 已经翻完
        cursors: dict[str, int | None] = {spec[0]: spec[2] for spec in specs}
        ends: dict[str, int] = {spec[0]: spec[3] for spec in specs}
        failures: dict[str, str] = {}
        truncated = False

        for _ in range(self.max_event_pages):
            pending = [spec for spec in specs if cursors[spec[0]] is not None]
            if not pending:
                break

            fields = [
                _events_field(
                    alias, "All", cursors[alias], window_end,
                    filter_expression=filter_expression,
                    include_resources=include_resources,
                )
                for alias, filter_expression, _, window_end, include_resources in pending
            ]
            data = await self.v2.graphql(
                client, _events_query(fields), {"code": report_code, "fightIDs": [fight_id]},
                label=f"events:{','.join(spec[0] for spec in pending)}",
            )
            report = (data.get("reportData") or {}).get("report") or {}
            # GraphQL 是「HTTP 200 + 部分成功」：个别 alias 会返回 null 并带一条 error。
            # 若不看 errors，这种失败会被当成「这条 alias 已经翻完」——于是取数失败的
            # 事件流以 {available: true, events: []} 落盘，看起来就像「这场没有施法」。
            failed_aliases = errors_by_alias(data)

            advanced = False
            for alias, _, _, _, _ in pending:
                paginator = report.get(alias)
                if not isinstance(paginator, dict):
                    if alias in failed_aliases:
                        failures[alias] = failed_aliases[alias]
                    cursors[alias] = None
                    continue

                page = paginator.get("data")
                if isinstance(page, dict):
                    page = page.get("events")
                if not isinstance(page, list) or not page:
                    cursors[alias] = None
                    continue

                chunks[alias].extend(e for e in page if isinstance(e, dict))

                next_timestamp = paginator.get("nextPageTimestamp")
                if next_timestamp is None or next_timestamp <= cursors[alias] \
                        or next_timestamp >= ends[alias]:
                    cursors[alias] = None
                else:
                    cursors[alias] = int(next_timestamp)
                    advanced = True

            if not advanced:
                break
        else:
            # 循环跑满仍有人在翻页 —— 只有这种情况才算截断
            truncated = any(cursor is not None for cursor in cursors.values())

        return chunks, truncated, failures

    # ------------------------------------------------------------------ #
    async def _extract_death_events(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        report_code: str,
        fight: Mapping[str, Any],
        tables: dict[str, Any],
        friendly: set[int],
        warnings: list[str],
    ) -> None:
        """每个死亡点前后各取一段事件窗口。

        V1 用的是 ``type in ("damage","heal","absorbed")`` 这个原始过滤条件，
        V2 里用 ``dataType: All`` + 同一个 ``filterExpression`` 原样复现。

        **不能用 `dataType: DamageTaken`** —— 实测窗口流里 78% 是 heal 和
        absorbed，而且 V2 的事件枚举里**根本没有 absorbed 成员**。
        """
        deaths = _table_entries(tables.get("deaths"))
        if not deaths:
            tables["death_events"] = {
                "available": True, "events": [], "deaths": [], "truncated": False,
            }
            return

        fight_start = float(fight.get("start_time") or 0)
        fight_end = float(fight.get("end_time") or 0)

        windows: list[tuple[dict[str, Any], float, int, int]] = []
        for death in deaths:
            raw_time = death.get("timestamp")
            if raw_time is None:
                raw_time = death.get("deathTime")
            if raw_time is None:
                continue

            death_time = float(raw_time)
            window_start = int(max(fight_start, death_time - DEATH_WINDOW_PRE_MS))
            window_end = int(min(fight_end, death_time + DEATH_WINDOW_POST_MS))
            if window_end <= window_start:
                continue

            windows.append((death, death_time, window_start, window_end))

        windows = [(d, t, s, e) for d, t, s, e in windows if isinstance(d.get("id"), int)]
        if not windows:
            tables["death_events"] = {
                "available": True, "events": [], "deaths": [], "truncated": False,
            }
            return

        all_events: list[dict[str, Any]] = []
        death_windows: list[dict[str, Any]] = []
        any_truncated = False
        # 死亡窗口互相重叠（多个死亡点常常挨得很近），必须跨窗口全局去重，
        # 否则同一批事件会被重复计入。V1 也是这么做的。
        seen: set[tuple[Any, ...]] = set()

        async with semaphore:
            for batch_start in range(0, len(windows), MAX_ALIASES_PER_REQUEST):
                batch = windows[batch_start : batch_start + MAX_ALIASES_PER_REQUEST]
                try:
                    results, truncated = await self._fetch_death_batch(
                        client, report_code, fight, batch
                    )
                except WCLApiError as exc:
                    warnings.append(f"无法获取死亡窗口事件：{exc}")
                    continue

                any_truncated = any_truncated or truncated
                for window, events in results:
                    death, death_time, window_start, window_end = window
                    actor_id = death.get("id")
                    fresh: list[dict[str, Any]] = []
                    for event in events:
                        # 只留打到这个死亡玩家身上的事件。V2 的 targetID 参数
                        # 不过滤，所以这一步必须在本地做（V1 也是本地过滤）。
                        if str(event.get("targetID")) != str(actor_id):
                            continue
                        key = _dedup_key(event)
                        if key in seen:
                            continue
                        seen.add(key)
                        fresh.append(event)
                        all_events.append(event)
                    death_windows.append(
                        {
                            "player": death.get("name"),
                            "actor_id": death.get("id"),
                            "death_time": death_time,
                            "window_start": window_start,
                            "window_end": window_end,
                            "event_count": len(fresh),
                        }
                    )

        _apply_friendliness(all_events, friendly)
        tables["death_events"] = {
            "available": True,
            "events": all_events,
            "deaths": death_windows,
            "truncated": any_truncated,
        }
        if any_truncated:
            warnings.append("death_events 达到分页上限，个别死亡窗口明细可能不完整")

    async def _fetch_death_batch(
        self,
        client: httpx.AsyncClient,
        report_code: str,
        fight: Mapping[str, Any],
        batch: list[tuple[dict[str, Any], float, int, int]],
    ) -> tuple[list[tuple[tuple[dict[str, Any], float, int, int], list[dict[str, Any]]]], bool]:
        """一批死亡窗口 alias 一起发，各自翻页。

        用 alias 批量是因为一场战斗最多可以有二十几个死亡点，逐个发请求会
        成倍消耗 V2 的点数配额。
        """
        # 不传 targetID：**V2 的这个参数实测无效**（带与不带返回完全相同的结果集），
        # 必须在本地按 targetID 过滤，见 _extract_death_events。
        specs = tuple(
            (f"d{index}", DEATH_EVENT_FILTER, window_start, window_end, False)
            for index, (death, _, window_start, window_end) in enumerate(batch)
        )
        chunks, truncated, failures = await self._paginate_aliases(
            client, report_code, fight["id"], specs
        )
        results = []
        for index, window in enumerate(batch):
            alias = f"d{index}"
            if alias in failures:
                # 这个窗口取数失败：宁可让调用方记一条警告，也不要静默返回空窗口
                raise WCLApiError(failures[alias])
            results.append((window, _merge_events([chunks[alias]])))
        return results, truncated

    # ------------------------------------------------------------------ #
    def validate_extract(self, tables: Mapping[str, Any]) -> None:
        """落盘前的形状守卫。

        这一层没有单元测试兜底，而错误数据一旦写进 `wcl_data_store` 就会被
        `player_compare.load_fight` 永久优先返回（本地优先且无新鲜度检查）。
        所以宁可在这里硬失败，也不要留下一份「看起来正常但全是空」的缓存。

        V1 时代 `_entries` 只认顶层 list 键，V2 的表多包一层 `data`——少解一层
        就是全空的典型症状。
        """
        optional = ("summons", "interrupts", "dispels")
        must_have = [v for v in TABLE_VIEWS if v not in optional]

        # 先区分「整批请求失败」和「形状变了」——两者的排查方向完全相反。
        # 前者是配额/网络问题（表被标成 available: False），后者才是 _entries 解不开。
        errored = [
            view
            for view in must_have
            if isinstance(tables.get(view), dict) and tables[view].get("available") is False
        ]
        if len(errored) == len(must_have):
            first = tables[errored[0]].get("error")
            raise WCLApiError(
                f"所有关键表都取数失败（不是形状问题），第一个错误：{first}。"
                "多半是配额用尽或网络故障——稍后重试即可，拒绝写入缓存"
            )

        empty = [view for view in must_have if not _table_entries(tables.get(view))]
        if len(empty) == len(must_have):
            raise WCLApiError(
                "所有关键表都返回空——V2 响应形状可能又变了"
                "（检查 table 是否多包了一层 data）——拒绝写入缓存"
            )

        for stream_key, payload in tables.items():
            if not isinstance(payload, dict) or not payload.get("available"):
                continue
            events = payload.get("events") or []
            if not events:
                continue
            if stream_key == "buff_debuff_events" and not self._types_are_known(stream_key, events):
                raise WCLApiError(
                    "光环事件出现了未知的 type，聚合层会静默产出空区间——拒绝写入缓存"
                )


# --------------------------------------------------------------------------- #
def resolve_fight_reference(url: str, settings: Settings) -> tuple[str, int]:
    """把用户给的链接解析成 `(report_code, fight_id)`。

    与 `parse_wcl_url` 的区别是会**把 `#fight=last` 真的解析出来** —— 那需要一次
    fights 列表的 API 调用（'last' 是 WCL 前端的语法糖，API 不认）。

    **必须保持同步**：`.claude/skills/` 下的两个脚本直接同步调用它。
    """
    report_code, fight_id = parse_wcl_url(url)
    if fight_id is not None:
        return report_code, fight_id

    client = WCLClient(settings)
    if not client.v2.configured:
        raise WCLAuthError(
            "解析 `fight=last` 需要访问 WCL，请先在 backend/.env 配置 "
            "WCL_CLIENT_ID 与 WCL_CLIENT_SECRET"
        )

    adapted = [_to_v1_fight(f) for f in client.fetch_fights(report_code)]
    target = _last_boss_fight(adapted)
    if target is None:
        raise WCLApiError(f"{report_code} 里找不到 Boss 战，无法解析 `fight=last`")
    return report_code, int(target["id"])


__all__ = [
    "ALLOWED_HOSTS",
    "ARCHON_HOSTS",
    "AURA_EVENT_TYPES",
    "DEATH_EVENT_FILTER",
    "EVENT_STREAM_FILTERS",
    "TABLE_DATA_TYPES",
    "TABLE_VIEWS",
    "WCLClient",
    "normalize_url",
    "parse_wcl_url",
    "resolve_fight_reference",
    "_is_boss_fight",
    "_last_boss_fight",
    "_table_entries",
]

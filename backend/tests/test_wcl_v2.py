"""取数层的测试。

这一层在 V1 时代**完全没有测试**，而它的故障模式几乎全是静默的：形状变了不会
抛异常，只会让聚合层到处返回空列表，然后被写进缓存永久生效。所以这里的用例主要
是钉住「V2 形状 → V1 形状」的翻译契约，而不是测网络。

fixture 里的载荷是从真实 V2 响应里摘下来的（不是照 schema 编的）。
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.errors import WCLApiError, WCLAuthError
from app.wcl import (
    EVENT_STREAMS_WITH_RESOURCES,
    _link_rankings_to_actors,
    _adapt_graph,
    _adapt_phases,
    _adapt_player_details,
    _adapt_rankings,
    _adapt_survivability,
    EVENT_STREAM_FILTERS,
    TABLE_VIEWS,
    WCLClient,
    _to_v1_fight,
    _unwrap_table,
)
from app.wcl_v2 import WCLV2Client

GRAPHQL = "https://www.warcraftlogs.com/api/v2/client"

# --- 真实响应片段 --------------------------------------------------------- #

#: 真实的 ReportFight（Boss 战）。注意 V2 的百分比是浮点，V1 是 ×100 的整数。
REAL_FIGHT = {
    "id": 78,
    "name": "乌拉特克",
    "encounterID": 3492,
    "originalEncounterID": None,
    "startTime": 90372701,
    "endTime": 90502702,
    "kill": False,
    "difficulty": 4,
    "size": 20,
    "fightPercentage": 85.63,
    "bossPercentage": 79.84,
    "inProgress": False,
    "gameZone": {"id": 3004, "name": "烈毒之渊"},
    "friendlyPlayers": [6, 9, 19],
    "friendlyPets": [],
    "friendlyNPCs": [],
}

#: 真实的 table 响应：比 V1 多包一层 data —— 少了这层的解包，
#: aggregation._entries 会静默返回空列表。
REAL_TABLE = {
    "data": {
        "entries": [
            {
                "name": "请让战皇进本",
                "id": 4,
                "guid": 111284631,
                "type": "Warrior",
                "icon": "Warrior-Arms",
                "itemLevel": 322,
                "total": 146295002,
                "activeTime": 552082,
                "abilities": [{"guid": 12294, "name": "致死打击", "total": 20881955}],
            }
        ],
        "totalTime": 552082,
        "logVersion": 22,
    }
}

#: `deaths` 表必须有 timestamp/id，否则 `_extract_death_events` 会直接早退，
#: 死亡窗口（alias 批量 + 跨窗口去重 + 截断告警）这条路径在测试里就跑不到
REAL_DEATHS_TABLE = {
    "data": {
        "entries": [
            # timestamp 必须落在 fight 的 [startTime, endTime] 内，
            # 否则算出的死亡窗口是负区间、会被直接跳过
            {"name": "虚空咒歌", "id": 143, "type": "Mage",
             "timestamp": 90373701, "overkill": 0}
        ]
    }
}

#: 真实的事件（useActorIDs: true + useAbilityIDs: false）
REAL_EVENT = {
    "timestamp": 2966924,
    "type": "begincast",
    "sourceID": 19,
    "targetID": -1,
    "ability": {"name": "造餐术", "guid": 190336, "type": 64, "abilityIcon": "x.jpg"},
    "fight": 11,
}

REAL_AURA_EVENT = {
    "timestamp": 2966557,
    "type": "removebuff",
    "sourceID": 9,
    "targetID": 9,
    "ability": {"name": "潮汐洞察", "guid": 1295057, "type": 16},
    "fight": 11,
}

#: V2 独有的附加数据（形状取自真实响应）
REAL_RANKINGS = {
    "data": [
        {
            "fightID": 78,
            "encounter": {"id": 3492, "name": "Ula'tek"},
            "duration": 471091,
            "deaths": 1,
            "roles": {
                "tanks": {
                    "name": "Tanks",
                    "characters": [
                        {
                            "id": 15, "name": "阿唔", "class": "DeathKnight", "spec": "Blood",
                            "amount": 57529.39, "rankPercent": 39, "bracketPercent": 34,
                            "rank": "~14848", "best": "~14848", "totalParses": 24342,
                            "bracketData": 319,
                            "server": {"id": 887, "name": "鬼雾峰", "region": "CN"},
                        }
                    ],
                }
            },
        }
    ]
}

REAL_PLAYER_DETAILS = {
    "data": {
        "playerDetails": {
            "dps": [
                {
                    "id": 17, "name": "牛牛丿萨", "type": "Shaman",
                    "server": "白银之手", "region": "CN",
                    "specs": [{"spec": "Elemental", "count": 1}],
                    "minItemLevel": 322, "maxItemLevel": 322,
                    "potionUse": 2, "healthstoneUse": 1,
                }
            ]
        }
    }
}

REAL_SURVIVABILITY = {
    "data": {
        "players": [
            {"name": "赵小帅", "id": 1, "guid": 86407190, "type": "Shaman", "fights": {"78": 0.969}},
        ],
        "fights": [{"id": 78, "start": 90372701, "end": 90502702}],
    }
}

REAL_PHASES = [
    {
        "encounterID": 3445,
        "separatesWipes": False,
        "phases": [
            {"id": 1, "name": "Stage One: Entombed Sentinels", "isIntermission": False},
            {"id": 2, "name": "Intermission: Vitriolic Stasis", "isIntermission": True},
        ],
    }
]

#: `report.graph` 实测不稳定：拿到过完整序列，也持续拿到空。
#: 非空时形状是 `{"data": {"series": [{name, id, pointStart, pointInterval, total, data}]}}`
REAL_GRAPH = {"data": {"series": []}}

#: `extract_fight` 在原来 18 个键之外新增的 V2 独有数据
EXTRA_KEYS = {"rankings", "player_details", "survivability", "graph", "phases"}


def _settings() -> Settings:
    return Settings(
        allowed_guild_name="g",
        wcl_client_id="cid",
        wcl_client_secret="secret",
        deepseek_api_key="d",
    )


class _Stub:
    """按请求内容分发假响应。"""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.table_calls = 0
        self.extras_calls = 0
        self.event_aliases: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.warcraftlogs.com" and request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 31104000})

        body = json.loads(request.content)
        self.requests.append(body)
        query = body.get("query", "")

        if "fights(" in query:
            return httpx.Response(200, json={"data": {"reportData": {"report": {
                "fights": [REAL_FIGHT],
            }}}})

        # 附加数据那一批（alias 前缀 ex_）要在 table( 之前判断——
        # 它里面也含一个 table(dataType: Survivability)
        if "ex_rankings" in query:
            self.extras_calls += 1
            return httpx.Response(200, json={"data": {"reportData": {"report": {
                "ex_rankings": REAL_RANKINGS,
                "ex_player_details": REAL_PLAYER_DETAILS,
                "ex_phases": REAL_PHASES,
                "ex_graph": REAL_GRAPH,
                "ex_survivability": REAL_SURVIVABILITY,
            }}}})

        if "table(" in query:
            self.table_calls += 1
            fields = {
                f"t_{view.replace('-', '_')}": (
                    REAL_DEATHS_TABLE if view == "deaths" else REAL_TABLE
                )
                for view in TABLE_VIEWS
            }
            return httpx.Response(200, json={"data": {"reportData": {"report": fields}}})

        # events：一个请求里可能有多个 alias（事件流一条，死亡窗口一批），
        # 必须按查询里真实的 alias 名应答，否则取数端读不到、事件流全空、
        # 而测试只断言「键都在」照样通过 —— 等于这条路径没有覆盖。
        aliases = re.findall(r"(\w+):\s*events\(", query)
        if aliases:
            self.event_aliases.extend(aliases)
            # 光环流必须回 aura 类型的事件：validate_extract 会检查 type 落在
            # 已知十种里，回错了会被形状守卫拦下（那是守卫在正常工作）
            if "applybuff" in query:
                event = REAL_AURA_EVENT
            elif "absorbed" in query:
                # 死亡窗口：事件必须打在这个死亡玩家身上，否则会被本地
                # 的 targetID 过滤全部滤掉（V2 的 targetID 参数不过滤，只能本地做）
                event = dict(REAL_EVENT, type="damage", targetID=143, amount=9000)
            else:
                event = REAL_EVENT
            report = {
                alias: {"data": [event], "nextPageTimestamp": None}
                for alias in aliases
            }
            return httpx.Response(200, json={"data": {"reportData": {"report": report}}})

        return httpx.Response(200, json={"data": {}})


def _client(stub: _Stub) -> WCLClient:
    return WCLClient(_settings(), transport=httpx.MockTransport(stub.handler))


# --- 适配层 --------------------------------------------------------------- #

def test_fight_keys_are_translated_to_v1_names():
    """boss / originalBoss / start_time 是承重键名。

    `boss_guides._boss_candidates` 与 `_is_boss_fight` 直接读它们，读不到时
    不报错，只会永远判定为非 Boss 战、挑不到攻略。
    """
    fight = _to_v1_fight(REAL_FIGHT)

    assert fight["boss"] == 3492
    assert fight["originalBoss"] is None
    assert fight["start_time"] == 90372701
    assert fight["end_time"] == 90502702
    assert fight["zoneID"] == 3004
    # V2 原生键保留，方便后续迁移
    assert fight["encounterID"] == 3492


def test_percentage_is_scaled_to_v1_integer():
    """V1 的 fightPercentage 是 ×100 的整数，V2 是浮点。实测 8563 ↔ 85.63。"""
    fight = _to_v1_fight(REAL_FIGHT)
    assert fight["fightPercentage"] == 8563
    assert fight["bossPercentage"] == 7984
    assert isinstance(fight["fightPercentage"], int)


def test_trash_fight_nulls_do_not_crash():
    """杂兵战的 kill/difficulty/size 在 V2 里是 None（V1 是键直接缺失）。"""
    trash = dict(REAL_FIGHT, encounterID=0, originalEncounterID=3497,
                 kill=None, difficulty=None, size=None,
                 fightPercentage=None, bossPercentage=None)
    fight = _to_v1_fight(trash)
    assert fight["boss"] == 0
    assert fight["originalBoss"] == 3497
    assert fight["kill"] is None
    assert fight["fightPercentage"] is None


def test_table_data_wrapper_is_unwrapped():
    """V2 的 table 比 V1 多包一层 data。

    这是整个迁移里最危险的一处：`aggregation._entries` 只认顶层值为 list 的键、
    不递归，少解这一层会让**每一张表**静默返回空列表。
    """
    unwrapped = _unwrap_table(REAL_TABLE)
    assert "entries" in unwrapped
    assert unwrapped["entries"][0]["name"] == "请让战皇进本"


def test_unwrap_is_idempotent_on_v1_shaped_payload():
    """老缓存里已经是 V1 形状的载荷不能被这层解包破坏。"""
    v1_payload = {"entries": [{"name": "x"}], "totalTime": 1}
    assert _unwrap_table(v1_payload) == v1_payload


# --- 端到端信封 ------------------------------------------------------------ #

def test_extract_fight_returns_all_data_keys():
    stub = _Stub()
    fight, tables, warnings = asyncio.run(_client(stub).extract_fight("code", 78))

    expected = set(TABLE_VIEWS) | set(EVENT_STREAM_FILTERS) | {"death_events"} | EXTRA_KEYS
    assert set(tables) == expected
    assert fight["boss"] == 3492
    assert warnings == []


def test_extract_fight_actually_populates_event_streams():
    """端到端必须真的把事件取回来。

    这条断言是防「测试空转」的：stub 曾经因为 alias 解析写错而让**所有**事件流
    返回空，而当时唯一的断言是「键都在」——把 `_paginate_aliases` 的翻页整个删掉
    测试照样全绿。
    """
    stub = _Stub()
    _, tables, _ = asyncio.run(_client(stub).extract_fight("code", 78))

    for stream in EVENT_STREAM_FILTERS:
        events = tables[stream]["events"]
        assert events, f"{stream} 应当是取到了事件的"

    # 朋友标记是本地推导出来的（V2 不返回 sourceIsFriendly），必须真的算上
    aura = tables["buff_debuff_events"]["events"][0]
    assert aura["sourceIsFriendly"] is (aura["sourceID"] in REAL_FIGHT["friendlyPlayers"])

    # 死亡窗口：deaths 表里有带 timestamp 的条目，就该发出 d0 这条 alias
    assert tables["death_events"]["events"], "死亡窗口应当取到事件"
    assert tables["death_events"]["deaths"][0]["player"] == "虚空咒歌"
    assert any(alias.startswith("d") for alias in stub.event_aliases), stub.event_aliases


def test_batches_are_one_request_each():
    """10 张表合并成一个请求、5 项附加数据合并成另一个 —— V2 按请求扣点。"""
    stub = _Stub()
    asyncio.run(_client(stub).extract_fight("code", 78))
    assert stub.table_calls == 1
    assert stub.extras_calls == 1


# --- V2 独有的附加数据 ------------------------------------------------------ #

def test_rankings_are_flattened_from_role_groups():
    """原始形状按 tanks/healers/dps 分组再嵌一层，摊平后才能走现成的读取路径。"""
    rows = _adapt_rankings(REAL_RANKINGS)["entries"]
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "阿唔"
    assert row["role"] == "tanks"
    assert row["rankPercent"] == 39          # 网站上的灰绿蓝紫橙
    assert row["bracketData"] == 319         # 同装等区间
    assert row["server"] == "鬼雾峰"


def test_rankings_empty_on_wipe_is_not_an_error():
    """灭团场次没有排名，`data` 是空列表 —— 这是正常情况，不是失败。"""
    result = _adapt_rankings({"data": []})
    assert result["available"] is True
    assert result["entries"] == []


def test_player_details_unwraps_double_data():
    """这一项比别的多包一层：`{data: {playerDetails: {...}}}`。"""
    rows = _adapt_player_details(REAL_PLAYER_DETAILS)["entries"]
    assert len(rows) == 1
    row = rows[0]
    assert row["spec"] == "Elemental"
    assert row["class"] == "Shaman"
    assert row["potionUse"] == 2            # V1 完全拿不到
    assert row["healthstoneUse"] == 1


def test_survivability_extracts_per_fight_score():
    """分数埋在 players[].fights["<fightID>"] 里。"""
    rows = _adapt_survivability(REAL_SURVIVABILITY, 78)["entries"]
    assert rows[0]["name"] == "赵小帅"
    assert rows[0]["survivability"] == 0.969


def test_phases_are_matched_by_encounter_id():
    """一个报告里有很多 BOSS，只取这一场对应的阶段定义。"""
    result = _adapt_phases(REAL_PHASES, 3445)
    assert [p["name"] for p in result["phases"]] == [
        "Stage One: Entombed Sentinels",
        "Intermission: Vitriolic Stasis",
    ]
    assert result["phases"][1]["isIntermission"] is True

    assert _adapt_phases(REAL_PHASES, 9999)["phases"] == []


def test_rankings_ids_are_relinked_by_name():
    """`rankings.id` 是角色 canonical ID，其余表用战斗内 actor ID。

    实测两者不是一个空间（阿唔 = 112272952 vs 15），guid 也不是桥（交集为 0）。
    不换 id 的话名册里的 parse 百分位会**静默变成空**。
    """
    rankings = _adapt_rankings({
        "data": [{"roles": {"dps": {"characters": [
            {"id": 112272952, "name": "阿唔", "rankPercent": 70},
        ]}}}]
    })
    details = _adapt_player_details({
        "data": {"playerDetails": {"tanks": [
            {"id": 15, "guid": 87549933, "name": "阿唔", "type": "DeathKnight"},
        ]}}
    })
    _link_rankings_to_actors(rankings, details)

    row = rankings["entries"][0]
    assert row["id"] == 15                  # 换成战斗内 actor id
    assert row["canonical_id"] == 112272952  # 原值保留备查


def test_rankings_relink_refuses_ambiguous_names():
    """同名时不猜 —— 把别人的分数安到这个人头上比缺失更糟。"""
    rankings = _adapt_rankings({
        "data": [{"roles": {"dps": {"characters": [
            {"id": 1, "name": "同名", "rankPercent": 50},
        ]}}}]
    })
    details = _adapt_player_details({
        "data": {"playerDetails": {"dps": [
            {"id": 7, "name": "同名"}, {"id": 8, "name": "同名"},
        ]}}
    })
    _link_rankings_to_actors(rankings, details)

    row = rankings["entries"][0]
    assert row["id"] == 1                    # 保持原样，没被乱改
    assert "同名角色不止一个" in row["match_note"]


def test_only_cast_events_request_resources():
    """`includeResources` 只给 cast_events 开。

    V2 事件默认只有 9 个字段，`itemLevel`（全团装等的唯一来源）要靠这个开关
    才拿得回来。但光环流一场十万条事件，给每条都挂上会让缓存文件成倍膨胀。
    """
    assert EVENT_STREAMS_WITH_RESOURCES == frozenset({"cast_events"})

    stub = _Stub()
    asyncio.run(_client(stub).extract_fight("code", 78))

    event_queries = [b["query"] for b in stub.requests if "events(" in b["query"]]
    assert event_queries, "应当发出过事件查询"
    with_resources = [q for q in event_queries if "includeResources: true" in q]
    without = [q for q in event_queries if "includeResources: true" not in q]
    assert len(with_resources) == 1, "只有 cast_events 这一条流该带 includeResources"
    assert without, "其余事件流不应带 includeResources"

    # 光环流是数据量最大的那条，绝不能带
    assert not any(
        "includeResources" in q and "applybuff" in q for q in event_queries
    )


def test_empty_graph_is_marked_unavailable():
    """`report.graph` 实测不稳定（时有时无）。

    返回空列表会让前端画出一张没有数据的空图表 —— 必须显式标记不可用。
    """
    result = _adapt_graph(REAL_GRAPH)
    assert result["available"] is False
    assert "不稳定" in result["error"]


def test_table_failure_becomes_available_false_envelope():
    """单张表失败要变成 {"available": False, "error": ...} —— 这个形状没有
    `events` 键，下游靠 ERROR_KEYS 嗅探，必须保留这个不对称。"""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 999})
        body = json.loads(request.content)
        if "fights(" in body["query"]:
            return httpx.Response(200, json={"data": {"reportData": {"report": {
                "fights": [REAL_FIGHT]}}}})
        if "table(" in body["query"]:
            # deaths 那个 alias 失败，其余正常
            fields = {f"t_{v.replace('-', '_')}": REAL_TABLE for v in TABLE_VIEWS}
            fields["t_deaths"] = None
            return httpx.Response(200, json={
                "data": {"reportData": {"report": fields}},
                "errors": [{"message": "boom", "path": ["reportData", "report", "t_deaths"]}],
            })
        return httpx.Response(200, json={"data": {"reportData": {"report": {}}}})

    client = WCLClient(_settings(), transport=httpx.MockTransport(handler))
    _, tables, warnings = asyncio.run(client.extract_fight("code", 78))

    assert tables["deaths"]["available"] is False
    assert "events" not in tables["deaths"]
    # 错误要**按 alias 归因**到这个具体的表上。GraphQL 的 error path 是
    # ["reportData", "report", "t_deaths"]，取错位置（比如取 path[0]）会让
    # 每个 alias 都归到 "reportData" 上，于是消息丢失、退化成通用文案。
    assert "boom" in tables["deaths"]["error"]
    assert any("boom" in w for w in warnings)
    # 其余表不受影响 —— 部分失败不能被放大成整体失败
    assert tables["damage-done"]["entries"]


# --- 错误分类 -------------------------------------------------------------- #

def test_report_null_raises_api_error():
    """坏 code / 私密报告时 report 是 null，且 GraphQL **不产生 error**。"""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 999})
        return httpx.Response(200, json={"data": {"reportData": {"report": None}}})

    client = WCLClient(_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(WCLApiError, match="找不到报告"):
        asyncio.run(client.extract_fight("badcode", 1))


def test_http_401_becomes_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 999})
        return httpx.Response(401, json={})

    client = WCLClient(_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(WCLAuthError):
        asyncio.run(client.extract_fight("code", 1))


def test_token_rejected_gives_actionable_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_client"})

    v2 = WCLV2Client(_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(WCLAuthError, match="confidential"):
        v2.tokens.get()


def test_missing_credentials_raise_auth_error():
    settings = Settings(allowed_guild_name="g", wcl_client_id="", wcl_client_secret="")
    v2 = WCLV2Client(settings)
    with pytest.raises(WCLAuthError, match="未配置"):
        v2.tokens.get()


# --- token 缓存 ------------------------------------------------------------ #

def test_token_is_reused_across_calls():
    calls = {"token": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["token"] += 1
        return httpx.Response(200, json={"access_token": "t", "expires_in": 31104000})

    v2 = WCLV2Client(_settings(), transport=httpx.MockTransport(handler))
    assert v2.tokens.get() == "t"
    assert v2.tokens.get() == "t"
    assert calls["token"] == 1


def test_token_refetch_after_invalidate():
    calls = {"token": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["token"] += 1
        return httpx.Response(200, json={"access_token": f"t{calls['token']}", "expires_in": 999})

    v2 = WCLV2Client(_settings(), transport=httpx.MockTransport(handler))
    assert v2.tokens.get() == "t1"
    v2.tokens.invalidate()
    assert v2.tokens.get() == "t2"


def test_token_cache_is_thread_safe_not_loop_bound():
    """用 threading.Lock 而非 asyncio.Lock。

    `player_compare.load_fight` 内部 asyncio.run 每次新建事件循环，模块级的
    asyncio 原语会在第二次调用时抛 "attached to a different loop"。
    这条用例模拟「两个独立事件循环先后取 token」。
    """
    calls = {"token": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["token"] += 1
        return httpx.Response(200, json={"access_token": "t", "expires_in": 31104000})

    v2 = WCLV2Client(_settings(), transport=httpx.MockTransport(handler))

    async def take() -> str:
        return v2.tokens.get()

    assert asyncio.run(take()) == "t"
    assert asyncio.run(take()) == "t"   # 第二个循环里仍然可用
    assert calls["token"] == 1


# --- 形状守卫 -------------------------------------------------------------- #

def test_validate_extract_rejects_all_empty_tables():
    """坏形状必须在落盘前硬失败。

    错误数据一旦写进 wcl_data_store 就会被 load_fight 永久优先返回
    （本地优先且无新鲜度检查），所以宁可在这里炸掉。
    """
    client = WCLClient(_settings())
    empty = {view: {"data": {}} for view in TABLE_VIEWS}
    with pytest.raises(WCLApiError, match="所有关键表都返回空"):
        client.validate_extract(empty)


def test_validate_extract_catches_missing_unwrap():
    """忘了解 data 包装时，守卫必须拦下来。

    这正是最危险的静默故障：解包漏了 → 每张表都空 → 若放行就会被写进缓存
    永久生效。
    """
    client = WCLClient(_settings())
    wrapped = {view: REAL_TABLE for view in TABLE_VIEWS}
    with pytest.raises(WCLApiError, match="所有关键表都返回空"):
        client.validate_extract(wrapped)


def test_validate_extract_allows_empty_optional_tables():
    """summons / interrupts / dispels 在短战斗里天然可能为空。"""
    client = WCLClient(_settings())
    tables = {view: _unwrap_table(REAL_TABLE) for view in TABLE_VIEWS}
    for optional in ("summons", "interrupts", "dispels"):
        tables[optional] = {"entries": []}
    client.validate_extract(tables)   # 不应抛出


def test_validate_extract_rejects_unknown_aura_types():
    """光环 type 变了要报出来。

    聚合层有六处按 `applybuff` / `removedebuffstack` 这类字面量分支，
    类型名变了会静默产出空区间而不是报错。
    """
    client = WCLClient(_settings())
    tables = {view: _unwrap_table(REAL_TABLE) for view in TABLE_VIEWS}
    tables["buff_debuff_events"] = {
        "available": True,
        "events": [{"type": "somebrandnewtype"}],
        "truncated": False,
    }
    with pytest.raises(WCLApiError, match="未知的 type"):
        client.validate_extract(tables)


# --- 大秘境 ---------------------------------------------------------------- #

def test_rankings_are_suppressed_for_mythic_plus():
    """大秘境不能产出 parse 百分位。

    实测 WCL 对限时通关的大秘境给每个人返回 `rankPercent: 100` / `rank: ~1`，
    且 `bracketData` 是**钥匙层数**（12）而不是装等区间。照搬会得出
    「全员完美发挥」的错误结论——UI 上显示 100、提示词还写着「90 = 前 10%」。
    """
    result = _adapt_rankings(REAL_RANKINGS, mythic_plus=True)
    assert result["available"] is True
    assert result["entries"] == []          # 不产出误导性的 100
    assert result["mythic_plus"] is True
    assert "钥匙层数" in result["note"]

    # 团本照常
    assert len(_adapt_rankings(REAL_RANKINGS)["entries"]) == 1


def test_death_window_aliases_are_deduped_across_windows():
    """死亡窗口互相重叠，跨窗口必须全局去重。

    漏了这一步实测会从 3,322 条涨到 106,069 条。
    """
    stub = _Stub()
    _, tables, _ = asyncio.run(_client(stub).extract_fight("code", 78))
    # stub 对每个窗口回同一条事件；全局去重后只应留下 1 条
    events = tables["death_events"]["events"]
    assert len(events) == 1, f"跨窗口去重失效，得到 {len(events)} 条"


def test_mythic_plus_fields_are_requested():
    """大秘境元数据必须出现在 fights 查询里。

    迁移时只挑了 ReportFight 的一部分字段，把 keystoneLevel / dungeonPulls /
    rating 等 14 个 M+ 字段全丢了（V1 时代是原样透传、白捡的）。
    """
    from app.wcl import FIGHTS_QUERY

    for field in ("keystoneLevel", "keystoneAffixes", "rating", "countReached",
                  "countRequired", "dungeonPulls", "npcCountMap", "boundingBox",
                  "averageItemLevel", "friendlySpecs"):
        assert field in FIGHTS_QUERY, f"FIGHTS_QUERY 少了 {field}"


def test_parse_ranking_note_distinguishes_empty_reasons():
    """「空」有三种完全不同的原因，不能都当成「没有排名」。"""
    from app.aggregation import parse_ranking_note

    # 1) 迁移前的旧缓存：根本没有这个键
    assert "重新分析" in parse_ranking_note({})
    # 2) 大秘境：有键但明确不产出
    assert "大秘境" in parse_ranking_note({"rankings": {"available": True, "entries": [],
                                                      "note": "大秘境不提供 raid 那种 parse 百分位"}})
    # 3) 团本有排名：不需要说明
    assert parse_ranking_note({"rankings": {"available": True, "entries": [{"rankPercent": 50}]}}) == ""

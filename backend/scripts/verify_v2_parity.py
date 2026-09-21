#!/usr/bin/env python3
"""WCL V2 ↔ V1 形状对拍工具。

用途：迁移前确认 V2 GraphQL 的响应形状与磁盘上缓存的 V1 REST 数据是否等价；
迁移后它是**唯一**能发现 WCL 侧漂移的回归工具（这一层没有任何单元测试覆盖）。

用法：
    cd backend && ./.venv/bin/python scripts/verify_v2_parity.py [--stage fights|tables|events|all]

只读本地缓存，绝不写入 backend/storage/wcl_data。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

DATA_DIR = BACKEND_DIR / "storage" / "wcl_data"
TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"
GRAPHQL_URL = "https://www.warcraftlogs.com/api/v2/client"

# 探针用的样本：小文件先跑通，再用中等体积的验分页
SAMPLES = [
    ("bCqgAw26rDL7TxBc", 11, "杂兵战，9 键，0.26 MB"),
    ("4jwhmPNMK2pQJnbq", 13, "Boss 战 3492，53 MB"),
    ("kpjAyZTN82Bt3ChL", 78, "Boss 战，12 MB"),
]


# --------------------------------------------------------------------------- #
# token
# --------------------------------------------------------------------------- #
class TokenProvider:
    """client credentials + 内存缓存。

    用 threading.Lock 而非 asyncio.Lock —— 见方案约束 5：load_fight 内部
    asyncio.run 每次新建事件循环，模块级 asyncio 原语会在第二次调用时炸。
    """

    def __init__(self, client_id: str, client_secret: str) -> None:
        self._id = client_id
        self._secret = client_secret
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = 0.0

    def get(self, force: bool = False) -> str:
        with self._lock:
            if not force and self._token and time.time() < self._expires_at:
                return self._token
            r = httpx.post(
                TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=(self._id, self._secret),
                timeout=30,
            )
            r.raise_for_status()
            body = r.json()
            self._token = body["access_token"]
            # 留 60s 余量
            self._expires_at = time.time() + float(body.get("expires_in", 3600)) - 60
            return self._token


def load_credentials() -> tuple[str, str]:
    env = (BACKEND_DIR / ".env").read_text()
    cid = re.search(r"^WCL_CLIENT_ID=(.+)$", env, re.M)
    sec = re.search(r"^WCL_CLIENT_SECRET=(.+)$", env, re.M)
    if not cid or not sec:
        sys.exit("backend/.env 里缺 WCL_CLIENT_ID / WCL_CLIENT_SECRET")
    return cid.group(1).strip(), sec.group(1).strip()


class GQL:
    def __init__(self, tokens: TokenProvider) -> None:
        self.tokens = tokens
        self.points: list[tuple[str, float]] = []
        self.calls = 0

    def run(self, query: str, variables: dict[str, Any] | None = None, label: str = "") -> dict[str, Any]:
        self.calls += 1
        for attempt in (1, 2):
            r = httpx.post(
                GRAPHQL_URL,
                headers={"Authorization": f"Bearer {self.tokens.get(force=attempt == 2)}"},
                json={"query": query, "variables": variables or {}},
                timeout=60,
            )
            if r.status_code in (401, 403) and attempt == 1:
                continue
            if r.status_code >= 400:
                print(f"  !! HTTP {r.status_code} ({label}): {r.text[:200]}")
                return {}
            break

        body = r.json()
        if "errors" in body:
            print(f"  !! GraphQL errors ({label}): {json.dumps(body['errors'], ensure_ascii=False)[:600]}")
        rl = ((body.get("data") or {}).get("rateLimitData")) or {}
        if rl:
            self.points.append((label, float(rl.get("pointsSpentThisHour", 0))))
        return body.get("data") or {}

    def report_point_cost(self) -> None:
        print(f"\n  共 {self.calls} 次请求")
        if len(self.points) >= 2:
            base = self.points[0][1]
            print(f"  起始 pointsSpentThisHour={base:.0f}，结束={self.points[-1][1]:.0f}"
                  f"（本次探针消耗 {self.points[-1][1] - base:.0f} 点）")


RATE = "rateLimitData { limitPerHour pointsSpentThisHour pointsResetIn }"


# --------------------------------------------------------------------------- #
# 形状工具
# --------------------------------------------------------------------------- #
def key_shape(value: Any) -> str:
    """给一个 JSON 值一个紧凑的形状描述。"""
    if isinstance(value, dict):
        return "{" + ", ".join(list(value.keys())[:12]) + ("..." if len(value) > 12 else "") + "}"
    if isinstance(value, list):
        if not value:
            return "[]"
        return f"[{len(value)} x {key_shape(value[0])}]"
    return type(value).__name__


def container_keys(payload: Any) -> list[str]:
    """列出 payload 里所有"值是 list"的顶层键 —— 这正是 aggregation._entries 的做法。"""
    if not isinstance(payload, dict):
        return []
    return [k for k, v in payload.items() if isinstance(v, list)]


def elements(payload: Any) -> list:
    """按 aggregation._entries 的语义取元素（只认顶层值为 list 的键）。"""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("entries", "events", "data", "auras", "deaths", "casts", "buffs", "debuffs"):
            v = payload.get(k)
            if isinstance(v, list):
                return v
    return []


def unwrap_data(payload: Any) -> Any:
    """V2 的 table 比 V1 多包一层 `data`。

    实测：V1 是 {"entries": [...]}，V2 是 {"data": {"entries": [...]}}。
    aggregation._entries 只认顶层值为 list 的键、不递归，所以这层包装会让
    **每一张表**静默返回空列表。适配层必须解开它。
    """
    if isinstance(payload, dict):
        inner = payload.get("data")
        if isinstance(inner, dict):
            return inner
    return payload


def load_cached(report_code: str, fight_id: int) -> dict[str, Any] | None:
    path = DATA_DIR / f"{report_code}__{fight_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


# --------------------------------------------------------------------------- #
# 阶段 A：fights
# --------------------------------------------------------------------------- #
FIGHTS_QUERY = """
query($code: String!) {
  %s
  reportData { report(code: $code) {
    zone { id name }
    fights(translate: false) {
      id name encounterID originalEncounterID startTime endTime kill
      difficulty size fightPercentage bossPercentage inProgress
      gameZone { id name }
    }
  } }
}
""" % RATE


def stage_fights(gql: GQL) -> None:
    print("=" * 100)
    print("阶段 A：report.fights vs 缓存的 V1 fight dict")
    print("=" * 100)

    for code, fid, note in SAMPLES:
        cached = load_cached(code, fid)
        if cached is None:
            print(f"\n{code} #{fid}: 无缓存，跳过")
            continue

        v1 = cached["fight"]
        print(f"\n--- {code} #{fid}  ({note})")
        print(f"  V1 键: {', '.join(sorted(v1))}")

        data = gql.run(FIGHTS_QUERY, {"code": code}, label=f"fights:{code}")
        report = (data.get("reportData") or {}).get("report")
        if report is None:
            print("  !! report 为 null（未产生 GraphQL error）—— 需要显式处理这种情况")
            continue

        fights = report.get("fights") or []
        v2 = next((f for f in fights if f.get("id") == fid), None)
        if v2 is None:
            print(f"  !! V2 的 fights 列表里找不到 id={fid}（共 {len(fights)} 场）")
            continue

        print(f"  V2 键: {', '.join(sorted(v2))}")
        print(f"  report.zone = {report.get('zone')}   fight.gameZone = {v2.get('gameZone')}")
        print("  逐字段对照：")
        pairs = [
            ("id", "id"), ("name", "name"), ("start_time", "startTime"), ("end_time", "endTime"),
            ("kill", "kill"), ("difficulty", "difficulty"), ("size", "size"),
            ("boss", "encounterID"), ("originalBoss", "originalEncounterID"),
            ("fightPercentage", "fightPercentage"), ("bossPercentage", "bossPercentage"),
            ("zoneID", None), ("zoneName", None), ("zoneDifficulty", None),
        ]
        for v1k, v2k in pairs:
            lv = v1.get(v1k, "<缺失>")
            rv = v2.get(v2k, "<缺失>") if v2k else "—"
            flag = "" if v2k and str(lv) == str(rv) else "   <-- 不同"
            print(f"    {v1k:20} V1={str(lv):>22}   V2.{str(v2k):20}={str(rv):>22}{flag}")


# --------------------------------------------------------------------------- #
# 阶段 B：tables
# --------------------------------------------------------------------------- #
TABLE_QUERY = """
query($code: String!, $fightIDs: [Int]) {
  %s
  reportData { report(code: $code) {
    table(dataType: %s, fightIDs: $fightIDs, translate: false)
  } }
}
"""

TABLE_VIEWS_V1 = [
    ("damage-done", "DamageDone"), ("healing", "Healing"), ("damage-taken", "DamageTaken"),
    ("deaths", "Deaths"), ("buffs", "Buffs"), ("debuffs", "Debuffs"), ("casts", "Casts"),
    ("interrupts", "Interrupts"), ("dispels", "Dispels"), ("summons", "Summons"),
]


def stage_tables(gql: GQL, code: str, fid: int) -> None:
    print()
    print("=" * 100)
    print(f"阶段 B：report.table vs 缓存的 V1 表  ({code} #{fid})")
    print("=" * 100)

    cached = load_cached(code, fid)
    v1_tables = cached["tables"] if cached else {}

    for v1_view, v2_type in TABLE_VIEWS_V1:
        data = gql.run(
            TABLE_QUERY % (RATE, v2_type),
            {"code": code, "fightIDs": [fid]},
            label=f"table:{v2_type}",
        )
        report = (data.get("reportData") or {}).get("report") or {}
        payload = report.get("table")
        v1_payload = v1_tables.get(v1_view, {})
        v1_n = len(elements(v1_payload))

        print(f"\n--- {v1_view}  →  {v2_type}")
        wrapped = isinstance(payload, dict) and isinstance(payload.get("data"), dict)
        print(f"  V2 原始顶层: {key_shape(payload)}"
              f"{'   → 多一层 data 包装，需解开' if wrapped else ''}")
        payload = unwrap_data(payload)
        print(f"  解包后:      {key_shape(payload)}")

        v2_els = elements(payload)
        print(f"  条数: V1={v1_n}   V2={len(v2_els)}"
              f"{'   <-- 不一致' if v1_n != len(v2_els) else '   OK'}")

        v1_els = elements(v1_payload)
        if v1_els and v2_els:
            v1_keys, v2_keys = set(v1_els[0].keys()), set(v2_els[0].keys())
            print(f"  首元素键: V1 独有={sorted(v1_keys - v2_keys) or '无'}")
            print(f"            V2 独有={sorted(v2_keys - v1_keys) or '无'}")
            print(f"  V1 首元素: {json.dumps(v1_els[0], ensure_ascii=False)[:300]}")
            print(f"  V2 首元素: {json.dumps(v2_els[0], ensure_ascii=False)[:300]}")


# --------------------------------------------------------------------------- #
# 阶段 C：events
# --------------------------------------------------------------------------- #
EVENT_QUERY = """
query($code: String!, $fightIDs: [Int], $start: Float, $end: Float) {
  %s
  reportData { report(code: $code) {
    ev: events(
      dataType: %s
      fightIDs: $fightIDs
      startTime: $start
      endTime: $end
      translate: false
      %s
    ) { data nextPageTimestamp }
  } }
}
"""

# V1 的 8 条事件流 → V2 候选 dataType
EVENT_STREAMS = [
    ("cast_events", "Casts", ""),
    ("interrupt_events", "Interrupts", ""),
    ("dispel_events", "Dispels", ""),
    ("resource_events", "Resources", ""),
    ("combatant_info_events", "CombatantInfo", ""),
    ("spawn_events", "Summons", ""),
]

AURA_TYPES = (
    "applybuff", "removebuff", "refreshbuff", "applybuffstack", "removebuffstack",
    "applydebuff", "removedebuff", "refreshdebuff", "applydebuffstack", "removedebuffstack",
)


def type_histogram(events: list) -> dict[str, int]:
    hist: dict[str, int] = {}
    for e in events:
        t = e.get("type", "?")
        hist[t] = hist.get(t, 0) + 1
    return dict(sorted(hist.items(), key=lambda kv: -kv[1]))


def _event_elements(paginator: Any) -> list:
    """从 ReportEventPaginator 里取出 events 数组，并报告外层形状。"""
    if not isinstance(paginator, dict):
        return []
    inner = paginator.get("data")
    if isinstance(inner, list):
        return inner
    if isinstance(inner, dict):
        for k in ("events", "data", "entries"):
            v = inner.get(k)
            if isinstance(v, list):
                return v
    return []


def stage_events(gql: GQL, code: str, fid: int) -> None:
    print()
    print("=" * 100)
    print(f"阶段 C：report.events vs 缓存的 V1 事件流  ({code} #{fid})")
    print("=" * 100)

    cached = load_cached(code, fid)
    v1_tables = cached["tables"] if cached else {}
    v1_fight = cached["fight"] if cached else {}
    start, end = v1_fight.get("start_time"), v1_fight.get("end_time")

    # C1: 信封形状 + useActorIDs 的影响
    print("\n[C1] 信封形状 & useActorIDs")
    for uaid in ("useActorIDs: false", "useActorIDs: true"):
        data = gql.run(
            EVENT_QUERY % (RATE, "Casts", uaid),
            {"code": code, "fightIDs": [fid], "start": start, "end": end},
            label=f"events:Casts:{uaid[-4:]}",
        )
        pag = ((data.get("reportData") or {}).get("report") or {}).get("ev")
        els = _event_elements(pag)
        ids = {e.get("sourceID") for e in els}
        print(f"  {uaid:22} paginator 顶层={key_shape(pag)}"
              f"  data 是 {type(pag.get('data')).__name__ if isinstance(pag, dict) else '?'}"
              f"  n={len(els)}  sourceIDs={sorted(x for x in ids if x is not None)[:8]}")
        if els:
            print(f"    首元素: {json.dumps(els[0], ensure_ascii=False)[:260]}")

    # 表里的 actor id 集合，用来判断哪个 ID 空间对得上
    table_ids: set[int] = set()
    for view in ("damage-done", "healing", "casts", "deaths"):
        for e in elements(v1_tables.get(view, {})):
            if isinstance(e.get("id"), int):
                table_ids.add(e["id"])
    print(f"  缓存表里的 actor id 样例: {sorted(table_ids)[:8]}")

    # C2: 各事件流用对应的 dataType 取，比对条数与 type 直方图
    print("\n[C2] 各事件流（useActorIDs: true, 无 hostility）")
    for v1_key, v2_type, extra in EVENT_STREAMS:
        data = gql.run(
            EVENT_QUERY % (RATE, v2_type, "useActorIDs: true"),
            {"code": code, "fightIDs": [fid], "start": start, "end": end},
            label=f"events:{v2_type}",
        )
        pag = ((data.get("reportData") or {}).get("report") or {}).get("ev")
        els = _event_elements(pag)
        v1_els = elements(v1_tables.get(v1_key, {}))
        hist = type_histogram(els)
        print(f"\n  {v1_key} → {v2_type}")
        print(f"    条数 V1={len(v1_els)}  V2={len(els)}")
        print(f"    V2 type 直方图: {hist}")
        if v1_els:
            print(f"    V1 type 直方图: {type_histogram(v1_els)}")
            print(f"    V1 首元素: {json.dumps(v1_els[0], ensure_ascii=False)[:240]}")
        if els:
            print(f"    V2 首元素: {json.dumps(els[0], ensure_ascii=False)[:240]}")

    # C3: buff_debuff_events —— 单条 Buffs 够不够
    print("\n[C3] buff_debuff_events：单 Buffs vs 四 alias 合并")
    v1_aura = elements(v1_tables.get("buff_debuff_events", {}))
    print(f"  V1 缓存: n={len(v1_aura)}  直方图={type_histogram(v1_aura)}")
    print(f"           targetIsFriendly 分布: "
          f"{ {k: sum(1 for e in v1_aura if e.get('targetIsFriendly') is k) for k in (True, False)} }")
    for dt, host in (("Buffs", ""), ("Debuffs", "")):
        data = gql.run(
            EVENT_QUERY % (RATE, dt, "useActorIDs: true" + (f", hostilityType: {host}" if host else "")),
            {"code": code, "fightIDs": [fid], "start": start, "end": end},
            label=f"events:{dt}",
        )
        pag = ((data.get("reportData") or {}).get("report") or {}).get("ev")
        els = _event_elements(pag)
        print(f"  {dt:10} n={len(els)}  直方图={type_histogram(els)}")
        unknown = sorted(set(type_histogram(els)) - set(AURA_TYPES))
        if unknown:
            print(f"      *注意* 出现 V1 十种 aura type 之外的 type: {unknown}")


# --------------------------------------------------------------------------- #
# 阶段 D：端到端取数对拍（形状门）
# --------------------------------------------------------------------------- #
def stage_extract(code: str, fid: int) -> None:
    """跑真实的 WCLClient.extract_fight，与磁盘上的 V1 缓存逐项比对。

    这是迁移的形状门：全部对齐才允许删掉 V1 路径。
    """
    import asyncio

    from app.config import settings
    from app.wcl import TABLE_VIEWS, WCLClient, _table_entries

    print()
    print("=" * 100)
    print(f"阶段 D：extract_fight(V2) vs 磁盘上的 V1 缓存  ({code} #{fid})")
    print("=" * 100)

    cached = load_cached(code, fid)
    if cached is None:
        sys.exit(f"没有 {code}__{fid}.json 可对照")

    print("  取数中（会消耗配额，不写任何缓存）…")
    client = WCLClient(settings)
    fight, tables, warnings = asyncio.run(client.extract_fight(code, fid))

    v1_fight = cached["fight"]
    v1_tables = cached["tables"]

    # --- fight ---
    print("\n[fight]")
    ok = True
    for key in ("id", "name", "start_time", "end_time", "kill", "difficulty",
                "size", "boss", "originalBoss", "fightPercentage", "bossPercentage"):
        a, b = v1_fight.get(key), fight.get(key)
        # 「键不存在」与「值为 None」对下游等价（都是 .get() 拿到 None）
        same = a == b
        ok = ok and same
        print(f"    {key:20} V1={str(a):>24}  V2={str(b):>24}  {'OK' if same else '<-- 不同'}")
    extra = sorted(set(fight) - set(v1_fight))
    print(f"    V2 多出的键: {extra or '无'}")
    print(f"    -> fight {'一致' if ok else '存在差异'}")

    # --- 10 张表 ---
    print("\n[表]")
    for view in TABLE_VIEWS:
        a = _table_entries(v1_tables.get(view))
        b = _table_entries(tables.get(view))
        flag = "OK" if len(a) == len(b) else "<-- 条数不同"
        ka = set(a[0]) if a else set()
        kb = set(b[0]) if b else set()
        keydiff = ""
        if a and b:
            if ka - kb:
                keydiff += f"  V2 缺键={sorted(ka - kb)}"
            if kb - ka:
                keydiff += f"  V2 多键={sorted(kb - ka)}"
        print(f"    {view:16} V1={len(a):5}  V2={len(b):5}  {flag}{keydiff}")

    # --- 事件流 ---
    print("\n[事件流]")
    for stream in ("buff_debuff_events", "cast_events", "interrupt_events",
                   "dispel_events", "resource_events", "combatant_info_events",
                   "spawn_events", "death_events"):
        a = v1_tables.get(stream) or {}
        b = tables.get(stream) or {}
        na, nb = len(a.get("events") or []), len(b.get("events") or [])
        flag = "OK" if nb >= na else "<-- V2 更少"
        print(f"    {stream:24} V1={na:6}  V2={nb:6}  {flag}")

    # --- 关键字段抽查 ---
    print("\n[关键字段抽查]")
    h1 = next((e for e in (v1_tables.get("buff_debuff_events") or {}).get("events") or []
               if e.get("type") == "applybuff"), None)
    h2 = next((e for e in (tables.get("buff_debuff_events") or {}).get("events") or []
               if e.get("type") == "applybuff"), None)
    for e in (h1, h2):
        if e:
            label = "V1" if e is h1 else "V2"
            ab = e.get("ability") or {}
            print(f"    {label} applybuff: sourceID={e.get('sourceID')} "
                  f"targetID={e.get('targetID')} 技能={ab.get('name')} "
                  f"srcFriendly={e.get('sourceIsFriendly')} tgtFriendly={e.get('targetIsFriendly')}")

    if warnings:
        print(f"\n  警告 {len(warnings)} 条:")
        for w in warnings[:10]:
            print(f"    - {w}")
    else:
        print("\n  无警告")

    print("\n  形状门:", "通过" if ok else "**未通过**（见上面的差异）")


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="fights",
                    choices=["fights", "tables", "events", "extract", "all"])
    ap.add_argument("--code", default=SAMPLES[0][0])
    ap.add_argument("--fight", type=int, default=SAMPLES[0][1])
    args = ap.parse_args()

    if args.stage == "extract":
        stage_extract(args.code, args.fight)
        return

    cid, sec = load_credentials()
    print(f"client_id={cid[:8]}...  （secret 未回显）")

    gql = GQL(TokenProvider(cid, sec))

    if args.stage in ("fights", "all"):
        stage_fights(gql)
    if args.stage in ("tables", "all"):
        stage_tables(gql, args.code, args.fight)
    if args.stage in ("events", "all"):
        stage_events(gql, args.code, args.fight)
    gql.report_point_cost()


if __name__ == "__main__":
    main()

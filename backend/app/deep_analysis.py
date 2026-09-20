"""比汇总更进一步的分析：施法空窗、资源曲线、冷却使用次数。

`aggregation.summarize_tables` 给的是「谁打了多少」；这个模块回答「哪里打错了」，
三者都是纯计算、可复现的判定，不需要模型参与：

- `idle_windows` / `compare_idle_windows`：把两人 >5s 的施法空窗做时序交叉，
  剔除全团同时停的机制时段，只留个人空窗。**不剔除机制时段会把团队问题算成个人问题。**
- `resource_curve`：重建资源曲线。关键坑是**消耗资源的技能不一定产生资源事件**
  （实测奥术弹幕消耗充能但不产生 type=16 事件），只读资源事件会得到一条只涨不跌的假曲线。
- `cooldown_uses`：引导类技能在原始施法记录里一秒一跳，直接数会把 1 次使用算成 5~6 次。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping, Sequence

from .aggregation import _entries, _event_ability_name, _number

# 平砍不是玩家的技能释放决策，任何序列分析都应排除
AUTO_ATTACK_ABILITIES = {"Melee", "近战", "自动攻击"}

DEFAULT_IDLE_THRESHOLD_MS = 5000

# 奥术充能：type=16，上限 4，由这些技能产生，由奥术弹幕倾泻
ARCANE_CHARGE_TYPE = 16
ARCANE_CHARGE_MAX = 4
ARCANE_CHARGE_SPENDERS = {"奥术弹幕"}


def _timestamp(event: Mapping[str, Any]) -> float:
    return _number(event.get("timestamp"))


def cast_sequence(
    tables: Mapping[str, Any],
    actor_id: int,
    *,
    fight_start_ms: float = 0,
    pet_ids: Iterable[int] = (),
    include_pets: bool = True,
) -> list[dict[str, Any]]:
    """按时间取出一名玩家的施法序列（战斗相对毫秒）。

    用 `cast` 而非 `begincast`：一次施法会产生起手和完成两条记录，都取会重复计数。
    """
    pets = set(pet_ids)
    rows: list[dict[str, Any]] = []
    for event in _entries(tables.get("cast_events")):
        if event.get("type") != "cast" or event.get("sourceIsFriendly") is not True:
            continue
        source_id = event.get("sourceID")
        is_pet = source_id in pets
        if source_id != actor_id and not (include_pets and is_pet):
            continue
        ability = _event_ability_name(event)
        if not ability or ability in AUTO_ATTACK_ABILITIES:
            continue
        timestamp = _timestamp(event)
        if timestamp <= 0:
            continue
        row = {"t": round(timestamp - fight_start_ms), "ability": ability}
        if is_pet:
            row["pet"] = True
        rows.append(row)
    rows.sort(key=lambda row: row["t"])
    return rows


def idle_windows(
    casts: Sequence[Mapping[str, Any]],
    threshold_ms: int = DEFAULT_IDLE_THRESHOLD_MS,
) -> list[tuple[int, int]]:
    """返回施法中超过阈值的空窗，元素为 (开始ms, 结束ms)。"""
    stamps = [int(row["t"]) for row in casts]
    return [
        (start, end)
        for start, end in zip(stamps, stamps[1:])
        if end - start > threshold_ms
    ]


def compare_idle_windows(
    a_casts: Sequence[Mapping[str, Any]],
    b_casts: Sequence[Mapping[str, Any]],
    *,
    a_name: str = "A",
    b_name: str = "B",
    threshold_ms: int = DEFAULT_IDLE_THRESHOLD_MS,
) -> dict[str, Any]:
    """对比两人的个人空窗，剔除两人同时静默的时段。

    同时静默通常意味着机制让全团停手（转阶段、无法选中目标等），
    那是团队层面的问题，算到个人头上会得出相反的结论。

    判定用「有任何重叠就算共同静默」的保守规则：一个空窗只要和对方的空窗有交集，
    就整段不计入个人空窗。这会低估个人空窗，但宁可低估也不把团队问题算到个人头上。
    """
    a_windows = idle_windows(a_casts, threshold_ms)
    b_windows = idle_windows(b_casts, threshold_ms)

    def overlaps(window: tuple[int, int], others: Sequence[tuple[int, int]]) -> bool:
        start, end = window
        return any(not (end < other_start or start > other_end) for other_start, other_end in others)

    shared = [w for w in a_windows if overlaps(w, b_windows)]
    only_a = [w for w in a_windows if not overlaps(w, b_windows)]
    only_b = [w for w in b_windows if not overlaps(w, a_windows)]

    def summarize(rows: Sequence[tuple[int, int]]) -> dict[str, Any]:
        return {
            "段数": len(rows),
            "合计毫秒": sum(end - start for start, end in rows),
            "明细": [
                {"start_ms": start, "end_ms": end, "duration_ms": end - start}
                for start, end in sorted(rows, key=lambda w: w[1] - w[0], reverse=True)[:10]
            ],
        }

    return {
        "threshold_ms": threshold_ms,
        "同时静默_段数": len(shared),
        f"只有{a_name}静默": summarize(only_a),
        f"只有{b_name}静默": summarize(only_b),
        "结论": (
            f"{a_name} 个人空窗 {sum(e - s for s, e in only_a) / 1000:.0f} 秒，"
            f"{b_name} {sum(e - s for s, e in only_b) / 1000:.0f} 秒"
            f"（已剔除 {len(shared)} 段两人同时静默的机制时段）"
        ),
    }


def cooldown_uses(
    tables: Mapping[str, Any],
    actor_id: int,
    ability: str,
    *,
    fight_start_ms: float = 0,
    merge_window_ms: int = 8000,
) -> list[int]:
    """某技能的真实使用时间点。

    引导类技能（神圣赞美诗、奥术飞弹等）在日志里一秒一跳，直接数次数会把
    一次引导算成五六次。同一技能在 merge_window_ms 内的连续记录归并为一次。
    """
    stamps = sorted(
        round(_timestamp(e) - fight_start_ms)
        for e in _entries(tables.get("cast_events"))
        if e.get("sourceID") == actor_id and _event_ability_name(e) == ability
    )
    uses: list[int] = []
    for stamp in stamps:
        if not uses or stamp - uses[-1] > merge_window_ms:
            uses.append(stamp)
    return uses


def resource_curve(
    tables: Mapping[str, Any],
    actor_id: int,
    *,
    resource_type: int,
    max_value: int,
    spend_abilities: set[str],
    fight_start_ms: float = 0,
) -> dict[str, Any]:
    """重建资源曲线。

    **必须把「产生」和「消耗」两条流按时间合并** —— 消耗资源的技能不一定产生
    资源事件（实测奥术弹幕消耗全部充能，但没有 type=16 的事件），只读资源事件
    会得到一条只涨不跌、一路饱和的假曲线，进而得出「几乎每次都在满层浪费」的
    反向结论。
    """
    events: list[tuple[float, str, str, float]] = []

    for event in _entries(tables.get("resource_events")):
        if event.get("sourceID") != actor_id:
            continue
        if _number(event.get("resourceChangeType")) != resource_type:
            continue
        events.append(
            (
                _timestamp(event),
                "gain",
                _event_ability_name(event),
                _number(event.get("resourceChange")),
            )
        )

    for row in cast_sequence(tables, actor_id, fight_start_ms=fight_start_ms):
        if row["ability"] in spend_abilities:
            events.append((fight_start_ms + row["t"], "spend", row["ability"], 0.0))

    events.sort(key=lambda item: item[0])

    value = 0
    curve: list[dict[str, Any]] = []
    overcapped_by_ability: Counter[str] = Counter()
    for timestamp, kind, ability, change in events:
        before = value
        if kind == "spend":
            value = 0
        else:
            value = max(0, min(max_value, value + change))
            if before >= max_value and change > 0:
                overcapped_by_ability[ability] += 1
        curve.append(
            {
                "t": round(timestamp - fight_start_ms),
                "kind": kind,
                "ability": ability,
                "before": before,
                "after": value,
            }
        )

    spends = [point["before"] for point in curve if point["kind"] == "spend"]
    return {
        "曲线点数": len(curve),
        "消耗次数": len(spends),
        "消耗时的资源层数分布": dict(sorted(Counter(spends).items())),
        "满层时仍产生资源的次数": sum(overcapped_by_ability.values()),
        "满层浪费_按技能": dict(overcapped_by_ability.most_common()),
        "达到上限的次数": sum(1 for p in curve if p["after"] == max_value),
    }


def arcane_charge_curve(
    tables: Mapping[str, Any],
    actor_id: int,
    fight_start_ms: float = 0,
) -> dict[str, Any]:
    """奥术充能的便捷封装（奥术法师专用）。"""
    return resource_curve(
        tables,
        actor_id,
        resource_type=ARCANE_CHARGE_TYPE,
        max_value=ARCANE_CHARGE_MAX,
        spend_abilities=ARCANE_CHARGE_SPENDERS,
        fight_start_ms=fight_start_ms,
    )


def ability_counts(
    casts: Sequence[Mapping[str, Any]],
    *,
    include_pets: bool = False,
) -> dict[str, int]:
    counter: Counter[str] = Counter(
        str(row["ability"])
        for row in casts
        if include_pets or not row.get("pet")
    )
    return dict(counter.most_common())

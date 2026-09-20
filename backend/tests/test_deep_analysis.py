import pytest

from app.deep_analysis import (
    ability_counts,
    arcane_charge_curve,
    cast_sequence,
    cooldown_uses,
    compare_idle_windows,
    idle_windows,
    resource_curve,
)


def _cast(t, ability, **extra):
    return {"timestamp": t, "type": "cast", "sourceIsFriendly": True,
            "sourceID": 98, "ability": {"name": ability}, **extra}


def test_cast_sequence_skips_begincast_and_auto_attacks():
    """一次施法有起手+完成两条记录，都取会重复计数；平砍不是玩家的决策。"""
    tables = {
        "cast_events": {
            "events": [
                _cast(1000, "火球术"),
                {"timestamp": 1000, "type": "begincast", "sourceIsFriendly": True,
                 "sourceID": 98, "ability": {"name": "火球术"}},
                _cast(2000, "Melee"),
                _cast(3000, "火球术"),
            ]
        }
    }

    sequence = cast_sequence(tables, 98)

    assert [row["ability"] for row in sequence] == ["火球术", "火球术"]


def test_cast_sequence_marks_pet_casts_separately():
    tables = {"cast_events": {"events": [_cast(1000, "恶魔火焰箭", sourceID=11)]}}

    sequence = cast_sequence(tables, 98, pet_ids={11})

    assert sequence == [{"t": 1000, "ability": "恶魔火焰箭", "pet": True}]


def test_idle_windows_only_reports_gaps_over_threshold():
    casts = [{"t": 0}, {"t": 1000}, {"t": 9000}, {"t": 9500}]

    assert idle_windows(casts, threshold_ms=5000) == [(1000, 9000)]


def test_compare_idle_excludes_mechanic_downtime():
    """两人同时静默是机制造成的，算到个人头上会得出相反的结论。"""
    a = [{"t": 0}, {"t": 1000}, {"t": 30000}, {"t": 31000}]   # 1000->30000 空窗
    b = [{"t": 0}, {"t": 1000}, {"t": 30000}, {"t": 31000}]   # 同样的空窗

    result = compare_idle_windows(a, b, a_name="甲", b_name="乙")

    assert result["同时静默_段数"] == 1
    assert result["只有甲静默"]["段数"] == 0
    assert result["只有乙静默"]["段数"] == 0


def test_compare_idle_reports_player_specific_downtime():
    a = [{"t": 0}, {"t": 1000}, {"t": 30000}, {"t": 31000}]      # 甲停 29 秒
    b = [{"t": t} for t in (0, 1000, 2000, 3000, 4000)]          # 乙全程没停

    result = compare_idle_windows(a, b, a_name="甲", b_name="乙")

    assert result["只有甲静默"]["段数"] == 1
    assert result["只有甲静默"]["合计毫秒"] == 29000
    assert result["只有乙静默"]["段数"] == 0
    assert "甲 个人空窗 29 秒" in result["结论"]


def test_partial_overlap_is_treated_as_shared_not_individual():
    """只要有重叠就整段归为共同静默 —— 宁可低估个人空窗，也不把团队问题算到个人头上。"""
    a = [{"t": 0}, {"t": 1000}, {"t": 30000}]     # 甲静默 1000-30000
    b = [{"t": 0}, {"t": 20000}, {"t": 30000}]    # 乙只在 20000-30000 静默（部分重叠）

    result = compare_idle_windows(a, b, a_name="甲", b_name="乙")

    assert result["同时静默_段数"] >= 1
    assert result["只有甲静默"]["段数"] == 0


def test_cooldown_uses_merges_channel_ticks():
    """引导技能一秒一跳，直接数会把一次引导算成多次使用。"""
    tables = {
        "cast_events": {
            "events": [_cast(t, "神圣赞美诗") for t in (10_000, 11_000, 12_000, 13_000, 14_000)]
            + [_cast(200_000, "神圣赞美诗"), _cast(201_000, "神圣赞美诗")]
        }
    }

    uses = cooldown_uses(tables, 98, "神圣赞美诗", merge_window_ms=8000)

    assert uses == [10_000, 200_000]


def test_resource_curve_counts_spend_stream_separately():
    """消耗资源的技能不一定产生资源事件，只读资源事件会得到只涨不跌的假曲线。

    这里模拟奥术充能：3 次产生（+1 每次），1 次由「奥术弹幕」消耗。
    若不合并消耗流，曲线会停在 3 层且永远看不到倾泻。
    """
    tables = {
        "cast_events": {
            "events": [
                _cast(1000, "奥术冲击"),
                _cast(2000, "奥术冲击"),
                _cast(3000, "奥术冲击"),
                _cast(4000, "奥术弹幕"),
            ]
        },
        "resource_events": {
            "events": [
                {"timestamp": t, "sourceID": 98, "resourceChangeType": 16,
                 "resourceChange": 1, "ability": {"name": "奥术冲击"}}
                for t in (1000, 2000, 3000)
            ]
        },
    }

    curve = resource_curve(
        tables, 98, resource_type=16, max_value=4, spend_abilities={"奥术弹幕"}
    )

    assert curve["消耗次数"] == 1
    assert curve["消耗时的资源层数分布"] == {3: 1}
    assert curve["满层时仍产生资源的次数"] == 0


def test_resource_curve_flags_overcapping_at_max():
    tables = {
        "cast_events": {"events": []},
        "resource_events": {
            "events": [
                {"timestamp": t, "sourceID": 98, "resourceChangeType": 16,
                 "resourceChange": 1, "ability": {"name": "奥术冲击"}}
                for t in (1000, 2000, 3000, 4000, 5000, 6000)
            ]
        },
    }

    curve = resource_curve(
        tables, 98, resource_type=16, max_value=4, spend_abilities={"奥术弹幕"}
    )

    # 第 5、6 次产生时已在上限 4 层
    assert curve["满层时仍产生资源的次数"] == 2
    assert curve["满层浪费_按技能"] == {"奥术冲击": 2}
    assert curve["达到上限的次数"] >= 1


def test_arcane_charge_curve_is_wired_to_the_right_resource():
    tables = {
        "cast_events": {"events": [_cast(1000, "奥术弹幕")]},
        "resource_events": {
            "events": [
                {"timestamp": 500, "sourceID": 98, "resourceChangeType": 16,
                 "resourceChange": 4, "ability": {"name": "大法师之触"}}
            ]
        },
    }

    curve = arcane_charge_curve(tables, 98)

    assert curve["消耗次数"] == 1
    assert curve["消耗时的资源层数分布"] == {4: 1}


def test_ability_counts_can_include_or_exclude_pets():
    casts = [
        {"t": 0, "ability": "火球术"},
        {"t": 1, "ability": "火焰箭", "pet": True},
    ]

    assert ability_counts(casts) == {"火球术": 1}
    assert ability_counts(casts, include_pets=True) == {"火球术": 1, "火焰箭": 1}

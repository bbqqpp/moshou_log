"""大秘境与团本分流。

核心验收是**污染扫描**：大秘境的 DeepSeek payload 里不允许出现任何团本概念。
改造前实测 SYSTEM 里有 `阶段`×26、`boss_guide`×8、`灭团`×8，USER 里有 `Boss`×3996
（`boss_timeline` 把每只小怪的施法都标成「Boss 施放」）。
"""
from __future__ import annotations

from app.aggregation import (
    keystone_summary,
    pull_summary,
    summarize_tables,
    time_accounting,
)
from app.deepseek_agent import _build_initial_messages
from app.player_compare import build_player_payload
from app.player_report import (
    MYTHIC_PLUS_COMPARISON_SYSTEM_PROMPT,
    MYTHIC_PLUS_SINGLE_SYSTEM_PROMPT,
    SINGLE_SYSTEM_PROMPT,
)
from app.prompt import (
    MYTHIC_PLUS_SYSTEM_PROMPT,
    RAID_SYSTEM_PROMPT,
    _light_fight,
    system_prompt_for,
)
from app.wow import is_mythic_plus

# --- 样本 ------------------------------------------------------------------ #

#: 实测一场 +12 夺目谷：11 段拉怪、4 个首领段、拉怪合计 1325s / 总 1432s
M_PLUS_FIGHT = {
    "id": 74,
    "name": "夺目谷",
    "start_time": 77290841,
    "end_time": 78722881,
    "kill": True,
    "size": 5,
    "difficulty": 10,
    "keystoneLevel": 12,
    "keystoneAffixes": [10, 9, 147],
    "keystoneAffixNames": ["强韧", "残暴", "萨拉塔斯的狡诈"],
    "keystoneBonus": 1,
    "keystoneTime": 1468738,
    "rating": 371.9,
    "countReached": 721,
    "countRequired": 655,
    "averageItemLevel": 314.8,
    "boss": 12859,
    "dungeonPulls": [
        {"id": 1, "name": "孢荒喷射者", "startTime": 77302895, "endTime": 77483681,
         "kill": False, "encounterID": 0},
        {"id": 3, "name": "光明众花", "startTime": 77600937, "endTime": 77750543,
         "kill": True, "encounterID": 3199},
        {"id": 4, "name": "发光的棘喉兽", "startTime": 77756913, "endTime": 77846724,
         "kill": False, "encounterID": 0},
    ],
}

RAID_FIGHT = {
    "id": 3,
    "name": "陵寝哨兵",
    "start_time": 1132737,
    "end_time": 1603828,
    "kill": True,
    "size": 20,
    "difficulty": 4,
    "boss": 3445,
    "keystoneLevel": None,
    "phaseTransitions": [{"id": 1, "startTime": 1132737}],
}

RAID_TERMS = (
    "团本", "团队", "BOSS", "Boss", "阶段", "灭团", "攻略",
    "boss_guide", "wipe_reasons", "checklist", "mechanic_hits",
    "腐蚀浪潮", "厄鳞外壳", "parse", "raid", "击杀",
)


def _tables(deaths: bool = True) -> dict:
    """最小可用的 tables：够 summarize_tables 跑出各类键。"""
    return {
        "damage-done": {"entries": [{"name": "甲", "id": 1, "total": 1000, "type": "Mage"}]},
        "healing": {"entries": [{"name": "乙", "id": 2, "total": 500, "type": "Priest"}]},
        "damage-taken": {"entries": [{"name": "甲", "id": 1, "total": 200, "type": "Mage"}]},
        "deaths": {"entries": (
            [{"name": "丙", "id": 3, "timestamp": 77650000, "type": "Mage",
              "killingBlow": {"name": "重击"}}] if deaths else []
        )},
        "casts": {"entries": []},
        "buffs": {"auras": []},
        "debuffs": {"auras": []},
        "cast_events": {"available": True, "events": [], "truncated": False},
        "buff_debuff_events": {"available": True, "events": [], "truncated": False},
    }


def _payload_text(fight: dict, summary: dict) -> str:
    messages = _build_initial_messages("AbCd1234EfGh5678", 74, fight, summary)
    return messages[0]["content"] + messages[1]["content"]


# --- 判定 ------------------------------------------------------------------ #

def test_is_mythic_plus_uses_keystone_level():
    assert is_mythic_plus(M_PLUS_FIGHT) is True
    assert is_mythic_plus(RAID_FIGHT) is False
    assert is_mythic_plus({}) is False
    assert is_mythic_plus(None) is False
    # 团本的 keystoneLevel 是 null，不能因为「键存在」就判成副本
    assert is_mythic_plus({"keystoneLevel": None}) is False


# --- 污染扫描（核心验收）---------------------------------------------------- #

def test_mythic_plus_payload_has_zero_raid_terms():
    """大秘境的整个分析上下文里不允许出现任何团本概念。

    这是本次改造的核心验收。改造前实测：SYSTEM 里 `阶段`×26、`boss_guide`×8、
    `灭团`×8，USER 里 `Boss`×3996。改完必须是 0。
    """
    summary = summarize_tables(
        _tables(), duration_ms=1432040, fight_start_ms=77290841, fight=M_PLUS_FIGHT
    )
    text = _payload_text(M_PLUS_FIGHT, summary)

    found = {term: text.count(term) for term in RAID_TERMS if text.count(term)}
    assert found == {}, f"大秘境 payload 里仍有团本字样：{found}"


def test_raid_payload_still_contains_raid_terms():
    """反向断言：团本那套不能被动坏。

    没有这条，把团本也改成大秘境提示词（或把两套写成一模一样）测试照样全绿。
    """
    summary = summarize_tables(
        _tables(), duration_ms=471091, fight_start_ms=1132737, fight=RAID_FIGHT
    )
    text = _payload_text(RAID_FIGHT, summary)

    for term in ("团本", "阶段", "灭团", "boss_guide"):
        assert term in text, f"团本 payload 里丢了「{term}」，团本路径被改坏了"


def test_mythic_plus_prompt_is_self_contained():
    """M+ 提示词必须是自洽的，不能是团本提示词打了补丁。

    禁止句（"不要提阶段"）会把团本概念引进 M+ 的语义空间，所以这里断言
    M+ 提示词里**根本不出现**这些词。
    """
    for term in ("团本", "阶段", "灭团", "boss_guide", "攻略", "parse"):
        assert term not in MYTHIC_PLUS_SYSTEM_PROMPT, f"M+ 提示词里出现了「{term}」"


def test_mythic_plus_player_prompts_are_self_contained():
    for prompt in (MYTHIC_PLUS_SINGLE_SYSTEM_PROMPT, MYTHIC_PLUS_COMPARISON_SYSTEM_PROMPT):
        for term in ("团本", "团队", "阶段", "灭团", "parse", "击杀"):
            assert term not in prompt, f"M+ 玩家提示词里出现了「{term}」"


# --- 提示词选路 ------------------------------------------------------------- #

def test_system_prompt_for_picks_by_fight_type():
    assert system_prompt_for(M_PLUS_FIGHT) == MYTHIC_PLUS_SYSTEM_PROMPT
    assert system_prompt_for(RAID_FIGHT) == RAID_SYSTEM_PROMPT
    assert system_prompt_for({"keystoneLevel": None}) == RAID_SYSTEM_PROMPT
    assert system_prompt_for(None) == RAID_SYSTEM_PROMPT


def test_light_fight_drops_raid_framed_fields_for_mythic_plus():
    """`boss` / `fight_percentage` / `difficulty` 在副本里语义是错的或空的。"""
    light = _light_fight(M_PLUS_FIGHT)
    assert light["keystone_level"] == 12
    assert light["affixes"] == ["强韧", "残暴", "萨拉塔斯的狡诈"]
    assert light["timed"] is True
    assert light["pull_count"] == 3
    for key in ("boss", "fight_percentage", "difficulty", "kill"):
        assert key not in light, f"大秘境不该带 {key}"

    raid_light = _light_fight(RAID_FIGHT)
    assert raid_light["boss"] == 3445
    assert "keystone_level" not in raid_light


# --- 聚合层 ---------------------------------------------------------------- #

def test_mythic_plus_summary_omits_raid_only_keys():
    """团本专有的键**整个不产出**，而不是产出空壳。

    空壳会被读成「本场没有 X」的假事实 —— 比如 mechanic_hits 全空意味着
    「没人被机制打中」，而真相是那套判定根本不适用于副本。
    """
    summary = summarize_tables(
        _tables(), duration_ms=1432040, fight_start_ms=77290841, fight=M_PLUS_FIGHT
    )
    for key in ("mechanic_hits", "boss_timeline", "phases", "parse_ranking"):
        assert key not in summary, f"大秘境 summary 不该有 {key}"

    assert summary["keystone"]["level"] == 12
    assert summary["pull_summary"]
    assert summary["time_accounting"]

    # 团本照旧
    raid = summarize_tables(
        _tables(), duration_ms=471091, fight_start_ms=1132737, fight=RAID_FIGHT
    )
    for key in ("mechanic_hits", "boss_timeline", "phases"):
        assert key in raid, f"团本 summary 丢了 {key}"


def test_pull_summary_marks_boss_pulls_by_encounter_id():
    """BOSS 段的判据是 `encounterID != 0`。

    **不能用 `kill`** —— 实测小怪段的 kill 恒为 false（哪怕顺利清掉），
    只有首领段的 kill 才有意义。
    """
    pulls = pull_summary(M_PLUS_FIGHT, _tables())
    assert [p["index"] for p in pulls] == [1, 2, 3]
    assert [p["is_boss"] for p in pulls] == [False, True, False]
    # 死亡时间戳放在第 2 段（光明众花）的窗口内，应当归到第 2 段而不是第 1 段
    assert pulls[1]["death_count"] == 1
    assert pulls[1]["deaths"] == ["丙"]
    assert pulls[0]["death_count"] == 0


def test_time_accounting_splits_combat_and_downtime():
    accounting = time_accounting(M_PLUS_FIGHT)

    assert accounting["total_ms"] == 1432040
    expected_pull = (77483681 - 77302895) + (77750543 - 77600937) + (77846724 - 77756913)
    assert accounting["pull_ms"] == expected_pull
    assert accounting["downtime_ms"] == 1432040 - expected_pull
    assert accounting["pull_count"] == 3
    assert accounting["slowest_pulls"][0]["index"] == 1     # 180s 最长


def test_keystone_summary_reads_timed_from_bonus():
    """`keystoneBonus` 0 = 超时，>=1 = 限时（+1/+2/+3）。"""
    assert keystone_summary(M_PLUS_FIGHT)["timed"] is True
    assert keystone_summary({**M_PLUS_FIGHT, "keystoneBonus": 0})["timed"] is False
    # 团本没有钥匙信息
    assert keystone_summary(RAID_FIGHT) == {}


# --- 玩家报告 -------------------------------------------------------------- #

def test_player_payload_carries_mythic_plus_flag_and_context():
    data = {"fight": M_PLUS_FIGHT, "tables": _tables()}
    payload = build_player_payload(data, "甲")

    assert payload["mythic_plus"] is True
    assert payload["keystone"]["level"] == 12
    assert payload["pull_summary"]
    # 大秘境不该带团本的阶段划分
    assert "phases" not in payload

    raid_payload = build_player_payload({"fight": RAID_FIGHT, "tables": _tables()}, "甲")
    assert raid_payload["mythic_plus"] is False
    assert "keystone" not in raid_payload


def test_skill_import_targets_still_exist():
    """`.claude/skills/wcl-rotation-compare/scripts/ask.py:32` 按名字 import 团本那份。

    这条用例之前是空转的（断言一个常量真值 + 拿常量名字符串去匹配提示词正文），
    改名删名都照样绿 —— 而它本该拦住的正是那个 import 断掉的风险。
    """
    from app import player_report

    for name in ("SINGLE_SYSTEM_PROMPT", "COMPARISON_SYSTEM_PROMPT"):
        assert hasattr(player_report, name), f"skill 脚本 import 的 {name} 不见了"


def test_comparison_payload_lifts_mythic_plus_to_the_top_level():
    """双人对比的 payload 结构是 `{a, b, fairness, ...}`，标识必须提到顶层。

    `run_player_report` 只读顶层 —— 不提升的话双人对比会**静默**退回团本提示词。
    """
    from app.player_report import build_comparison_payload

    data = {"fight": M_PLUS_FIGHT, "tables": _tables()}
    payload = build_comparison_payload(data, "甲", data, "乙")

    assert payload["mythic_plus"] is True
    assert payload["a"]["mythic_plus"] is True

    raid = {"fight": RAID_FIGHT, "tables": _tables()}
    assert build_comparison_payload(raid, "甲", raid, "乙")["mythic_plus"] is False


def test_pull_summary_reads_v1_key_names_too():
    """V1 和 V2 的 `dungeonPulls` 键名不同，两种都要认。

    V1：`start_time` / `end_time` / `boss`；V2：`startTime` / `endTime` / `encounterID`。
    磁盘上 33 份历史缓存全是 V1 形状 —— 只认 V2 键名会让它们的分段时长全变 0、
    首领段全判成小怪（实测过：11 段全变 0 秒 0 首领）。
    """
    v1_fight = {
        **M_PLUS_FIGHT,
        "dungeonPulls": [
            {"id": 1, "name": "孢荒喷射者", "start_time": 77302895, "end_time": 77483681,
             "boss": 0, "kill": False},
            {"id": 3, "name": "光明众花", "start_time": 77600937, "end_time": 77750543,
             "boss": 3199, "kill": True},
        ],
    }
    pulls = pull_summary(v1_fight, _tables())

    assert pulls[0]["duration_ms"] == 77483681 - 77302895
    assert pulls[1]["is_boss"] is True
    assert pulls[0]["is_boss"] is False
    assert time_accounting(v1_fight)["pull_ms"] == (77483681 - 77302895) + (77750543 - 77600937)


def test_keystone_timed_is_none_when_unknown():
    """V1 历史缓存没有 `keystoneBonus`，`timed` 必须是 None（未知）。

    把未知当成 False 会在前端显示成「超时」—— 把一场限时的副本说成黑掉的。
    """
    v1_fight = {k: v for k, v in M_PLUS_FIGHT.items() if k != "keystoneBonus"}
    assert keystone_summary(v1_fight)["timed"] is None
    assert keystone_summary({**v1_fight, "keystoneBonus": 0})["timed"] is False


# --- 审查发现的回归 -------------------------------------------------------- #

def test_light_fight_agrees_with_summary_for_v1_cache():
    """`_light_fight` 与 `summary.keystone` 必须是同一份事实。

    实测过的 bug：`_light_fight` 自己重读 V2 键名，于是同一份 payload 里
    `fight.affixes` 是 `[]`、`summary.keystone.affixes` 是三个中文词缀；
    `fight.timed` 是 `false`、`summary.keystone.timed` 是 `null`。
    模型同时看到两组互相矛盾的值，而提示词偏偏写了「如果 timed 为 false…」。
    """
    v1_fight = {
        k: v for k, v in M_PLUS_FIGHT.items()
        if k not in ("keystoneAffixNames", "keystoneBonus", "keystoneTime")
    }
    v1_fight["affixes"] = [10, 9, 147]
    v1_fight["completionTime"] = 1468738

    light = _light_fight(v1_fight)
    key = keystone_summary(v1_fight)

    assert light["affixes"] == key["affixes"] != []
    assert light["timed"] == key["timed"] is None
    assert light["completion_ms"] == key["completion_ms"] == 1468738
    assert light["keystone_level"] == key["level"] == 12


def test_completion_ms_falls_back_to_v1_key():
    """V1 缓存的通关用时叫 `completionTime`，值就在里面。"""
    assert keystone_summary({**M_PLUS_FIGHT, "keystoneTime": None,
                             "completionTime": 1468738})["completion_ms"] == 1468738
    assert keystone_summary({**M_PLUS_FIGHT, "keystoneTime": 100})["completion_ms"] == 100


def test_both_summary_branches_share_the_same_common_keys():
    """两个分支各自手写同一份「公共键」字典，没有约束就会漂移。

    实测已经漂过一次：`parse_ranking_note` 只加进了团本那份，大秘境那份漏了，
    前端拿到 `undefined` 后静默走了团本兜底文案（「灭团场次没有 parse 排名」
    出现在一场限时通关的大秘境上）。这条断言保证下次加共享键时两边同步。
    """
    m_plus = summarize_tables(
        _tables(), duration_ms=1432040, fight_start_ms=77290841, fight=M_PLUS_FIGHT
    )
    raid = summarize_tables(
        _tables(), duration_ms=471091, fight_start_ms=1132737, fight=RAID_FIGHT
    )

    raid_only = {"mechanic_hits", "boss_timeline", "phases", "parse_ranking", "parse_ranking_note"}
    m_plus_only = {"keystone", "pull_summary", "time_accounting"}

    shared_raid = set(raid) - raid_only
    shared_m_plus = set(m_plus) - m_plus_only

    assert shared_raid == shared_m_plus, (
        f"两个分支的公共键漂移了 —— 只在团本有：{shared_raid - shared_m_plus}；"
        f"只在大秘境有：{shared_m_plus - shared_raid}"
    )

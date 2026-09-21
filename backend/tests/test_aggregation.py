from app.aggregation import summarize_tables


def test_summarizes_rankings_and_player_behavior_tables():
    tables = {
        "damage-done": {
            "entries": [
                {"name": "Mage", "type": "Mage", "total": 2000},
                {"name": "Rogue", "type": "Rogue", "total": 1000},
            ]
        },
        "healing": {
            "entries": [
                {"name": "Priest", "type": "Priest", "total": 3000},
            ]
        },
        "damage-taken": {
            "entries": [
                {"name": "Tank", "type": "Warrior", "total": 4000},
            ]
        },
        "deaths": {
            "entries": [
                {
                    "name": "Rogue",
                    "id": 7,
                    # **真实形状**：`killingBlow.name` 是致死**技能**名，且
                    # 表里**没有击杀者字段**。原来这里的 fixture 写的是
                    # `{"name": "Boss", "ability": {"name": "Cleave"}}` ——
                    # 那个形状在 36 份真实缓存里出现 0 次，测试把错误语义
                    # 固化成了规范（断言 `source == "Boss"`）。
                    "killingBlow": {
                        "name": "顺劈斩", "guid": 1234, "type": 1, "abilityIcon": "x.jpg"
                    },
                    "damage": {
                        "total": 5000,
                        "abilities": [
                            {"guid": 1234, "name": "顺劈斩", "total": 4200},
                            {"guid": 1, "name": "Melee", "total": 800},
                        ],
                    },
                    "type": "Demonology Warlock",
                    "timestamp": 1000,
                }
            ]
        },
        "buffs": {
            "entries": [
                {"name": "Arcane Intellect", "sourceName": "Mage", "total": 1}
            ]
        },
        "debuffs": {
            "entries": [
                {"name": "Slow", "sourceName": "Boss", "total": 1}
            ]
        },
        "casts": {
            "entries": [
                {"name": "Mage", "abilityName": "Fireball", "timestamp": 2000}
            ]
        },
    }

    result = summarize_tables(tables)

    assert result["damage_done"][0]["actor"] == "Mage"
    assert result["damage_done"][0]["amount"] == 2000
    assert result["healing_done"][0]["actor"] == "Priest"
    assert result["damage_taken"][0]["actor"] == "Tank"
    assert result["death_count"] == 1
    assert result["deaths"][0]["player"] == "Rogue"
    # 致死技能，不是击杀者 —— deaths 表里根本没有击杀者字段
    assert result["deaths"][0]["killing_ability"] == "顺劈斩"
    assert result["deaths"][0]["damage_breakdown"][0]["ability"] == "顺劈斩"
    assert "source" not in result["deaths"][0], "不要再假装知道击杀者"
    assert result["deaths"][0]["role_label"] == "恶魔学识术士"
    assert len(result["buffs"]) == 1
    assert len(result["debuffs"]) == 1
    assert len(result["casts"]) == 1


def test_records_unavailable_table_errors():
    tables = {
        "damage-done": {"entries": []},
        "healing": {"entries": []},
        "damage-taken": {"entries": []},
        "deaths": {"entries": []},
        "buffs": {"available": False, "error": "503"},
        "debuffs": {"entries": []},
        "casts": {"entries": []},
    }

    result = summarize_tables(tables)

    assert result["table_errors"][0]["view"] == "buffs"


def test_mechanic_hit_summary_detects_corrosive_wave_and_egg_carrier_overlap():
    tables = {
        "damage-done": {
            "entries": [
                {"id": 98, "name": "甲", "type": "mage", "total": 10},
                {"id": 100, "name": "乙", "type": "druid", "total": 10},
                {"id": 101, "name": "丙", "type": "paladin", "total": 10},
            ]
        },
        "buff_debuff_events": {
            "events": [
                {
                    "timestamp": 1000,
                    "type": "applydebuff",
                    "sourceID": 369,
                    "targetID": 98,
                    "targetIsFriendly": True,
                    "ability": {"name": "腐蚀浪潮", "guid": 1292403},
                },
                {
                    "timestamp": 500,
                    "type": "applydebuff",
                    "sourceID": 369,
                    "targetID": 100,
                    "targetIsFriendly": True,
                    "ability": {"name": "厄鳞外壳", "guid": 1300312},
                },
                {
                    "timestamp": 2000,
                    "type": "applydebuff",
                    "sourceID": 369,
                    "targetID": 100,
                    "targetIsFriendly": True,
                    "ability": {"name": "腐蚀浪潮", "guid": 1292403},
                },
                {
                    "timestamp": 3000,
                    "type": "removedebuff",
                    "sourceID": 369,
                    "targetID": 100,
                    "targetIsFriendly": True,
                    "ability": {"name": "厄鳞外壳", "guid": 1300312},
                },
            ]
        },
    }

    result = summarize_tables(tables, duration_ms=5000, fight_start_ms=0)

    mechanic_hits = result["mechanic_hits"]
    assert "甲" in {hit["player"] for hit in mechanic_hits["wave_hits"]}
    assert "乙" in {hit["player"] for hit in mechanic_hits["wave_hits"]}
    assert [hit["player"] for hit in mechanic_hits["egg_carrier_wave_hits"]] == ["乙"]
    assert mechanic_hits["egg_carriers_seen"][0]["player"] == "乙"


def _tables_with_events(events, deaths=None):
    return {
        "damage-done": {
            "entries": [{"id": 98, "name": "甲", "type": "mage", "total": 10}]
        },
        "deaths": {"entries": deaths or []},
        "buff_debuff_events": {"events": events},
    }


def test_mechanic_hit_summary_reports_hatch_casts():
    tables = _tables_with_events([])
    tables["cast_events"] = {
        "events": [
            {
                "timestamp": 1500,
                "type": "begincast",
                "sourceID": 500,
                "ability": {"name": "孵化厄运", "guid": 1306862},
            },
            {
                "timestamp": 1600,
                "type": "cast",
                "sourceID": 98,
                "ability": {"name": "火球术", "guid": 133},
            },
        ]
    }

    result = summarize_tables(tables, duration_ms=5000, fight_start_ms=0)

    hatch = result["mechanic_hits"]["hatch_events"]
    assert len(hatch) == 1
    assert hatch[0]["ability"] == "孵化厄运"
    assert hatch[0]["guid"] == 1306862
    assert hatch[0]["relative_ms"] == 1500


def test_mechanic_hit_summary_tracks_deadly_aura_stacks():
    tables = _tables_with_events(
        [
            {
                "timestamp": 1000,
                "type": "applydebuff",
                "sourceID": 369,
                "targetID": 98,
                "targetIsFriendly": True,
                "ability": {"name": "剧毒撕咬", "guid": 1287036},
                "stack": 1,
            },
            {
                "timestamp": 2000,
                "type": "applydebuffstack",
                "sourceID": 369,
                "targetID": 98,
                "targetIsFriendly": True,
                "ability": {"name": "剧毒撕咬", "guid": 1287036},
                "stack": 7,
            },
            {
                "timestamp": 3000,
                "type": "removedebuffstack",
                "sourceID": 369,
                "targetID": 98,
                "targetIsFriendly": True,
                "ability": {"name": "剧毒撕咬", "guid": 1287036},
            },
        ]
    )

    result = summarize_tables(tables, duration_ms=5000, fight_start_ms=0)

    peaks = result["mechanic_hits"]["deadly_aura_peaks"]
    assert len(peaks) == 1
    assert peaks[0]["player"] == "甲"
    assert peaks[0]["ability"] == "剧毒撕咬"
    assert peaks[0]["max_stacks"] == 7


def test_death_counts_debuff_stripped_by_death_itself():
    """死亡会剥离光环：减益在死亡前几毫秒结束，仍必须算作「死时带着」。"""
    tables = _tables_with_events(
        [
            {
                "timestamp": 5000,
                "type": "applydebuff",
                "sourceID": 369,
                "targetID": 98,
                "targetIsFriendly": True,
                "ability": {"name": "腐臭薄膜", "guid": 1301268},
            },
            {
                "timestamp": 9999,
                "type": "removedebuff",
                "sourceID": 369,
                "targetID": 98,
                "targetIsFriendly": True,
                "ability": {"name": "腐臭薄膜", "guid": 1301268},
            },
        ],
        deaths=[{"id": 98, "name": "甲", "timestamp": 10000}],
    )

    result = summarize_tables(tables, duration_ms=20000, fight_start_ms=0)

    rows = result["mechanic_hits"]["deaths_overlapping_mechanics"]
    assert [row["player"] for row in rows] == ["甲"]
    assert rows[0]["ability"] == "腐臭薄膜"
    assert rows[0]["aura_removed_before_death_ms"] == 1
    assert rows[0]["death_relative_ms"] == 10000


def test_death_ignores_debuff_that_expired_long_before():
    """真正的提前消失不该被算进来（容差之外的）：否则判定就失去意义。"""
    tables = _tables_with_events(
        [
            {
                "timestamp": 1000,
                "type": "applydebuff",
                "sourceID": 369,
                "targetID": 98,
                "targetIsFriendly": True,
                "ability": {"name": "腐臭薄膜", "guid": 1301268},
            },
            {
                "timestamp": 2000,
                "type": "removedebuff",
                "sourceID": 369,
                "targetID": 98,
                "targetIsFriendly": True,
                "ability": {"name": "腐臭薄膜", "guid": 1301268},
            },
        ],
        deaths=[{"id": 98, "name": "甲", "timestamp": 10000}],
    )

    result = summarize_tables(tables, duration_ms=20000, fight_start_ms=0)

    assert result["mechanic_hits"]["deaths_overlapping_mechanics"] == []


def test_combatant_info_spec_id_overrides_the_class_only_label():
    """specID 是客户端上报的，比解析 type 字符串可靠，必须覆盖旧结果。"""
    tables = {
        "damage-done": {
            "entries": [{"id": 98, "name": "甲", "type": "Shaman", "total": 10}]
        },
        "combatant_info_events": {"events": [{"sourceID": 98, "specID": 264}]},
    }

    result = summarize_tables(tables)

    assert result["damage_done"][0]["role_label"] == "恢复萨满祭司"
    assert result["damage_done"][0]["spec"] == "恢复"


def test_unknown_spec_id_keeps_the_class_only_label():
    tables = {
        "damage-done": {
            "entries": [{"id": 98, "name": "甲", "type": "Shaman", "total": 10}]
        },
        "combatant_info_events": {"events": [{"sourceID": 98, "specID": 123456}]},
    }

    result = summarize_tables(tables)

    assert result["damage_done"][0]["role_label"] == "萨满祭司"
    assert result["damage_done"][0]["spec"] == ""


def test_spec_id_reaches_death_rows_too():
    tables = {
        "damage-done": {
            "entries": [{"id": 98, "name": "甲", "type": "Shaman", "total": 10}]
        },
        "deaths": {"entries": [{"id": 98, "name": "甲", "timestamp": 1000}]},
        "combatant_info_events": {"events": [{"sourceID": 98, "specID": 262}]},
    }

    result = summarize_tables(tables, duration_ms=5000, fight_start_ms=0)

    assert result["deaths"][0]["role_label"] == "元素萨满祭司"


def test_death_overlap_covers_juxie_siyao_not_just_the_original_pair():
    """剧毒撕咬 曾在判定集合外：原实现只查腐臭薄膜/厄鳞外壳，系统性漏报。"""
    tables = _tables_with_events(
        [
            {
                "timestamp": 5000,
                "type": "applydebuff",
                "sourceID": 369,
                "targetID": 98,
                "targetIsFriendly": True,
                "ability": {"name": "剧毒撕咬", "guid": 1287036},
            },
        ],
        deaths=[{"id": 98, "name": "甲", "timestamp": 9000}],
    )

    result = summarize_tables(tables, duration_ms=20000, fight_start_ms=0)

    rows = result["mechanic_hits"]["deaths_overlapping_mechanics"]
    assert [row["ability"] for row in rows] == ["剧毒撕咬"]
    assert rows[0]["aura_removed_before_death_ms"] is None


# --- V2 独有数据的聚合 ------------------------------------------------------ #

def test_event_count_ignores_v2_derived_tables():
    """`event_count` 是前端「事件数量」那个卡片，只该数战斗事件。

    `rankings` / `survivability` / `player_details` 也是 `{"entries": [...]}` 形状、
    也能被 `_entries` 读到，但它们不是事件。混进去会让这个数字莫名其妙地变大。
    """
    tables = {
        "damage-done": {"entries": [{"name": "A", "total": 1}]},
        "cast_events": {"available": True, "events": [{"type": "cast"}], "truncated": False},
        "rankings": {"available": True, "entries": [{"name": "A"}, {"name": "B"}]},
        "survivability": {"available": True, "entries": [{"name": "A"}]},
        "player_details": {"available": True, "entries": [{"name": "A"}]},
    }
    summary = summarize_tables(tables)
    assert summary["event_count"] == 2      # 只有 damage-done 的 1 条 + cast_events 的 1 条


def test_phase_timeline_pairs_transitions_with_names():
    """阶段数据在 WCL 那边拆成两处：`phaseTransitions` 给时间、`phases` 给名字。"""
    fight = {
        "start_time": 1000,
        "end_time": 6000,
        "phaseTransitions": [
            {"id": 1, "startTime": 1000},
            {"id": 2, "startTime": 3000},
            {"id": 1, "startTime": 5000},
        ],
    }
    tables = {
        "phases": {
            "available": True,
            "phases": [
                {"id": 1, "name": "Stage One", "isIntermission": False},
                {"id": 2, "name": "Intermission", "isIntermission": True},
            ],
        }
    }

    segments = summarize_tables(tables, duration_ms=5000, fight_start_ms=1000,
                                fight={"start_time": 1000, "end_time": 6000,
                                       "phaseTransitions": fight["phaseTransitions"]},
                                )["phases"]
    assert [s["name"] for s in segments] == ["Stage One", "Intermission", "Stage One"]
    assert [s["start_ms"] for s in segments] == [0, 2000, 4000]
    assert [s["end_ms"] for s in segments] == [2000, 4000, 5000]
    assert segments[1]["is_intermission"] is True
    # 同一个 phase_id 出现两次要各算一段 —— 转阶段型 BOSS 会反复进出
    assert segments[0]["phase_id"] == segments[2]["phase_id"] == 1


def test_phase_timeline_empty_without_data():
    """V1 历史缓存没有阶段定义，要返回空列表而不是抛错。"""
    summary = summarize_tables({"damage-done": {"entries": [{"name": "A"}]}}, fight={})
    assert summary["phases"] == []


def test_parse_ranking_sorted_and_empty_on_wipe():
    tables = {
        "rankings": {
            "available": True,
            "entries": [
                {"name": "Low", "rankPercent": 12, "role": "dps"},
                {"name": "High", "rankPercent": 88, "role": "dps"},
            ],
        }
    }
    rows = summarize_tables(tables)["parse_ranking"]
    assert [r["player"] for r in rows] == ["High", "Low"]

    # 灭团场次 WCL 不给排名 —— 空列表，不是错误
    wipe = {"rankings": {"available": True, "entries": [],
                         "note": "这场没有 parse 排名（灭团场次不产生排名）"}}
    assert summarize_tables(wipe)["parse_ranking"] == []


def test_phase_timeline_reads_v1_key_too():
    """V1 的历史缓存把阶段时间放在 `phases` 下，V2 放在 `phaseTransitions` 下。

    形状相同、键名不同。只读后者会让所有历史战斗的阶段时间白白丢掉。
    名字仍然缺失（V1 没有 report.phases），前端会退化成「第 N 阶段」。
    """
    fight = {
        "start_time": 0,
        "end_time": 4000,
        "phases": [{"id": 1, "startTime": 0}, {"id": 2, "startTime": 2000}],
    }
    summary = summarize_tables({}, duration_ms=4000, fight_start_ms=0, fight=fight)
    segments = summary["phases"]
    assert len(segments) == 2
    assert segments[0]["phase_id"] == 1
    assert segments[1]["phase_id"] == 2
    assert segments[0]["name"] is None      # V1 没有阶段名字


def test_first_seen_is_the_earliest_band_not_the_last():
    """多段 band 的光环，`first_seen_ms` 必须取**最早**那段。

    原来循环里写的是直接赋值，于是 first_seen 恒等于最后一段的起点 ——
    实测全库 35 份缓存里 2341 行的这个字段都是错的
    （「水之护盾」三段起于 0/447982/449613，报出来是 449613）。
    这个字段只喂给 DeepSeek，用来推断大招/药水时机，前端不显示，纯静默。
    """
    tables = {
        "buffs": {
            "auras": [
                {
                    "name": "水之护盾",
                    "guid": 52127,
                    "totalUptime": 600,
                    "totalUses": 3,
                    "bands": [
                        {"startTime": 1000, "endTime": 2000},
                        {"startTime": 5000, "endTime": 6000},
                        {"startTime": 9000, "endTime": 9500},
                    ],
                }
            ]
        }
    }
    rows = summarize_tables(tables, duration_ms=10000, fight_start_ms=1000)["buffs"]
    assert rows[0]["first_seen_ms"] == 0        # 1000 - 1000
    assert rows[0]["last_seen_ms"] == 8500      # 9500 - 1000


def test_self_healing_boss_casts_are_not_dropped():
    """会自我治疗的 BOSS 会出现在 `healing` 表里，于是被误判成「友方」。

    `_boss_cast_timeline` 原来无条件用 actor 目录过滤友方施法，
    实测一场团本的 528 次敌方施法被丢掉 311 次 —— 丢的正是两个真 BOSS
    （祖尔加 177 次、妖术领主玛拉卡斯 134 次，后者靠 988 万自我治疗进的
    healing 表）。剩下的每行都写着「Boss 施放」，但全是无名小怪。
    """
    tables = {
        # BOSS 出现在 healing 表里（自我治疗），所以会被目录当成友方
        "healing": {"entries": [{"name": "妖术领主玛拉卡斯", "id": 357, "total": 9880000}]},
        "damage-done": {"entries": [{"name": "某玩家", "id": 100, "total": 5000}]},
        "cast_events": {
            "available": True,
            "events": [
                # 真 BOSS：事件自带 sourceIsFriendly=False，必须以它为准
                {"timestamp": 2000, "type": "cast", "sourceID": 357,
                 "sourceIsFriendly": False, "ability": {"name": "恐惧箭", "guid": 1}},
                # 友方玩家：要过滤掉
                {"timestamp": 3000, "type": "cast", "sourceID": 100,
                 "sourceIsFriendly": True, "ability": {"name": "火球术", "guid": 2}},
            ],
            "truncated": False,
        },
    }
    timeline = summarize_tables(tables, duration_ms=10000, fight_start_ms=1000)["boss_timeline"]

    abilities = [row["ability"] for row in timeline]
    assert "恐惧箭" in abilities, "自我治疗的 BOSS 施法被丢掉了"
    assert "火球术" not in abilities, "友方施法不该进 boss_timeline"
    # 能查到名字就用名字 —— 全部写成「Boss」会让模型以为只有一个施法者
    assert timeline[0]["source"] == "妖术领主玛拉卡斯"


def test_death_without_killing_blow_falls_back_to_the_top_damage_ability():
    """实测 5 条死亡里有 2 条没有 `killingBlow`。

    这时退回 `damage.abilities[0]`（WCL 自己按技能统计的承伤构成首位），
    而不是一路回退到 `entry["type"]` —— 那是**职业名**，会写出
    「赵小帅死亡，致死技能：Shaman」这种荒谬结论。
    """
    tables = {
        "deaths": {
            "entries": [
                {
                    "name": "赵小帅",
                    "id": 9,
                    "type": "Shaman",
                    "timestamp": 1000,
                    # 没有 killingBlow
                    "damage": {
                        "total": 469656,
                        "abilities": [
                            {"guid": 1299838, "name": "毒液爆裂", "total": 469656},
                            {"guid": 1, "name": "Melee", "total": 1200},
                        ],
                    },
                }
            ]
        }
    }
    death = summarize_tables(tables)["deaths"][0]

    assert death["killing_ability"] == "毒液爆裂"
    assert death["killing_ability"] != "Shaman", "绝不能回退到职业名"
    # 专精照常解析（`type` 里只有职业名，所以只能到职业这一级），与致死技能无关
    assert death["role_label"] == "萨满祭司"
    assert [b["ability"] for b in death["damage_breakdown"]] == ["毒液爆裂", "Melee"]


def test_death_timeline_says_killing_ability_not_killer():
    """时间轴文案不能写「击杀者：X」—— 那是技能名，读起来像凶手。"""
    tables = {
        "deaths": {"entries": [{"name": "甲", "id": 1, "killingBlow": {"name": "重击"},
                                "timestamp": 500}]},
        "casts": {"entries": []},
    }
    summary = summarize_tables(tables, duration_ms=1000, fight_start_ms=0)

    detail = next(i for i in summary["timeline"] if i["type"] == "death")["detail"]
    assert "致死技能：重击" in detail
    assert "击杀者" not in detail


# --- 审查发现：非玩家混进按人统计的地方 -------------------------------------- #

def test_boss_and_pet_are_not_players():
    """WCL 榜单条目的 `type`：玩家是职业名，非玩家是 Boss/NPC/Pet。

    实测 36 份缓存里两者零重叠。不筛的后果：会自我治疗的 BOSS
    （妖术领主玛拉卡斯，988 万）挤进治疗榜第 10/25，还能被勾选生成
    「玩家报告」；宠物（光诞鞭笞者）同样出现在伤害榜和名册里。
    """
    from app.aggregation import is_player_entry

    assert is_player_entry({"name": "甲", "type": "Shaman"}) is True
    assert is_player_entry({"name": "乙", "type": "DeathKnight"}) is True
    assert is_player_entry({"name": "丙", "class": "Mage"}) is True
    assert is_player_entry({"name": "妖术领主玛拉卡斯", "type": "Boss"}) is False
    assert is_player_entry({"name": "光诞鞭笞者", "type": "Pet"}) is False
    assert is_player_entry({"name": "某 NPC", "type": "NPC"}) is False
    assert is_player_entry({"name": "无类型"}) is False


def test_rankings_exclude_non_players():
    """榜单是「谁打了多少」，BOSS/宠物不该混在里面。

    顺带修掉一个数值失真：`percent` 的分母是榜单总量，混进 BOSS 的自我治疗
    会把所有玩家的占比压低。
    """
    tables = {
        "damage-done": {"entries": [
            {"name": "甲", "type": "Mage", "total": 1000},
            {"name": "光诞鞭笞者", "type": "Pet", "total": 900},
        ]},
        "healing": {"entries": [
            {"name": "乙", "type": "Priest", "total": 500},
            {"name": "妖术领主玛拉卡斯", "type": "Boss", "total": 9880000},
        ]},
    }
    summary = summarize_tables(tables)

    assert [r["actor"] for r in summary["damage_done"]] == ["甲"]
    # 分母只算玩家：甲占 100%，而不是被宠物的 900 摊薄成 52.6%
    assert summary["damage_done"][0]["percent"] == 100.0
    assert [r["actor"] for r in summary["healing_done"]] == ["乙"]
    assert summary["healing_done"][0]["percent"] == 100.0

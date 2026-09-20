from app.aggregation import summarize_tables


def test_summarizes_rankings_and_player_behavior_tables():
    tables = {
        "damage-done": {
            "entries": [
                {"name": "Mage", "type": "Fire", "total": 2000},
                {"name": "Rogue", "type": "Combat", "total": 1000},
            ]
        },
        "healing": {
            "entries": [
                {"name": "Priest", "type": "Holy", "total": 3000},
            ]
        },
        "damage-taken": {
            "entries": [
                {"name": "Tank", "type": "Protection", "total": 4000},
            ]
        },
        "deaths": {
            "entries": [
                {
                    "name": "Rogue",
                    "killingBlow": {
                        "name": "Boss",
                        "ability": {"name": "Cleave"},
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
    assert result["deaths"][0]["source"] == "Boss"
    assert result["deaths"][0]["ability"] == "Cleave"
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

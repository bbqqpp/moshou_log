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

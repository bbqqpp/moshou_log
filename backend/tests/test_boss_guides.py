from app.boss_guides import find_boss_guide, load_all_boss_guides


def test_local_guides_include_expected_bosses():
    boss_ids = {guide["boss_id"] for guide in load_all_boss_guides()}
    assert 3420 in boss_ids
    assert 3421 in boss_ids
    assert 3429 in boss_ids
    assert 3445 in boss_ids
    assert 3492 in boss_ids
    assert 3497 in boss_ids


def test_find_guide_by_boss_id():
    fight = {"boss": 3492, "name": "乌拉特克"}
    guide = find_boss_guide(fight)
    assert guide is not None
    assert guide["name_zh"] == "乌拉特克"


def test_find_guide_falls_back_to_original_boss():
    fight = {"boss": 0, "originalBoss": 3497, "name": "迷失的探险者"}
    guide = find_boss_guide(fight)
    assert guide is not None
    assert guide["name_zh"] == "迷失的探险者"


def test_unknown_boss_returns_none():
    fight = {"boss": 999999, "name": "不存在的 BOSS"}
    assert find_boss_guide(fight) is None

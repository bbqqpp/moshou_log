from app.wcl_data_store import WCLDataStore


def test_save_load_and_query(tmp_path):
    store = WCLDataStore(tmp_path)
    store.save(
        "ReportA123",
        12,
        {"id": 12, "start_time": 0},
        {
            "deaths": {
                "entries": [
                    {"name": "Rogue", "timestamp": 5000},
                    {"name": "Mage", "timestamp": 8000},
                ]
            }
        },
    )

    result = store.query("ReportA123", 12, "deaths", start_time=6000)

    assert result["found"] is True
    assert result["matched_count"] == 1
    assert result["items"][0]["name"] == "Mage"


def test_query_event_stream_by_player_name_and_relative_time_window(tmp_path):
    store = WCLDataStore(tmp_path)
    store.save(
        "ReportA123",
        12,
        {"id": 12, "start_time": 300000, "end_time": 500000},
        {
            "casts": {
                "entries": [{"name": "Mage", "id": 1, "type": "Mage", "timestamp": 1000}]
            },
            "cast_events": {
                "events": [
                    {"timestamp": 302000, "sourceID": 1, "targetID": 9, "ability": {"name": "Fireball"}},
                    {"timestamp": 305000, "sourceID": 2, "targetID": 9, "ability": {"name": "Frostbolt"}},
                ]
            },
        },
    )

    by_player = store.query("ReportA123", 12, "cast_events", player="Mage")
    assert by_player["matched_count"] == 1
    assert by_player["items"][0]["sourceID"] == 1
    assert by_player["actors"][1]["name"] == "Mage"

    relative = store.query(
        "ReportA123", 12, "cast_events", start_time=2000, end_time=4000
    )
    assert relative["matched_count"] == 1
    assert relative["items"][0]["timestamp"] == 302000


def test_missing_key_is_distinct_from_empty(tmp_path):
    """「键不在文件里」和「键在但内容为空」必须区分开。

    `rankings` / `survivability` / `player_details` 是迁移到 WCL V2 之后才有的，
    V1 时代的历史缓存里没有。若静默返回 0 条，模型会得出「这场没有 parse 排名」
    的结论 —— 而事实是「这份缓存太旧，重新拉一次就有」。
    """
    store = WCLDataStore(tmp_path)
    store.save(
        "ReportA123",
        12,
        {"id": 12, "start_time": 0, "end_time": 1000},
        {
            "buffs": {"auras": []},                      # 键在，内容为空
            "rankings": {"available": True, "entries": []},
        },
    )

    empty = store.query("ReportA123", 12, "rankings")
    assert empty["found"] is True
    assert empty["matched_count"] == 0                   # 确实是「没有排名」

    store.save(
        "ReportA123",
        13,
        {"id": 13, "start_time": 0, "end_time": 1000},
        {"buffs": {"auras": []}},                        # 模拟迁移前的旧缓存
    )
    legacy = store.query("ReportA123", 13, "rankings")
    assert legacy["found"] is False
    assert "迁移到 WCL V2 之前" in legacy["message"]


def test_numeric_player_is_an_actor_id_not_a_value_match(tmp_path):
    """纯数字的 player 必须按 actor id 解释，且要是这一场真实存在的 actor。

    实测过的 bug：`player="100"` 返回 9485 条（真值 581），其中 8904 条来自
    值相等匹配 —— 主要是 `classResources[0].max == 100`（怒气/能量的默认上限）。
    `matched_count` 看起来权威，于是把别人的施法当成这个玩家的手法问题。
    """
    store = WCLDataStore(tmp_path)
    store.save(
        "ReportA123", 12, {"id": 12, "start_time": 0, "end_time": 1000},
        {
            "cast_events": {
                "available": True,
                "events": [
                    {"timestamp": 100, "type": "cast", "sourceID": 100,
                     "ability": {"name": "火球术", "guid": 1},
                     # 这个 100 是资源上限，不是玩家
                     "classResources": [{"amount": 100, "max": 100}]},
                    {"timestamp": 200, "type": "cast", "sourceID": 200,
                     "ability": {"name": "冰霜箭", "guid": 2},
                     "classResources": [{"amount": 20, "max": 100}]},
                ],
                "truncated": False,
            },
            "damage-done": {"entries": [
                {"name": "甲", "id": 100, "total": 100}, {"name": "乙", "id": 200, "total": 50},
            ]},
        },
    )

    # actor 100 真实存在 → 只返回它自己的那条
    assert store.query("ReportA123", 12, "cast_events", player="100")["matched_count"] == 1
    # actor 999 不存在 → 不再退化成值相等匹配
    not_found = store.query("ReportA123", 12, "cast_events", player="999")
    assert not_found["found"] is False
    assert "没有匹配到玩家" in not_found["message"]


def test_unknown_player_reports_who_is_present(tmp_path):
    """找不到人时必须说清楚 —— 返回 0 条会让模型写成「这个人全程没输出」。"""
    store = WCLDataStore(tmp_path)
    store.save(
        "ReportB456", 7, {"id": 7, "start_time": 0, "end_time": 1000},
        {"damage-done": {"entries": [{"name": "甲", "id": 1, "total": 100}]}},
    )

    result = store.query("ReportB456", 7, "damage-done", player="假玩家")
    assert result["found"] is False
    assert "甲" in result["message"]
    assert result["available_players"] == ["甲"]


def test_time_window_on_a_table_without_timestamps_is_flagged(tmp_path):
    """聚合表（damage-done / healing…）没有时间戳字段，时间窗对它无效。

    原来静默返回整场数据、`matched_count` 看着权威，调用方会把它当成
    「那 10 秒的数据」来解读（事件流是正常过滤的，所以从行为上察觉不到差异）。
    """
    store = WCLDataStore(tmp_path)
    store.save(
        "ReportC789", 3, {"id": 3, "start_time": 0, "end_time": 60000},
        {
            "damage-done": {"entries": [
                {"name": "甲", "type": "Mage", "total": 100},
                {"name": "乙", "type": "Priest", "total": 50},
            ]},
            "cast_events": {"available": True, "events": [
                {"timestamp": 10_000, "type": "cast", "sourceID": 1, "ability": {"name": "火球术"}},
                {"timestamp": 50_000, "type": "cast", "sourceID": 1, "ability": {"name": "火球术"}},
            ], "truncated": False},
        },
    )

    aggregated = store.query("ReportC789", 3, "damage-done", start_time=10_000, end_time=20_000)
    assert aggregated["time_filter_ignored"] is True
    assert "没有时间戳字段" in aggregated["message"]
    assert aggregated["matched_count"] == 2, "聚合表本来就该返回全部行"

    # 事件流能正常过滤，不该带这个标记
    events = store.query("ReportC789", 3, "cast_events", start_time=10_000, end_time=20_000)
    assert "time_filter_ignored" not in events
    assert events["matched_count"] == 1

    # 不传时间窗时也不该有
    assert "time_filter_ignored" not in store.query("ReportC789", 3, "damage-done")

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

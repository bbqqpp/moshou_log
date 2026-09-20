import json
import re

from app.deepseek_agent import _build_initial_messages


def _user_text(fight=None, summary=None) -> str:
    messages = _build_initial_messages(
        "AbCd1234EfGh5678",
        7,
        fight if fight is not None else {"id": 7, "start_time": 0, "end_time": 60000},
        summary if summary is not None else {"deaths": [], "damage_done": []},
    )
    return messages[1]["content"]


def _payload(text: str) -> dict:
    match = re.search(r"```json\n(.*)\n```", text, re.S)
    assert match, "首轮消息里应当有一个 ```json 代码块"
    return json.loads(match.group(1))


def test_initial_payload_is_serialized_compactly():
    """这块 payload 每轮 tool call 都要重发，缩进是纯浪费 —— 锁住紧凑序列化。

    实测：紧凑化把这场的首轮消息从 361,816 字符降到 198,993（-45%）。
    """
    body = re.search(r"```json\n(.*)\n```", _user_text(), re.S).group(1)

    assert "\n" not in body, "必须是单行紧凑 JSON，缩进会在每轮重复计费"
    # 默认分隔符是 ", " / ": "，紧凑化后应当消失（字符串值内部不含这两种组合）
    assert '": ' not in body
    assert '", "' not in body


def test_initial_payload_keeps_the_fields_the_report_needs():
    payload = _payload(_user_text())

    assert payload["report_code"] == "AbCd1234EfGh5678"
    assert payload["fight_id"] == 7
    assert "summary" in payload
    assert "boss_guide" in payload
    assert "query_hint" in payload
    assert payload["query_hint"]["max_results_per_call"] == 1000


def test_death_windows_are_derived_from_the_summary():
    text = _user_text(
        summary={"deaths": [{"player": "甲", "timestamp": 12_000, "raw": {"id": 98}}]}
    )

    windows = _payload(text)["death_windows"]
    assert len(windows) == 1
    assert windows[0]["player"] == "甲"
    assert windows[0]["player_id"] == 98
    assert windows[0]["pre_window_ms"] == 15000
    assert windows[0]["post_window_ms"] == 3000

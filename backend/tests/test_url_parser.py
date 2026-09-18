import pytest

from app.errors import WCLUrlError
from app.wcl import _is_boss_fight, _last_boss_fight, parse_wcl_url


def test_parses_standard_retail_fight_url():
    report, fight = parse_wcl_url(
        "https://www.warcraftlogs.com/reports/AbCd1234EfGh5678#fight=42&type=damage-done"
    )
    assert report == "AbCd1234EfGh5678"
    assert fight == 42


def test_accepts_missing_scheme_and_www():
    report, fight = parse_wcl_url("warcraftlogs.com/reports/AbCd1234EfGh5678#fight=7")
    assert report == "AbCd1234EfGh5678"
    assert fight == 7


def test_accepts_cn_warcraft_logs_domain():
    report, fight = parse_wcl_url(
        "https://cn.warcraftlogs.com/reports/AbCd1234EfGh5678#fight=7&type=damage-done"
    )
    assert report == "AbCd1234EfGh5678"
    assert fight == 7


def test_rejects_unsupported_domain():
    with pytest.raises(WCLUrlError):
        parse_wcl_url("https://classic.warcraftlogs.com/reports/AbCd1234EfGh5678#fight=1")


def test_rejects_missing_fight_id():
    with pytest.raises(WCLUrlError):
        parse_wcl_url("https://www.warcraftlogs.com/reports/AbCd1234EfGh5678#type=damage-done")


def test_last_fight_is_deferred_to_the_client():
    """`fight=last` 无法在解析阶段定下来，返回 None 交由客户端解析成最后一场 Boss 战。"""
    report, fight = parse_wcl_url(
        "https://www.warcraftlogs.com/reports/AbCd1234EfGh5678#fight=last"
    )
    assert report == "AbCd1234EfGh5678"
    assert fight is None


def test_last_fight_accepted_in_query_string():
    report, fight = parse_wcl_url(
        "https://www.warcraftlogs.com/reports/kpjAyZTN82Bt3ChL?fight=last"
    )
    assert report == "kpjAyZTN82Bt3ChL"
    assert fight is None


def test_boss_fight_detection_uses_boss_field():
    assert _is_boss_fight({"id": 14, "boss": 3470}) is True


def test_boss_fight_detection_falls_back_to_original_boss():
    """部分 report 的 Boss 灭团场次只带 originalBoss，boss 为 0。"""
    assert _is_boss_fight({"id": 13, "boss": 0, "originalBoss": 3470}) is True


def test_trash_pull_is_not_a_boss_fight():
    assert _is_boss_fight({"id": 1, "boss": 0}) is False
    assert _is_boss_fight({"id": 2, "boss": 0, "originalBoss": None}) is False


def test_last_boss_fight_skips_trailing_trash():
    fights = [
        {"id": 10, "boss": 3512, "name": "Boss"},
        {"id": 22, "boss": 0, "name": "小怪"},
        {"id": 23, "boss": 0, "name": "小怪"},
    ]
    assert _last_boss_fight(fights)["id"] == 10


def test_last_boss_fight_returns_none_when_no_boss_present():
    assert _last_boss_fight([{"id": 1, "boss": 0}, {"id": 2, "boss": 0}]) is None
    assert _last_boss_fight([]) is None


def test_parses_archon_gg_report_url():
    report, fight = parse_wcl_url(
        "https://www.archon.gg/wow/reports/kpjAyZTN82Bt3ChL/fights/14/raid?locale=cn"
    )
    assert report == "kpjAyZTN82Bt3ChL"
    assert fight == 14


def test_archon_url_without_www_or_scheme():
    report, fight = parse_wcl_url("archon.gg/wow/reports/AbCd1234EfGh5678/fights/3")
    assert report == "AbCd1234EfGh5678"
    assert fight == 3


def test_rejects_archon_url_without_fight_id():
    with pytest.raises(WCLUrlError):
        parse_wcl_url("https://www.archon.gg/wow/reports/kpjAyZTN82Bt3ChL")


def test_archon_url_is_not_treated_as_a_wcl_report_path():
    """archon 的路径结构与 WCL 不同，必须走单独的分支而不是误匹配。"""
    with pytest.raises(WCLUrlError):
        parse_wcl_url("https://www.archon.gg/wow/rankings/14")

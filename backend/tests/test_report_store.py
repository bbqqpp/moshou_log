import pytest

from app.report_store import PlayerReportStore, make_slug, strip_preamble


def _store(tmp_path) -> PlayerReportStore:
    return PlayerReportStore(tmp_path / "player_reports")


def test_save_and_get_roundtrip(tmp_path):
    store = _store(tmp_path)

    assert store.get("AbCd1234EfGh5678", 7, "single", "nope") is None

    saved = store.save(
        "AbCd1234EfGh5678",
        7,
        "single",
        ["测试玩家"],
        player_ids=[12],
        specs=["奥术法师"],
        model="deepseek-flash",
        analysis="# 报告",
        payload={"a": 1},
    )

    record = store.get("AbCd1234EfGh5678", 7, "single", saved["slug"])
    assert record is not None
    assert record["analysis"] == "# 报告"
    assert record["players"] == ["测试玩家"]
    assert record["specs"] == ["奥术法师"]
    assert record["payload"] == {"a": 1}


def test_regenerating_same_players_overwrites_instead_of_duplicating(tmp_path):
    """同一组玩家重新生成必须是覆盖 —— 否则侧边栏会堆出一串重复项。"""
    store = _store(tmp_path)

    first = store.save("CODE1234", 7, "single", ["甲"], analysis="第一版")
    second = store.save("CODE1234", 7, "single", ["甲"], analysis="第二版")

    assert first["slug"] == second["slug"]
    assert len(list((tmp_path / "player_reports").glob("*.json"))) == 1
    assert store.get("CODE1234", 7, "single", first["slug"])["analysis"] == "第二版"


def test_slug_ignores_player_order(tmp_path):
    """对比报告的两个人换个顺序，应该是同一份而不是两份。"""
    assert make_slug(["甲", "乙"]) == make_slug(["乙", "甲"])
    assert make_slug(["甲"]) != make_slug(["甲", "乙"])


def test_single_and_comparison_do_not_collide(tmp_path):
    store = _store(tmp_path)

    store.save("CODE1234", 7, "single", ["甲"], analysis="单人")
    store.save("CODE1234", 7, "comparison", ["甲"], analysis="对比")

    rows = store.list_for_fight("CODE1234", 7)
    assert sorted(row["kind"] for row in rows) == ["comparison", "single"]


def test_list_for_fight_is_scoped_to_the_fight(tmp_path):
    store = _store(tmp_path)
    store.save("CODE1234", 7, "single", ["甲"], analysis="第 7 场")
    store.save("CODE1234", 8, "single", ["甲"], analysis="第 8 场")
    store.save("OTHER999", 7, "single", ["甲"], analysis="别的 report")

    rows = store.list_for_fight("CODE1234", 7)

    assert len(rows) == 1
    assert store.get("CODE1234", 7, "single", rows[0]["slug"])["analysis"] == "第 7 场"


def test_list_entries_carry_no_heavy_fields(tmp_path):
    """列表接口只给元数据，不能把 analysis/payload 一起塞进响应。"""
    store = _store(tmp_path)
    store.save("CODE1234", 7, "single", ["甲"], analysis="x" * 5000, payload={"big": "y" * 5000})

    row = store.list_for_fight("CODE1234", 7)[0]

    assert "analysis" not in row
    assert "payload" not in row
    assert row["players"] == ["甲"]


def test_rejects_unknown_kind(tmp_path):
    with pytest.raises(ValueError, match="不支持的报告类型"):
        _store(tmp_path).save("CODE1234", 7, "trilogy", ["甲"], analysis="x")


@pytest.mark.parametrize(
    "raw",
    [
        "I now have comprehensive data. Let me write the report.\n\n# 乌拉特克复盘\n正文",
        "我已获得足够证据。现在整理最终报告。\n\n# 乌拉特克复盘\n正文",
        "# 乌拉特克复盘\n正文",
    ],
)
def test_strip_preamble_drops_model_chatter(raw: str):
    """实测 31 份缓存里 30 份带前言（中英文都有），直接渲染会顶在报告最上面。"""
    assert strip_preamble(raw).startswith("# 乌拉特克复盘")


def test_strip_preamble_keeps_text_without_any_heading():
    assert strip_preamble("没有标题的纯文本") == "没有标题的纯文本"


def test_strip_preamble_handles_empty():
    assert strip_preamble("") == ""

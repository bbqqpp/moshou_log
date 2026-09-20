import json

from app.cache import AnalysisCache


def test_cache_roundtrip_by_report_and_fight(tmp_path):
    cache = AnalysisCache(tmp_path, model="deepseek-flash")

    assert cache.get("ReportA123", 12) is None

    cache.set("ReportA123", 12, "# 报告内容")

    assert cache.get("ReportA123", 12) == "# 报告内容"
    assert cache.get("ReportA123", 13) is None
    assert cache.get("ReportB999", 12) is None


def test_set_stores_fight_metadata_for_the_sidebar(tmp_path):
    """侧边栏要显示 Boss 名，但 wcl_data 单文件可达 56MB，不能为了列个表去读它。"""
    cache = AnalysisCache(tmp_path, model="deepseek-flash")

    cache.set(
        "ReportA123",
        12,
        "# 报告内容",
        fight={"name": "乌拉特克", "boss": 3492, "kill": False, "size": 21},
    )

    rows = cache.list_all()
    assert len(rows) == 1
    assert rows[0]["fight"]["name"] == "乌拉特克"
    assert rows[0]["fight_id"] == 12
    assert rows[0]["report_code"] == "ReportA123"


def _make_stale(tmp_path, cache: AnalysisCache) -> None:
    """把 signature 改掉，模拟「系统提示词/模型改过之后」的旧报告。"""
    path = cache._path("ReportA123", 12)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["signature"] = "outdated-signature"
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")


def test_stale_reports_are_still_listed(tmp_path):
    """刻意不过滤 stale —— 否则改一次提示词侧边栏就整个空掉。"""
    cache = AnalysisCache(tmp_path, model="deepseek-flash")
    cache.set("ReportA123", 12, "# 报告内容")
    _make_stale(tmp_path, cache)

    assert cache.get("ReportA123", 12) is None, "严格读法应当失效"

    rows = cache.list_all()
    assert len(rows) == 1
    assert rows[0]["stale"] is True


def test_read_record_ignores_signature_so_old_reports_stay_viewable(tmp_path):
    cache = AnalysisCache(tmp_path, model="deepseek-flash")
    cache.set("ReportA123", 12, "# 报告内容")
    _make_stale(tmp_path, cache)

    record = cache.read_record("ReportA123", 12)

    assert record is not None
    assert record["analysis"] == "# 报告内容"
    assert record["stale"] is True
    assert cache.read_record("ReportA123", 99) is None


def test_list_all_keeps_records_without_fight_metadata(tmp_path):
    """回填之前写下的缓存没有 fight 字段，前端会降级显示，但条目本身不能消失。"""
    cache = AnalysisCache(tmp_path, model="deepseek-flash")
    cache.set("ReportA123", 12, "# 老报告")

    rows = cache.list_all()

    assert len(rows) == 1
    assert rows[0]["fight"] is None


def test_list_all_skips_unreadable_and_empty_files(tmp_path):
    cache = AnalysisCache(tmp_path, model="deepseek-flash")
    cache.set("ReportA123", 12, "# 正常报告")
    (tmp_path / "Broken999__1.json").write_text("{ 不是 JSON", encoding="utf-8")
    (tmp_path / "Empty999__2.json").write_text(
        json.dumps({"report_code": "Empty999", "fight_id": 2, "analysis": ""}),
        encoding="utf-8",
    )

    rows = cache.list_all()

    assert [row["report_code"] for row in rows] == ["ReportA123"]

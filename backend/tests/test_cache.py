from app.cache import AnalysisCache


def test_cache_roundtrip_by_report_and_fight(tmp_path):
    cache = AnalysisCache(tmp_path, model="deepseek-flash")

    assert cache.get("ReportA123", 12) is None

    cache.set("ReportA123", 12, "# 报告内容")

    assert cache.get("ReportA123", 12) == "# 报告内容"
    assert cache.get("ReportA123", 13) is None
    assert cache.get("ReportB999", 12) is None

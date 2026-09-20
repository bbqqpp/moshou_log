#!/usr/bin/env python3
"""一次性回填：把 `wcl_data` 里的 fight 元数据补进已有的 analysis_cache 记录。

侧边栏要显示 Boss 名 / 击杀结果，而 `AnalysisCache.set` 现在会把 `fight` 一起存下来 ——
但**在这之前写下的缓存没有这个字段**，不回填的话侧边栏第一眼全是裸 report code。

代价是要读一遍 `wcl_data`（单个文件最大 56MB，实测 31 个文件约 18 秒），所以只跑一次。

幂等：已经有 `fight` 的记录直接跳过。可以重复运行。

用法：
    cd backend && .venv/bin/python3 scripts/backfill_report_meta.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

CACHE_DIR = BACKEND_DIR / "storage" / "analysis_cache"
WCL_DATA_DIR = BACKEND_DIR / "storage" / "wcl_data"


def main() -> int:
    if not CACHE_DIR.exists():
        print(f"没有缓存目录：{CACHE_DIR}", file=sys.stderr)
        return 1

    filled = skipped = missing = broken = 0

    for cache_path in sorted(CACHE_DIR.glob("*.json")):
        try:
            record = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            broken += 1
            continue
        if not isinstance(record, dict):
            broken += 1
            continue

        if record.get("fight"):
            skipped += 1
            continue

        report_code = record.get("report_code")
        fight_id = record.get("fight_id")
        if not report_code or fight_id is None:
            broken += 1
            continue

        # 两边文件名约定相同，直接按同样的规则拼
        data_path = WCL_DATA_DIR / f"{report_code}__{int(fight_id)}.json"
        if not data_path.exists():
            # 实测确实存在这种：只有分析缓存、原始数据已被删掉的那一场。
            # 不报错，前端会降级显示 report code + fight id。
            print(f"[跳过] {cache_path.name}：找不到对应的 wcl_data", file=sys.stderr)
            missing += 1
            continue

        try:
            data = json.loads(data_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print(f"[跳过] {cache_path.name}：原始数据读不出来", file=sys.stderr)
            broken += 1
            continue

        fight = data.get("fight")
        if not fight:
            print(f"[跳过] {cache_path.name}：原始数据里没有 fight 字段", file=sys.stderr)
            missing += 1
            continue

        record["fight"] = fight
        tmp = cache_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(cache_path)
        filled += 1
        print(f"[回填] {cache_path.name} -> {fight.get('name')}", file=sys.stderr)

    print(
        f"\n完成：回填 {filled}，已有跳过 {skipped}，缺原始数据 {missing}，损坏 {broken}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

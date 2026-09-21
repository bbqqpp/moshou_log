#!/usr/bin/env python3
"""单场战斗复盘：拉全量数据 → 交给 DeepSeek 生成整场中文复盘报告。

这是网页版应用的等价命令行流程，逻辑全部复用 backend/app，不依赖那个 Backend 服务在跑。

用法：
    python3 review.py --url "https://www.warcraftlogs.com/reports/CODE#fight=12" --out report.md
    python3 review.py --report CODE --fight 12
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
BACKEND_DIR = PROJECT_ROOT / "backend"

# 依赖装在 backend/.venv 里，用系统 python3 跑会缺包，自动切过去。
# 判据用 sys.prefix 而非路径比较：venv 的 python3 是指向 /usr/bin/python3 的符号链接。
_VENV_DIR = BACKEND_DIR / ".venv"
_VENV_PYTHON = _VENV_DIR / "bin" / "python3"
if _VENV_PYTHON.exists() and Path(sys.prefix) != _VENV_DIR:
    os.execv(
        str(_VENV_PYTHON),
        [str(_VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
    )

sys.path.insert(0, str(BACKEND_DIR))

from app.aggregation import _number, summarize_tables  # noqa: E402
from app.config import settings  # noqa: E402
from app.player_compare import load_fight  # noqa: E402
from app.wcl import resolve_fight_reference  # noqa: E402


async def _run(report_code: str, fight_id: int) -> str:
    from app.deepseek_agent import run_deepseek_tool_loop
    from app.wcl_data_store import WCLDataStore

    data = load_fight(report_code, fight_id)
    fight = data.get("fight") or {}
    fight_id = int(data.get("fight_id") or fight_id)

    fight_start = _number(fight.get("start_time"))
    duration_ms = max(0.0, _number(fight.get("end_time")) - fight_start)
    summary = summarize_tables(
        data.get("tables") or {},
        timeline_limit=settings.timeline_preview_limit,
        duration_ms=duration_ms,
        fight_start_ms=fight_start,
        # 不传 fight 就拿不到真实阶段时间轴（summary.phases 会恒为空），
        # skill 链路的报告会和网页端得出不一样的阶段结论
        fight=fight,
    )

    store = WCLDataStore(BACKEND_DIR / "storage" / "wcl_data")
    return await run_deepseek_tool_loop(store, report_code, fight_id, fight, summary)


def main() -> None:
    parser = argparse.ArgumentParser(description="跑一场 WCL 战斗的完整复盘")
    parser.add_argument("--url", help="WCL / archon 战斗链接（支持 #fight=last）")
    parser.add_argument("--report", help="WCL report code")
    parser.add_argument("--fight", type=int, help="战斗 ID")
    parser.add_argument("--out", help="把报告写入文件（默认打印到 stdout）")
    args = parser.parse_args()

    if not settings.deepseek_api_key:
        raise SystemExit("backend/.env 里没有配置 DEEPSEEK_API_KEY")

    if args.url:
        report_code, fight_id = resolve_fight_reference(args.url, settings)
    elif args.report and args.fight is not None:
        report_code, fight_id = args.report, args.fight
    else:
        raise SystemExit("需要给出 --url，或者 --report 加 --fight")

    print(f"[复盘] {report_code} fight={fight_id}，正在分析…", file=sys.stderr)
    report = asyncio.run(_run(report_code, fight_id))

    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"[完成] 已写入 {args.out}", file=sys.stderr)
    print(report)


if __name__ == "__main__":
    main()

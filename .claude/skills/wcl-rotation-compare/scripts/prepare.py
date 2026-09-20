#!/usr/bin/env python3
"""为两名玩家的技能循环对比准备数据。

对比逻辑本身在 `backend/app/player_compare.py`，与 MCP server 共用一份，
这里只负责解析参数、调 URL、落盘。

用法：
    python3 prepare.py \
        --a-url "https://www.warcraftlogs.com/reports/CODE#fight=12" --a-player 甲 \
        --b-url "..." --b-player 乙 \
        --out /tmp/compare.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
BACKEND_DIR = PROJECT_ROOT / "backend"

# 项目依赖（httpx/python-dotenv 等）装在 backend/.venv 里，用系统 python3 跑会缺包。
# 这里自动切到 venv 解释器，让 skill 无论被谁调用都能起来。
# 判据用 sys.prefix 而不是路径比较：venv 的 python3 是指向 /usr/bin/python3 的
# 符号链接，resolve() 之后两边一模一样，比不出区别。
_VENV_DIR = BACKEND_DIR / ".venv"
_VENV_PYTHON = _VENV_DIR / "bin" / "python3"
if _VENV_PYTHON.exists() and Path(sys.prefix) != _VENV_DIR:
    os.execv(
        str(_VENV_PYTHON),
        [str(_VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
    )

sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402

from app.config import settings  # noqa: E402
from app.player_compare import build_comparison, build_player_payload, load_fight  # noqa: E402
from app.wcl import resolve_fight_reference  # noqa: E402


def _side_args(args: argparse.Namespace, side: str) -> tuple[str, int, str]:
    """每个玩家支持两种给法：完整链接，或 report + fight 分开给。"""
    player = getattr(args, f"{side}_player")
    url = getattr(args, f"{side}_url")
    if url:
        report_code, fight_id = resolve_fight_reference(url, settings)
        return report_code, fight_id, player

    report_code = getattr(args, f"{side}_report")
    fight_id = getattr(args, f"{side}_fight")
    if not report_code or fight_id is None:
        raise SystemExit(f"{side} 需要给出 --{side}-url，或者 --{side}-report 加 --{side}-fight")
    return report_code, fight_id, player


def main() -> None:
    parser = argparse.ArgumentParser(description="准备两名玩家的技能循环对比数据")
    for side in ("a", "b"):
        parser.add_argument(f"--{side}-url", help=f"{side} 的 WCL/archon 完整链接（推荐）")
        parser.add_argument(f"--{side}-report", help=f"{side} 的 WCL report code")
        parser.add_argument(f"--{side}-fight", type=int, help=f"{side} 的战斗 ID")
        parser.add_argument(f"--{side}-player", required=True, help=f"{side} 的玩家名")
    parser.add_argument("--out", required=True, help="输出 JSON 路径")
    args = parser.parse_args()

    sides = {}
    for side in ("a", "b"):
        report_code, fight_id, player = _side_args(args, side)
        print(f"[取数] {side.upper()}: {report_code} fight={fight_id} 玩家={player}", file=sys.stderr)
        data = load_fight(report_code, fight_id)
        payload = build_player_payload(data, player)
        payload["report_code"], payload["fight_id"] = report_code, fight_id
        sides[side] = payload

    payload = build_comparison(sides["a"], sides["b"])

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    a, b = payload["a"], payload["b"]
    print(f"已写入 {out_path}")
    for label, side in (("A", a), ("B", b)):
        unit = "HPS" if side["output_kind"] == "healing" else "DPS"
        extra = (
            f"  过量率 {side.get('overheal_percent')}%"
            if side["output_kind"] == "healing"
            else ""
        )
        print(
            f"  {label}: {side['player']}  {side['spec']}  装等 {side['item_level']}  "
            f"{unit}(活跃) {side['per_second_by_active_time']:,.0f}{extra}  "
            f"施法 {side['cast_count']} 次"
        )
    print(f"  {payload['idle_comparison']['结论']}")
    for note in payload["fairness"]["notes"]:
        print(f"  ⚠ {note}")


if __name__ == "__main__":
    main()

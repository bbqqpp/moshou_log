#!/usr/bin/env python3
"""把 prepare.py 生成的对比数据交给 DeepSeek 分析技能循环差异。

用法：
    python3 ask.py --input /tmp/compare.json [--out report.md] [--model deepseek-flash]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
BACKEND_DIR = PROJECT_ROOT / "backend"

# 同 prepare.py：自动切到装了依赖的 venv 解释器（用 sys.prefix 判断，不是路径比较）
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
from app.player_report import COMPARISON_SYSTEM_PROMPT as SYSTEM_PROMPT  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="让 DeepSeek 分析技能循环差异")
    parser.add_argument("--input", required=True, help="prepare.py 生成的对比 JSON")
    parser.add_argument("--out", help="把分析结果写入文件（默认打印到 stdout）")
    parser.add_argument("--model", default=settings.deepseek_model)
    args = parser.parse_args()

    if not settings.deepseek_api_key:
        raise SystemExit("backend/.env 里没有配置 DEEPSEEK_API_KEY")

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    a, b = payload["a"], payload["b"]

    user_text = (
        f"请对比 {a['player']}（{a['spec']}）与 {b['player']}（{b['spec']}）的技能释放顺序和输出差异。\n\n"
        f"```json\n{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n```"
    )

    endpoint = f"{settings.deepseek_base_url.rstrip('/')}/chat/completions"
    print(f"[请求] {args.model}  输入约 {len(user_text) // 4:,} tokens…", file=sys.stderr)

    response = httpx.post(
        endpoint,
        headers={
            "Authorization": f"Bearer {settings.deepseek_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": args.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            "stream": False,
            "thinking": {"type": "disabled"},
            "max_tokens": settings.deepseek_max_tokens,
        },
        timeout=httpx.Timeout(600.0, connect=20.0),
    )

    if response.status_code != 200:
        raise SystemExit(f"DeepSeek 返回 {response.status_code}：{response.text[:800]}")

    choices = response.json().get("choices") or []
    if not choices:
        raise SystemExit("DeepSeek 未返回 choices")
    analysis = str((choices[0].get("message") or {}).get("content") or "")
    if not analysis:
        raise SystemExit("DeepSeek 返回了空内容")

    if args.out:
        Path(args.out).write_text(analysis, encoding="utf-8")
        print(f"[完成] 已写入 {args.out}", file=sys.stderr)
    print(analysis)


if __name__ == "__main__":
    main()

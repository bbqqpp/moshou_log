from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Mapping

from .boss_guides import load_all_boss_guides
from .prompt import SYSTEM_PROMPT


class AnalysisCache:
    """File-backed cache keyed by WCL report code and fight id."""

    def __init__(self, base_dir: Path, model: str):
        self.base_dir = Path(base_dir)
        self.model = model

    def _signature(self) -> str:
        payload = {
            "model": self.model,
            "system_prompt": SYSTEM_PROMPT,
            "boss_guides": load_all_boss_guides(),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:16]

    def _path(self, report_code: str, fight_id: int) -> Path:
        safe_report = "".join(
            char for char in report_code if char.isalnum() or char in "_-"
        )
        filename = f"{safe_report}__{int(fight_id)}.json"
        return self.base_dir / filename

    def get(self, report_code: str, fight_id: int) -> str | None:
        path = self._path(report_code, fight_id)
        if not path.exists():
            return None

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

        if payload.get("signature") != self._signature():
            return None
        if payload.get("model") != self.model:
            return None

        analysis = payload.get("analysis")
        return analysis if isinstance(analysis, str) and analysis else None

    def read_record(self, report_code: str, fight_id: int) -> dict[str, Any] | None:
        """按磁盘原样读出整条记录，**不做 signature 校验**。

        `get()` 回答的是「能不能拿这份缓存当结果用」，所以它会因为提示词/模型变化而失效；
        但**展示一份已经存在的报告**不该因为提示词改过就直接消失——那会让侧边栏里
        点进去一片空白。这里只在返回值里带一个 `stale` 标记，交给前端提示。
        """
        path = self._path(report_code, fight_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None

        analysis = payload.get("analysis")
        if not isinstance(analysis, str) or not analysis:
            return None

        payload["stale"] = payload.get("signature") != self._signature()
        return payload

    def set(
        self,
        report_code: str,
        fight_id: int,
        analysis: str,
        fight: Mapping[str, Any] | None = None,
    ) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        path = self._path(report_code, fight_id)
        payload = {
            "report_code": report_code,
            "fight_id": int(fight_id),
            "model": self.model,
            "signature": self._signature(),
            "created_at": time.time(),
            "analysis": analysis,
            # 侧边栏要显示 Boss 名 / 击杀结果，但 wcl_data 单文件能到 56MB
            # （实测 json.load 0.57s），列 31 场就是 ~18 秒。顺手存一份在这里，
            # 列表就只读这些小文件。老缓存没有这个字段，读出来是 None，前端降级显示。
            "fight": dict(fight) if fight else None,
        }

        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(path)

    def list_all(self) -> list[dict[str, Any]]:
        """全部已分析战斗的轻量元数据（不含 analysis 正文）。

        **刻意不过滤 stale 条目**，和 `get()` 的行为相反：提示词或模型一改，
        所有旧报告的 signature 都会失配，这里要是跟着过滤，侧边栏会直接空掉。
        改成把 stale 作为标记返回，由前端决定怎么展示。
        """
        if not self.base_dir.exists():
            return []

        current_signature = self._signature()
        rows: list[dict[str, Any]] = []
        for path in self.base_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue

            analysis = payload.get("analysis")
            if not isinstance(analysis, str) or not analysis:
                continue

            rows.append(
                {
                    "report_code": payload.get("report_code"),
                    "fight_id": payload.get("fight_id"),
                    "model": payload.get("model"),
                    "created_at": payload.get("created_at"),
                    "stale": payload.get("signature") != current_signature,
                    "fight": payload.get("fight"),
                }
            )

        return sorted(rows, key=lambda row: row.get("created_at") or 0, reverse=True)

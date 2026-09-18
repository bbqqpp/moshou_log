from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

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

    def set(self, report_code: str, fight_id: int, analysis: str) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        path = self._path(report_code, fight_id)
        payload = {
            "report_code": report_code,
            "fight_id": int(fight_id),
            "model": self.model,
            "signature": self._signature(),
            "created_at": time.time(),
            "analysis": analysis,
        }

        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(path)

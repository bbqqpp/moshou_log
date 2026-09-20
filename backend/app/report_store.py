"""玩家报告（单人 / 双人对比）的落盘存储。

沿用 `cache.py` 和 `wcl_data_store.py` 的**扁平目录**约定，不发明新布局，
只在文件名后缀上多带两段：

    {report_code}__{fight_id}__{kind}__{slug}.json

`slug` 由玩家名算出来而不是随机数，所以**同一个人重新生成是覆盖同一个文件**，
不会在侧边栏里堆出一串重复条目。分享链接也因此永久稳定：重新生成报告，
链接不变。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

KINDS = ("single", "comparison")

# 分析正文开头常带一段模型的前言再进 `#` 标题（实测 31 份里 30 份如此，
# 中英文都有），直接渲染会让分享页顶部顶着一行
# "I now have comprehensive data..."。取第一个行首 `#` 之前的内容丢掉。
_HEADING_RE = re.compile(r"^#", re.MULTILINE)


def strip_preamble(text: str) -> str:
    """丢掉 markdown 正文之前那段模型前言。"""
    if not text:
        return ""
    match = _HEADING_RE.search(text)
    if match is None:
        return text.strip()
    return text[match.start():].strip()


def make_slug(players: Iterable[str]) -> str:
    """由玩家名生成确定性的 slug（与顺序无关）。"""
    normalized = "|".join(sorted(str(player).strip() for player in players))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:10]


def _safe_report(report_code: str) -> str:
    return "".join(char for char in report_code if char.isalnum() or char in "_-")


class PlayerReportStore:
    """Stores generated player reports for one report/fight on disk."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)

    def _path(
        self,
        report_code: str,
        fight_id: int,
        kind: str,
        slug: str,
    ) -> Path:
        return self.base_dir / (
            f"{_safe_report(report_code)}__{int(fight_id)}__{kind}__{slug}.json"
        )

    def _prefix(self, report_code: str, fight_id: int) -> str:
        return f"{_safe_report(report_code)}__{int(fight_id)}__"

    def save(
        self,
        report_code: str,
        fight_id: int,
        kind: str,
        players: Iterable[str],
        *,
        player_ids: Iterable[int] = (),
        specs: Iterable[str] = (),
        model: str = "",
        analysis: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """写入一份报告。同一组玩家重复调用会覆盖同一个文件。"""
        if kind not in KINDS:
            raise ValueError(f"不支持的报告类型：{kind}")

        player_list = [str(player) for player in players]
        slug = make_slug(player_list)
        self.base_dir.mkdir(parents=True, exist_ok=True)

        record = {
            "report_code": report_code,
            "fight_id": int(fight_id),
            "kind": kind,
            "slug": slug,
            "players": player_list,
            "player_ids": [int(i) for i in player_ids],
            "specs": [str(s) for s in specs],
            "model": model,
            "created_at": time.time(),
            "analysis": analysis,
            "payload": dict(payload or {}),
        }

        path = self._path(report_code, fight_id, kind, slug)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(record, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)
        return record

    def get(
        self,
        report_code: str,
        fight_id: int,
        kind: str,
        slug: str,
    ) -> dict[str, Any] | None:
        return self._read(self._path(report_code, fight_id, kind, slug))

    def _read(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _summary(record: Mapping[str, Any]) -> dict[str, Any]:
        """列表用的轻量投影——不带 analysis 和 payload。"""
        return {
            "report_code": record.get("report_code"),
            "fight_id": record.get("fight_id"),
            "kind": record.get("kind"),
            "slug": record.get("slug"),
            "players": record.get("players") or [],
            "specs": record.get("specs") or [],
            "created_at": record.get("created_at"),
        }

    def list_for_fight(self, report_code: str, fight_id: int) -> list[dict[str, Any]]:
        if not self.base_dir.exists():
            return []

        prefix = self._prefix(report_code, fight_id)
        rows = []
        for path in sorted(self.base_dir.glob(f"{prefix}*.json")):
            record = self._read(path)
            if record is not None:
                rows.append(self._summary(record))
        return sorted(rows, key=lambda row: row.get("created_at") or 0, reverse=True)

    def list_all(self) -> list[dict[str, Any]]:
        """全部玩家报告的轻量元数据，供侧边栏按战斗分组。"""
        if not self.base_dir.exists():
            return []

        rows = []
        for path in self.base_dir.glob("*.json"):
            record = self._read(path)
            if record is not None:
                rows.append(self._summary(record))
        return sorted(rows, key=lambda row: row.get("created_at") or 0, reverse=True)

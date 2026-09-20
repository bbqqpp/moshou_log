from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass
class AnalysisJob:
    task_id: str
    report_code: str
    fight_id: int
    url: str
    fight: Mapping[str, Any]
    summary: Mapping[str, Any]
    tables: dict[str, Any]
    status: str = "ready"
    deepseek_full_text: str = ""
    error_message: str = ""
    created_at: float = field(default_factory=time.time)


class JobStore:
    """暂存 extract 与 analyze 之间那份已拉取的战斗数据。"""

    def __init__(self, limit: int = 100):
        self.limit = limit
        self._jobs: dict[str, AnalysisJob] = {}

    def create(
        self,
        *,
        report_code: str,
        fight_id: int,
        url: str,
        fight: Mapping[str, Any],
        summary: Mapping[str, Any],
        tables: Mapping[str, Any],
    ) -> AnalysisJob:
        if len(self._jobs) >= self.limit:
            oldest = min(self._jobs, key=lambda key: self._jobs[key].created_at)
            self._jobs.pop(oldest, None)

        task_id = uuid.uuid4().hex
        job = AnalysisJob(
            task_id=task_id,
            report_code=report_code,
            fight_id=fight_id,
            url=url,
            fight=fight,
            summary=summary,
            tables=dict(tables),
        )
        self._jobs[task_id] = job
        return job

    def get(self, task_id: str) -> AnalysisJob | None:
        return self._jobs.get(task_id)

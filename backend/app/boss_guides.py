from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

GUIDES_DIR = Path(__file__).resolve().parents[1] / "storage" / "boss_guides"


def load_all_boss_guides() -> list[dict[str, Any]]:
    """Load every boss guide stored under ``backend/storage/boss_guides``."""
    guides: list[dict[str, Any]] = []
    if not GUIDES_DIR.exists():
        return guides

    for path in sorted(GUIDES_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue

        bosses = payload.get("bosses")
        if isinstance(bosses, list):
            guides.extend(item for item in bosses if isinstance(item, dict))

    return guides


def _boss_candidates(fight: Mapping[str, Any]) -> list[Any]:
    candidates = [fight.get("boss"), fight.get("originalBoss"), fight.get("name")]
    seen: set[tuple[str, str]] = set()
    unique: list[Any] = []
    for candidate in candidates:
        key = (type(candidate).__name__, str(candidate))
        if key not in seen and candidate not in (None, "", 0):
            seen.add(key)
            unique.append(candidate)
    return unique


def find_boss_guide(fight: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the local guide matching a WCL fight object.

    V1 WCL sometimes puts the encounter id in ``boss`` and sometimes in
    ``originalBoss``, so both are checked. If no numeric id matches, fall back
    to matching by Chinese or English boss name.
    """
    guides = load_all_boss_guides()
    if not guides:
        return None

    candidates = _boss_candidates(fight)
    candidate_set = {int(item) for item in candidates if str(item).isdigit()}
    candidate_names = {str(item).strip().casefold() for item in candidates}

    for guide in guides:
        boss_id = guide.get("boss_id")
        if isinstance(boss_id, int) and boss_id in candidate_set:
            return guide

    if not candidate_names:
        return None

    for guide in guides:
        names = [guide.get("name_zh"), guide.get("name_en")]
        normalized = {
            str(name).strip().casefold() for name in names if name is not None
        }
        if normalized & candidate_names:
            return guide

    return None

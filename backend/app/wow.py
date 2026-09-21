from __future__ import annotations

import json
from pathlib import Path

from typing import Any

CLASS_MAP = {
    "death-knight": {
        "cn": "死亡骑士",
        "aliases": ("death knight", "deathknight", "dk"),
    },
    "demon-hunter": {
        "cn": "恶魔猎手",
        "aliases": ("demon hunter", "demonhunter", "dh"),
    },
    "evoker": {
        "cn": "唤魔师",
        "aliases": ("evoker",),
    },
    "druid": {
        "cn": "德鲁伊",
        "aliases": ("druid",),
    },
    "hunter": {
        "cn": "猎人",
        "aliases": ("hunter",),
    },
    "mage": {
        "cn": "法师",
        "aliases": ("mage",),
    },
    "monk": {
        "cn": "武僧",
        "aliases": ("monk",),
    },
    "paladin": {
        "cn": "圣骑士",
        "aliases": ("paladin",),
    },
    "priest": {
        "cn": "牧师",
        "aliases": ("priest",),
    },
    "rogue": {
        "cn": "潜行者",
        "aliases": ("rogue",),
    },
    "shaman": {
        "cn": "萨满祭司",
        "aliases": ("shaman",),
    },
    "warlock": {
        "cn": "术士",
        "aliases": ("warlock",),
    },
    "warrior": {
        "cn": "战士",
        "aliases": ("warrior",),
    },
}

SPEC_MAP = {
    "death-knight": (
        ("blood", "鲜血"),
        ("frost", "冰霜"),
        ("unholy", "邪恶"),
    ),
    "demon-hunter": (
        ("havoc", "浩劫"),
        ("vengeance", "复仇"),
    ),
    "evoker": (
        ("devastation", "湮灭"),
        ("preservation", "恩护"),
        ("augmentation", "增辉"),
    ),
    "druid": (
        ("balance", "平衡"),
        ("feral", "野性"),
        ("guardian", "守护"),
        ("restoration", "恢复"),
    ),
    "hunter": (
        ("beast mastery", "野兽控制"),
        ("marksmanship", "射击"),
        ("survival", "生存"),
    ),
    "mage": (
        ("arcane", "奥术"),
        ("fire", "火焰"),
        ("frost", "冰霜"),
    ),
    "monk": (
        ("brewmaster", "酒仙"),
        ("windwalker", "踏风"),
        ("mistweaver", "织雾"),
    ),
    "paladin": (
        ("holy", "神圣"),
        ("protection", "防护"),
        ("retribution", "惩戒"),
    ),
    "priest": (
        ("discipline", "戒律"),
        ("holy", "神圣"),
        ("shadow", "暗影"),
    ),
    "rogue": (
        ("assassination", "奇袭"),
        ("outlaw", "狂徒"),
        ("subtlety", "敏锐"),
    ),
    "shaman": (
        ("elemental", "元素"),
        ("enhancement", "增强"),
        ("restoration", "恢复"),
    ),
    "warlock": (
        ("affliction", "痛苦"),
        ("demonology", "恶魔学识"),
        ("destruction", "毁灭"),
    ),
    "warrior": (
        ("arms", "武器"),
        ("fury", "狂怒"),
        ("protection", "防护"),
    ),
}


# 客户端在 combatant_info 里上报的专精 ID。WCL 的 tables 里那个 type 字段给的是
# 职业名（"Shaman"、"Paladin"），拿它去匹配专精词永远匹配不到 —— 所以在有
# combatant_info 数据时必须用这张表，专精才判得出来。认不出的 ID 一律留空，不猜。
SPEC_ID_MAP: dict[int, tuple[str, str]] = {
    # 死亡骑士
    250: ("death-knight", "鲜血"),
    251: ("death-knight", "冰霜"),
    252: ("death-knight", "邪恶"),
    # 恶魔猎手
    577: ("demon-hunter", "浩劫"),
    581: ("demon-hunter", "复仇"),
    # 唤魔师
    1467: ("evoker", "湮灭"),
    1468: ("evoker", "恩护"),
    1473: ("evoker", "增辉"),
    # 德鲁伊
    102: ("druid", "平衡"),
    103: ("druid", "野性"),
    104: ("druid", "守护"),
    105: ("druid", "恢复"),
    # 猎人
    253: ("hunter", "野兽控制"),
    254: ("hunter", "射击"),
    255: ("hunter", "生存"),
    # 法师
    62: ("mage", "奥术"),
    63: ("mage", "火焰"),
    64: ("mage", "冰霜"),
    # 武僧
    268: ("monk", "酒仙"),
    269: ("monk", "踏风"),
    270: ("monk", "织雾"),
    # 圣骑士
    65: ("paladin", "神圣"),
    66: ("paladin", "防护"),
    70: ("paladin", "惩戒"),
    # 牧师
    256: ("priest", "戒律"),
    257: ("priest", "神圣"),
    258: ("priest", "暗影"),
    # 潜行者
    259: ("rogue", "奇袭"),
    260: ("rogue", "狂徒"),
    261: ("rogue", "敏锐"),
    # 萨满祭司
    262: ("shaman", "元素"),
    263: ("shaman", "增强"),
    264: ("shaman", "恢复"),
    # 术士
    265: ("warlock", "痛苦"),
    266: ("warlock", "恶魔学识"),
    267: ("warlock", "毁灭"),
    # 战士
    71: ("warrior", "武器"),
    72: ("warrior", "狂怒"),
    73: ("warrior", "防护"),
}


def localize_spec_id(spec_id: Any) -> dict[str, Any]:
    """Map a client-reported spec id to Chinese role info.

    Unrecognised ids return empty fields rather than a guess — a wrong spec is
    worse than an absent one when the report reasons about class abilities.
    """
    try:
        key = int(spec_id)
    except (TypeError, ValueError):
        return {"class": "", "spec": "", "label": ""}

    entry = SPEC_ID_MAP.get(key)
    if entry is None:
        return {"class": "", "spec": "", "label": ""}

    class_key, spec_cn = entry
    class_cn = CLASS_MAP[class_key]["cn"]
    return {"class": class_cn, "spec": spec_cn, "label": f"{spec_cn}{class_cn}"}


def _match_class(value: str) -> str | None:
    lowered = value.lower()
    for class_key, info in CLASS_MAP.items():
        if info["cn"] in value:
            return class_key
        if any(alias in lowered for alias in info["aliases"]):
            return class_key
    return None


def _match_spec(class_key: str, value: str) -> str:
    lowered = value.lower()
    for spec_alias, spec_cn in SPEC_MAP[class_key]:
        if spec_cn in value or spec_alias in lowered:
            return spec_cn
    return ""


def localize_role(value: str) -> dict[str, Any]:
    """Map a WCL class/spec string to Chinese role information."""
    text = str(value or "").strip()
    if not text:
        return {"class": "", "spec": "", "label": ""}

    class_key = _match_class(text)
    if not class_key:
        return {"class": "", "spec": "", "label": text}

    class_cn = CLASS_MAP[class_key]["cn"]
    spec_cn = _match_spec(class_key, text)
    label = f"{spec_cn}{class_cn}" if spec_cn else class_cn
    return {"class": class_cn, "spec": spec_cn, "label": label}


def is_mythic_plus(fight: Any) -> bool:
    """Whether a WCL fight is a Mythic+ dungeon run rather than a raid encounter.

    `keystoneLevel` 只有大秘境才有（团本是 null），是最干净的判据 ——
    比看 zone、看人数、看时长都可靠。

    放在这个模块是因为它无依赖，`prompt.py` / `aggregation.py` / `deepseek_agent.py`
    都要用它，而它们之间不该互相 import。
    """
    if not isinstance(fight, dict):
        return False
    return fight.get("keystoneLevel") is not None


#: 词缀 id → 中文名的持久化缓存。
#:
#: 名字只有国服端点有（见 `wcl_v2.affix_names`），而 V1 时代的历史缓存里
#: 只有词缀 **id**（`affixes: [10, 9, 147]`）。把取到的表落一份到磁盘，
#: 历史缓存也能解析出中文名——聚合层只读文件，不发网络请求。
AFFIX_CACHE_PATH = Path(__file__).resolve().parents[1] / "storage" / "affixes.json"


def load_affix_names() -> dict[int, str]:
    """读词缀中文名对照表。文件不存在或损坏时返回空字典。"""
    try:
        payload = json.loads(AFFIX_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    names: dict[int, str] = {}
    for key, value in payload.items():
        try:
            names[int(key)] = str(value)
        except (TypeError, ValueError):
            continue
    return names


def save_affix_names(mapping: Mapping[int, str]) -> None:
    """落盘词缀对照表。失败不影响主流程（只是下次还得重新取）。"""
    if not mapping:
        return
    merged = load_affix_names()
    merged.update(mapping)
    try:
        AFFIX_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        AFFIX_CACHE_PATH.write_text(
            json.dumps({str(k): v for k, v in sorted(merged.items())},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass

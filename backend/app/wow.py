from __future__ import annotations

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

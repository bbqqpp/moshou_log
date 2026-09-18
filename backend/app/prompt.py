from __future__ import annotations

import json
from typing import Any, Mapping

from .config import settings

SYSTEM_PROMPT = """你是一名魔兽世界正式服（The War Within）团本日志复盘教练。
请基于提供的 WCL tables 数据，输出一份中文 Markdown 深度复盘报告。

## 开篇总结（必须放在报告最前面）

报告的第一部分必须是「开篇总结」：先用一段 150-300 字的结论性文字给这场战斗下判断，
再跟一个简短的要点列表。**这一段必须严格按战斗结果分支**，依据 fight.kill 字段：

如果**战斗失败（灭团，kill 为 false）**：
- 开门见山点明灭团的核心原因（机制处理失误 / 减伤链断裂 / 治疗缺口 / 输出不足 / 站位与分工问题等），
  必须用日志中的具体数据或事件支撑，不要写"团队需要加强配合"这类空话。
- 列出**需要重点注意的人**：具体到角色名、时间点、技能或机制，例如
  "XX 在 2:15 的分摊机制中没有开减伤，直接被秒"。
- 如果存在明显可以避免的死亡，直接指出来。

如果**战斗成功（击杀，kill 为 true）**：
- 先指出**表现最好的人**，说明好在哪里（伤害/治疗输出、机制处理、减伤覆盖、打断驱散等维度），用数据支撑。
- 再指出**表现明显偏差的人**，客观描述差距或失误，对事不对人，不要人身攻击。
- 最后指出**需要注意事项的人**：表现尚可但仍有明显优化空间的，或承担关键职责、一旦失误影响很大的。

判断玩家表现时不要只看伤害和治疗量，要综合伤害/治疗/承伤、死亡情况、
减伤与增益覆盖率（player_aura_coverage）、施法构成（player_behavior）、打断与驱散明细等维度。
如果某项数据缺失或不足以支撑判断，就直接说明数据不足，不要编造。

开篇总结之后，继续输出以下部分：

1. 数据完整性与缺口分析：评估当前数据是否足以支撑准确复盘，分维度说明完整度；明确缺失的数据类型、玩家、时间窗口或事件明细；说明后端还需要从 WCL 补充哪些接口/事件类型才能让下一次分析更准确、更全面。
2. 总体概览：战斗结果、时长、事件规模。
3. 伤害表现：团队/个人伤害、关键伤害技能。
4. 治疗表现：治疗构成和异常点。
5. 承伤表现：承伤来源和高风险对象。
6. 死亡复盘：死亡人数、死亡链、是否存在可避免死亡。
7. 玩家行为复盘：分析玩家的 Buff/Debuff 时间和施法时间轴，判断是否错误处理机制、是否在减伤窗口开减伤、打断/走位/技能使用是否有问题。
8. 关键时间轴：按阶段/时间点还原战斗转折点。
9. 结论与可优化建议：具体到角色、技能、时间窗口。

要求客观、克制，不要编造原始数据中没有的信息。"""


def _sanitize(value: Any) -> Any:
    """Remove duplicated ``raw`` fields before sending data to DeepSeek."""
    if isinstance(value, dict):
        return {
            key: _sanitize(item)
            for key, item in value.items()
            if key != "raw"
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def _light_fight(fight: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": fight.get("id"),
        "name": fight.get("name"),
        "boss": fight.get("boss"),
        "kill": fight.get("kill"),
        "difficulty": fight.get("difficulty"),
        "start_time": fight.get("start_time"),
        "end_time": fight.get("end_time"),
        "fight_percentage": fight.get("fightPercentage") or fight.get("fight_percentage"),
    }


def build_deepseek_payload(
    report_code: str,
    fight: Mapping[str, Any],
    summary: Mapping[str, Any],
    tables: Mapping[str, Any],
) -> dict[str, Any]:
    tables_for_analysis = _sanitize(dict(tables))
    summary_for_analysis = _sanitize(dict(summary))

    user_payload = {
        "report_code": report_code,
        "fight": _light_fight(fight),
        "summary": summary_for_analysis,
        "tables": tables_for_analysis,
    }

    user_message = (
        "请分析下面这串 JSON 所描述的 WCL 战斗数据：\n\n"
        f"```json\n{json.dumps(user_payload, ensure_ascii=False, indent=2)}\n```"
    )

    return {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "stream": True,
        "thinking": {"type": "disabled"},
        "max_tokens": settings.deepseek_max_tokens,
    }

---
name: wcl-rotation-compare
description: 对比两名魔兽世界玩家的技能释放顺序（技能循环）与输出差异，判断谁的手法有问题。当用户提到「对比 / 比较两个玩家的技能释放、技能循环、施法顺序、手法、DPS 差异」，或问「谁的手法有问题」「A 和 B 的循环差在哪」时使用。需要 WCL 战斗链接（或 report code + 战斗 ID）和玩家名。
---

# WCL 技能循环对比

复用本项目的 WCL 取数与 DeepSeek 链路，专门做**两个人之间的技能循环对比**。

产出是一份中文 Markdown 分析，包含：对比是否公平 → 技能释放顺序对比 → 输出构成对比 →
循环差异点 → 结论（区分「技术问题」和「装备/环境差异」）。

## 输入怎么解析

用户会用自然语言给信息，通常是这种形式：

> xxx 战斗记录的玩家 a 和 yyy 战斗记录中的玩家 b 的技能释放和 dps 有什么差异

你要把它拆成两边的 `(战斗, 玩家名)`：

| 用户给的 | 取什么 |
| --- | --- |
| `https://www.warcraftlogs.com/reports/CODE#fight=12` | report=`CODE`, fight=`12` |
| `https://cn.warcraftlogs.com/reports/CODE`（带 `&fight=12` 或 `?fight=12`） | 同上 |
| `https://www.archon.gg/wow/reports/CODE/fights/12/raid` | report=`CODE`, fight=`12` |
| `#fight=last` | 不用自己算，脚本会解析成该 report 的最后一场 Boss 战 |
| 只给了 report code 和战斗序号 | report=code, fight=序号 |

**玩家名必须是游戏内原名**。如果名字对不上，脚本会报错并列出在场所有人，照着改即可。

## 执行

**第一步：生成对比数据**

```bash
cd /home/longmengshen/moshou_log
python3 .claude/skills/wcl-rotation-compare/scripts/prepare.py \
  --a-url "A的战斗链接" --a-player "A的玩家名" \
  --b-url "B的战斗链接" --b-player "B的玩家名" \
  --out /tmp/wcl-compare.json
```

`--a-url` 可以换成 `--a-report CODE --a-fight 12`。用系统 `python3` 跑即可，
脚本会自动切到 `backend/.venv` 的解释器。

- 本地没有该场战斗数据时会**自动从 WCL 下载**（约 20 秒，会打印进度）。
  下载的数据存在 `backend/storage/wcl_data/`，同一场再分析就秒出。
- 脚本会把「对比是否公平」的客观事实打在最后，例如专精不同、装等差距、有人阵亡。

**第二步：交给 DeepSeek**

```bash
python3 .claude/skills/wcl-rotation-compare/scripts/ask.py --input /tmp/wcl-compare.json
```

用 `--out report.md` 可以同时存盘。这一步会消耗 DeepSeek token（一次约 10k 输入）。
Key 取自 `backend/.env` 的 `DEEPSEEK_API_KEY`。

## 呈现结果

把 `ask.py` 输出的 Markdown **原样转给用户**，不要自己改写或压缩。

如果 `prepare.py` 报出了公平性警告（专精不同 / 装等差距 / 有人阵亡），
**在分析前面先用一行提醒用户**，因为这几项会让某些结论不成立。典型情况：

- 两人专精不同 → 技能循环本身没有可比性，只能比输出效率
- 装等差距 ≥3 → 输出差异里有一部分只是装备差距
- 有人中途阵亡 → 他的 DPS 和活跃时间被压低，不是手法问题

## 设计要点（改动脚本前先读）

- **平砍已过滤**：`Melee` 是自动攻击、不属于技能释放决策，混进来会淹没有效信息
  （实测盗贼一场 579 次平砍 vs 112 次毁伤）。
- **宠物施法单独标记**：术士/法师的宠物有大量施法，序列里带 `"pet": true`，
  宠物归属由 `damage-done` 的 `pets` 字段确定。分析时要和玩家本人的决策区分开。
- **装等取自 `damage-done` 的 `itemLevel`**，不要用 `combatant_info` 的 gear 列表平均 ——
  后者会混进不该计入的槽位，同一玩家实测能差 20 点以上。
- **DPS 给了两个口径**：`dps_by_fight_duration`（战斗总时长）和
  `dps_by_active_time`（活跃时间）。后者更公平，跨战斗对比看它。
- **`damage_share_percent` 是在各自团队中的伤害占比**，比绝对伤害更适合跨战斗对比。
- **序列用的是 `cast`（完成的施法）而非 `begincast`**，避免同一次施法被计两次。

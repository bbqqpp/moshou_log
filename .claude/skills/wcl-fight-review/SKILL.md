---
name: wcl-fight-review
description: 对一场魔兽世界 WCL 战斗日志跑完整复盘，生成中文深度分析报告（伤害/治疗/承伤/死亡/机制/时间轴）。当用户说「分析这场战斗」「复盘这场」「看看这场为什么灭团/打得怎么样」「分析这个 WCL 链接」并给出单条战斗链接时使用。如果要对比两个玩家，用 wcl-rotation-compare。
---

# WCL 单场战斗复盘

网页版应用的等价命令行流程：**拉全量数据 → 聚合摘要 → 交给 DeepSeek（带本地查询工具）生成整场复盘**。

## 触发场景

用户给**一条**战斗链接，想了解整场表现：

> 分析一下这场 https://www.warcraftlogs.com/reports/CODE#fight=12

> 这场为什么灭团？

> 复盘一下这场

**如果用户要对比两个人**，用 `wcl-rotation-compare` skill，不要用这个。

## 输入

| 用户给的 | 取什么 |
| --- | --- |
| `https://www.warcraftlogs.com/reports/CODE#fight=12`（或 `?fight=12`） | 直接用 |
| `https://cn.warcraftlogs.com/...` | 直接用 |
| `https://www.archon.gg/wow/reports/CODE/fights/12/raid` | 直接用 |
| `#fight=last` | 直接用，脚本会解析成该 report 的最后一场 Boss 战 |
| 只给了 report code | 需要问用户是哪一场（或提议用 `fight=last`） |

## 执行

```bash
cd /home/longmengshen/moshou_log
python3 .claude/skills/wcl-fight-review/scripts/review.py \
  --url "用户的链接" --out /tmp/review.md
```

用系统 `python3` 跑即可，脚本会自动切到 `backend/.venv` 解释器。

- 本地没有该场数据时会**自动从 WCL 下载**（约 20 秒）。数据存在 `backend/storage/wcl_data/`。
- DeepSeek 分析需要 **1-3 分钟**（模型会按需调用工具查本地数据，最多 6 轮）。
- **会消耗 DeepSeek token**（首轮约 58k 输入）。同一场的结果会缓存。

## 呈现结果

把 `review.py` 输出的 Markdown **原样交给用户**，不要自己压缩或改写。

报告结构（由后端提示词控制，不要自行调整）：

1. **开篇总结** —— 只讲正面表现；灭团时说明核心原因（团队/机制层面，不点名批评个人）
2. 数据完整性与缺口分析
3. 总体概览 / 伤害 / 治疗 / 承伤
4. 死亡复盘
5. 玩家行为复盘（机制处理、减伤、打断、走位）
6. 关键时间轴（按「第X阶段」划分）
7. 结论与可优化建议

## 相关工具

本项目的 MCP server（`.mcp.json` 里的 `wcl-analyzer`）提供了更细的查询能力。**做完复盘后如果用户想深挖某一项**，优先用 MCP 工具而不是重跑一次分析：

| 工具 | 用途 |
| --- | --- |
| `list_players` | 这场有谁，什么专精 |
| `query_data` | 查原始表/事件流（可按玩家、时间窗过滤） |
| `player_rotation` | 某人的施法序列、大招真实次数、资源曲线 |
| `compare_players` | 两人深度对比（含空窗交叉判定） |
| `boss_guide` | 这场 BOSS 的本地攻略（阶段/机制/灭团点） |

## 注意事项

- **数据是本地缓存的**。同一场重复分析不会重新下载；想拿最新数据要先删掉
  `backend/storage/wcl_data/{report}__{fight}.json`。
- **分析结果也有缓存**，存在 `backend/storage/analysis_cache/`，改了提示词后会自动失效
  （缓存签名包含模型和系统提示词）。
- 报告里如果出现阶段编号，它们来自 `boss_guide` 的本地攻略数据；某些 BOSS 没有攻略数据，
  这时报告会退化成基于机制时间的推断。

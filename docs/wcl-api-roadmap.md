# WCL 数据能力与接入路线图

> 记录 WCL 还能拿到哪些数据、以及要按什么顺序接进项目。防止后续遗忘。
> 最后更新：2026-09-21
>
> **所有标「实测」的结论都不是从 schema 推断的** —— schema 镜像已经错过好几次
> （`masterData` 不接受 `limit`、`ReportFightNPC` 没有 `name`、`trialScore` 不存在、
> `PhaseMetadata` 没有 `startTime`）。回归工具：`backend/scripts/verify_v2_parity.py`。

## 现状快照

**取数层已全面走 V2 GraphQL**（2026-09-20 完成迁移，V1 REST 路径已删除）：

- 传输：`backend/app/wcl_v2.py`，client credentials + `threading.Lock` token 缓存
- 编排：`backend/app/wcl.py`，对外仍返回 V1 形状（下游零改动、历史缓存继续可用）
- 凭证：`WCL_CLIENT_ID` / `WCL_CLIENT_SECRET`，token 有效期 **360 天**、配额 **3600 点/小时**
- 一次 `extract_fight` 发两类批量请求：10 张表合并成 1 个请求、5 项附加数据合并成 1 个

**已接入的数据**：原 18 个数据流（10 张表 + 8 条事件流）+ **5 项 V2 独有数据**
（parse 排名 / 角色档案 / 生存分 / 真实阶段 / 时间序列，见附录 B.5）。

**相关文件**：`backend/app/wcl.py`（取数编排 + 形状适配）、`backend/app/wcl_v2.py`（GraphQL 传输）、
`backend/app/aggregation.py`（派生数据）、`backend/app/boss_guides.py`（手写攻略，阶段部分已被真实数据取代）

---

## 待办总表

规模：**小** ≈ 改一个函数；**中** ≈ 新增模块 + 聚合逻辑；**大** ≈ 新模块 + 数据流改造 + 前端

### P0 · 现有 V1 key 就能做，直接提升复盘质量

- [ ] **P0-1 敌方视角**（`hostility=1`）— 小
      BOSS 对团队的伤害与技能构成。现在只拉友方侧，"谁被哪个技能打死的"看不到
- [ ] **P0-2 按技能分组**（`by=ability`）— 小
      技能级归因。现在拿的是按人聚合的粗表
- [ ] **P0-3 `summary` 视图** — 小
      文档 9 个视图里唯一没用过的。服务端直接给"按技能拆分的总伤/命中/暴击/未命中"，省掉自己聚合原始事件
- [ ] **P0-4 光环条件过滤**（`sourceAurasPresent/Absent`、`targetAurasPresent/Absent`）— 中
      "身上没有 X 的时候吃到 Y 伤害的人"。机制处理分析，服务端过滤，目前完全做不到
- [ ] **P0-5 场次过滤**（`wipes=1`、`cutoff`、`difficulty`、`death=N`）— 小
      只看灭团场次、只看第 N 次死亡
- [ ] **P0-6 抓取 actors** — 小
      `report/fights` 返回的 `friendlies`/`enemies`/`pets` 现在被丢弃。含 actorID→玩家/宠物映射，`pets` 带 `petOwner`；是比 combatantinfo 事件流更省的专精来源
- [ ] **P0-7 前端展示 icon 与装等** — 小
      表数据里**本来就带** `icon`、`abilityIcon`、`itemLevel`，前端目前一个都没用。纯前端改动，性价比最高

### P1 · 玩家/公会维度的数据（V1 未使用的端点）

- [ ] **P1-1 `/parses/character/{name}/{server}/{region}`** — 中
      角色历史 parse。让玩家报告从"这一场打得好不好"升级到"这个人的历史水平"。对 `player_report.py` 价值最大
- [ ] **P1-2 `/reports/guild/{guild}/{server}/{region}`** — 中
      公会报告列表（带时间范围）。登录本来就是按公会名做的，可以直接做成"从最近的公会团本里选一场"，不用每次贴链接
- [ ] **P1-3 `/rankings/encounter/{id}`** — 小
      遭遇战排行榜。支持 metric（dps/hps/bossdps/tankhps/speed/execution/feats）、difficulty、bracket、class、spec、server、region、`includeCombatantInfo`（带装备天赋）
- [ ] **P1-4 `/rankings/character/...`** — 小
      角色在各 BOSS 上的排名，支持 `timeframe=today|historical`
- [ ] **P1-5 `/zones`** — 小
      zone/encounter ID 表。Boss 元数据现在散落在 `boss_guides.py`，可以用它对齐
- [ ] **P1-6 `/classes`** — 小（**不一定要改**）
      能替代 `wow.py` 里 239 行硬编码映射。但硬编码省一次 API 调用且数据几乎不变，除非要支持多语言，否则维持现状也合理

### P2 · V2 接入（凭证已就绪，缺代码）

- [x] **P2-0 `backend/app/wcl_v2.py` 地基** — 中
      token 换取 + 内存缓存（按 360 天做，401 时重取）+ 最小 GraphQL 查询封装。**后面几项都依赖它**
- [x] **V1 → V2 取数层迁移**（2026-09-20）— 大
      V1 REST 路径已删除，18 个数据流全部走 V2，输出形状保持不变（下游零改动、
      936 MB 历史缓存继续可用）。三个真实样本逐条精确对拍通过，见附录 A。
      回归工具：`backend/scripts/verify_v2_parity.py`
- [x] **P2-1 `report.rankings(compare: Parses)`** — 中
      战斗内每个人的 parse 百分位（灰绿蓝紫橙）。**V1 没有任何等价物**，是接 V2 的最大理由。
      已接入：名册、玩家报告 payload、整场复盘提示词、前端徽标。见附录 B
- [x] **P2-2 `report.phases` / `fight.phaseTransitions`** — 中
      真实阶段划分和时间戳。已接入，替代手写的阶段表（`boss_guides.py` 的仍作为兜底）
- [x] **P2-3 `report.playerDetails(includeCombatantInfo: true)`** — 小
      已接入：专精、装等区间、**爆发药水与治疗石使用次数**
- [ ] **P2-4 `report.graph(viewBy, dataType)`** — 中
      ⚠️ **实测不可用**：同一查询有时返回完整序列、有时持续返回空，
      跨 dataType / 跨报告都如此，与配额无关。已接入取数但拿不到时显式标记不可用，
      前端据此隐藏。**要靠它做功能前需要先搞清楚 WCL 侧的行为**
- [ ] **P2-5 `rateLimitData`** — 小
      配额监控，避免 `events` 分页把点数烧完
- [x] **P2-6 `masterData`** — 小
      actor 名册（含 `petOwner` 宠物归属）已可用；ability 名字和图标走
      `useAbilityIDs: false` 直接拿到，不需要单独查 masterData
- [ ] **P2-7 报告检索** — 中
      `reportData.reports(zoneID:)` 可用；但**按公会名检索还需要 server slug**
      （实测只给公会名会报 `No guild exists`），而登录只收公会名

### P3 · 技术债与风险

- [ ] **P3-1 `interrupts` / `dispels` 表改走原始事件** — 小
      这两个视图在 V1 文档里没有，属于未文档化行为（详见"风险"）。迁移到 V2 后
      它们是 `TableDataType` 的正式成员，风险降低；但 `interrupt_events` /
      `dispel_events` 两条事件流已经是文档化的等价来源，仍值得收敛成一条路径
- [x] **P3-2 V2 events 的取数策略** — 中
      已定：`dataType: All` + 原 V1 表达式 + alias 批量，实测整场 40-60 点
      （≈70 场/小时）。**不能**照搬"每条流拆阵营"或类型化 dataType，理由见附录 A.3
- [x] **P3-3 长期迁移 V1 → V2** — 大
      已完成，见上面的迁移条目

---

## 关键细节

### P0-4 光环条件过滤为什么值得做

这是**服务端过滤**能力，客户端补不回来。典型用法：

- `targetAurasAbsent=<减伤buff ID>` + `type in ("damage")` → 找出"没开减伤就吃到致死伤害"的人
- `sourceAurasPresent=<爆发buff ID>` → 只统计爆发期内的输出

现在的做法是把整场 buff 事件拉下来在本地关联，既费流量又难写对。

### P0-1 / P0-2 的具体参数

`hostility`：0 = 友方（默认），1 = 敌方。
`by`：`source` | `target` | `ability`，对应网站上表格的默认分组方式。

两者在 `report/tables` 和 `report/events` 上都可用。

### P2 的 V2 查询长这样

```graphql
query($code: String!) {
  reportData {
    report(code: $code) {
      fights { id name encounterID kill }
      rankings(compare: Parses, playerMetric: dps)   # ← parse 百分位
      phases { encounterID phases { id startTime } } # ← 真实阶段
    }
  }
}
```

注意 `rankings` 返回的是**全团**数据，需要按 fight 在前端/本地过滤。

---

## 风险与注意事项

### `interrupts` / `dispels` 属于未文档化行为

官方文档列出的 table/events 视图只有 9 个：
`summary`、`damage-done`、`damage-taken`、`healing`、`casts`、`summons`、`buffs`、`debuffs`、`deaths`。

项目在用的 `interrupts`、`dispels` **不在其中**——现在能用，但官方不承诺保留，哪天没了不会有通知。

而且 `tables` 端点文档原话是：

> It can and will change as the needs of those panes do, and as such should never be considered a frozen API.

**缓解方案**：这两个数据本来就有原始事件版本（`type in ("interrupt")` / `type in ("dispel")`），项目里的 `interrupt_events` / `dispel_events` 已经在拉了。改成只依赖事件流、去掉多余的 tables 调用即可（P3-1）。

### V1 整体已废弃

V1 swagger 文档开头就写着"v1 API has been deprecated and is no longer being actively developed，新应用应改用 v2 API"。

### 凭证说明

- **V1 key**：账号设置页底部（`www.warcraftlogs.com/accounts/changeuser`）的 Public key，与 V2 凭证**不是一回事**，V1 key 不能当 V2 secret 用
- **V2 凭证**：在 `www.warcraftlogs.com/api/clients/` 创建，**必须选 confidential**（勾了 "Public Client" 就拿不到 secret，只能用需要真人浏览器授权的 PKCE 流程）
- **国服限制**：WCL 目前不支持绑定国服战网账号。绕过办法是在同一 WCL 账号上额外绑一个受支持区服（如台服）再建 client
- 读**公开报告**只需 client credentials，走 `www` 站端点即可（国服报告也能读）；只有读**私密报告**才需要用户 OAuth，且 global / cn 要分别授权

### 数据来源可靠性

- V1 部分来自官方 swagger：`https://www.warcraftlogs.com/v1/docsjson`（可直连）
- V2 部分来自**同源的 GraphQL schema 镜像**（RPGLogs 家族，WCL/FFLogs/ESO 共用），枚举值与 WoW 对得上。官方文档站被 Cloudflare 保护（403），落代码前建议对着 [官方文档](https://www.warcraftlogs.com/v2-api-docs/warcraft/) 核一遍
- 文中"实测"字样（token 360 天、3600 点/小时）为 2026-09-20 实际调用结果

---

## 参考资料

- [WCL V1 API swagger](https://www.warcraftlogs.com/v1/docsjson) — 直连可用
- [WCL V2 API 官方文档](https://www.warcraftlogs.com/v2-api-docs/warcraft/) — 需浏览器打开
- [V2 client 管理页](https://www.warcraftlogs.com/api/clients/)
- [RPGLogs schema 镜像](https://raw.githubusercontent.com/ESO-Toolkit/eso-toolkit/refs/heads/main/public/schema.graphql)
- [warcraftlogs-mcp](https://www.npmjs.com/package/warcraftlogs-mcp) — 含国服绑定限制说明

---

## 附录 A：V1 → V2 实测字段映射（2026-09-20）

由 `backend/scripts/verify_v2_parity.py` 对拍真实缓存数据得出，**不是从 schema 推断的**。
样本：`bCqgAw26rDL7TxBc__11`（杂兵战）、`4jwhmPNMK2pQJnbq__13`（Boss 战，109k 光环事件）、`kpjAyZTN82Bt3ChL__78`。

### A.1 必须显式传的查询参数

不传这些会**静默**给出错误数据，没有异常、没有警告：

| 参数 | 值 | 不传的后果 |
|---|---|---|
| `translate` | **`false`** | 默认翻译成**站点语言**（www 站＝英文），把中文技能名/Boss 名全变成英文。`aggregation.py` 的机制识别规则按中文名硬编码且无 GUID 兜底 → 机制检测全灭 |
| `useActorIDs` | **`true`** | 默认返回嵌套的 `source: {id, name, ...}` 对象且**没有 `sourceID`** → 所有跨表 join 失败 |
| `useAbilityIDs` | **`false`** | 默认只给 `abilityGameID: 190336`，没有技能名和图标。传 `false` 才得到 V1 形状的 `ability: {name, guid, type, abilityIcon}` |
| `hostilityType` | **不用传** —— 见 A.3 | 类型化 dataType 默认只返回 `Friendlies`（`cast_events` 会从 10 条掉到 5 条）。改用 `dataType: All` 后默认就含双方，不需要这个参数 |
| `includeResources` | **`cast_events` 必须传 `true`** | 见 B.1。不传就丢 `itemLevel`（全团装等的唯一来源）、坐标、血量、资源 |

### A.2 响应形状差异

| 项 | V1 | V2 | 适配 |
|---|---|---|---|
| `table` 信封 | `{entries: [...]}` | `{data: {entries: [...]}}` | **解开一层 `data`**。`aggregation._entries` 不递归，不解包则**每张表静默返回空** |
| `events` 信封 | 顶层 `events` | `{data: [...], nextPageTimestamp: N}` | 取出 `data` |
| `fight.boss` / `originalBoss` | 同名 | `encounterID` / `originalEncounterID` | 改键名（**值完全一致**，已验证 0→0、3497→3497、3492→3492） |
| `fight.start_time` / `end_time` | 同名 | `startTime` / `endTime` | 改键名 |
| `fight.fightPercentage` / `bossPercentage` | **×100 的整数**（8563） | **浮点**（85.63） | **×100 取整** |
| `fight.zoneID` / `zoneName` | 在 fight 上 | 在 `fight.gameZone` 上 | 从 `gameZone` 取（注意 `report.zone.id` 是**另一个值**，53 vs 3004） |
| `fight.zoneDifficulty` | 有 | 无对应 | 全仓库无消费者，可省略 |
| 事件的 `ability` | `{name, guid, type, abilityIcon}` | 同左（需 `useAbilityIDs: false`） | 无需适配 |
| `buffs` 表容器键 | `auras`（非 `entries`） | 同左 | 无需适配 |

### A.3 事件流的取数方式

**结论：全部用 `dataType: All` + V1 当年的原始 `filterExpression` 原样复用**，
而不是 V2 的类型化 dataType（`Casts` / `Buffs` / …）。类型化 dataType 各自带着
网站面板的隐含过滤，与 V1 的原始过滤条件并不等价 —— 实测：

- ⚠️ **`dataType: Casts` 排除平砍**。V1 的 `cast_events` 含 `Melee`（实测 2 条），
  `Casts` 不含 → 条数从 10 掉到 8。
- ⚠️ **类型化 dataType 默认只返回 Friendlies**（`hostilityType` 默认值）。
  `cast_events` 会从 10 条掉到 5 条（BOSS 施法全丢），光环流会丢 20%。
- ✅ **`All` 默认就含双方**，不需要拆阵营再合并。`All` + 表达式返回的集合与
  「四 alias 合并」的原始结果逐条一致（小战斗 51、大战斗 118,276），但只需一个查询。

同理，**`dataType: DamageTaken` 不能用于死亡窗口** —— 实测只给 1 条（V1 是 7 条）。
窗口流里 78% 是 heal 和 absorbed，而 `absorbed` 在 V2 的 `EventDataType` 枚举里
**根本不存在**。

⚠️ **`events` 的 `targetID` 参数实测无效** —— 带与不带返回完全相同的结果集。
死亡窗口必须像 V1 一样在**本地**按 `targetID == 死者 actor_id` 过滤。

### A.4 三个必须显式传的参数（补充）

见 A.1。这三个不传都是**静默**出错：

- `translate: false` —— 保住中文技能名（默认翻译成站点语言）
- `useActorIDs: true` —— 要扁平的整数 ID
- `useAbilityIDs: false` —— 要 `ability: {name, guid, type, abilityIcon}`，
  否则只有 `abilityGameID`，技能名和图标全丢

**`sourceIsFriendly` / `targetIsFriendly` 需要自己推导** —— 传了 `useActorIDs: true`
之后 V2 事件里没有这两个标志。规则：

```
sourceIsFriendly = (sourceID ∈ fights.friendlyPlayers ∪ friendlyPets.id ∪ friendlyNPCs.id)
```

**只信友方名单**：实测敌方名单不全（有 `enemyNPCs` 没列出、但事件里确实存在的敌方
actor），所以判定是「在友方集合里 = 友方，其余一律非友方」。这条规则在
**31,378 条事件上零不符**。

### A.5 事件的其它差异

- **V2 是 V1 的超集**：大战斗光环流 V2 118,276 条 vs V1 109,382 条，十种类型
  V2 都更多。V1 的 `_get_filtered_events` 去重键过激，会吃掉真实事件
  （`resourcechange` 实测 1 vs 2）
- 事件**去重仍需要**：WCL 会返回键相同的事件（实测同一时间戳同一技能 3 条），
  V1 也把它们折叠成了 1 条。跨窗口/跨 alias 也要去重，否则重叠的死亡窗口会把
  同一批事件重复计入（实测 3,322 → 106,069）
- 少数表字段 V2 没有：`damage-taken` 的 `blocked`/`tmi`/`efftmi`、
  `buffs` 的 `stackUptime`。**全仓库无消费者**，不影响功能

### A.6 点数预算（实测）

- 10 张表：分开发 9 点 → **alias 批量合并成 1 个请求**后只剩零头
- 大战斗整场 18 个数据流 ≈ **40-60 点** → 3600 点/小时 ≈ **70 场/小时**

### A.7 分页

`nextPageTimestamp` 语义与 V1 相同。大战斗光环流翻 12 页共 118,276 条，
约 8,000-10,000 条/页。

### A.8 对拍结果（形状门）

`verify_v2_parity.py --stage extract` 在三个真实样本上**逐条精确一致**：

| 样本 | 光环 | 施法 | 资源 | 召唤 | 死亡窗口 |
|---|---|---|---|---|---|
| `4jwhmPNMK2pQJnbq__13` | 109,382 = 109,382 | 30,976 = 30,976 | 10,025 = 10,025 | 1,684 = 1,684 | 3,322 = 3,322 |
| `kpjAyZTN82Bt3ChL__78` | 21,210 = 21,210 | 5,120 = 5,120 | 2,198 = 2,198 | 293 = 293 | 2,609 = 2,609 |
| `bCqgAw26rDL7TxBc__11` | 43 = 43 | 10 = 10 | 1 = 1 | 0 = 0 | 15 ≥ 7 |

10 张表的条数同样全部一致。`fightPercentage` 的 ×100 换算、`boss`/`originalBoss`
映射、`band` 的绝对时间、`killingBlow` 形状均在样本上验证通过。

---

## 附录 B：第二批实测结论（2026-09-21）

接 parse 百分位 / 阶段 / 角色档案 / 生存分时踩到的坑。**每条都是静默失败**——
不报错，只是数据悄悄变空或变错。

### B.1 `includeResources` 不是可选项，是 V1 等价性的一部分

V2 的事件**默认只返回 9 个字段**：

```
ability, fight, sourceID, sourceInstance, sourceIsFriendly,
targetID, targetIsFriendly, timestamp, type
```

而 V1 返回 **24 个**：多出 `itemLevel`、`hitPoints`、`maxHitPoints`、`classResources`、
`attackPower`、`spellPower`、`armor`、`absorb`、`avoidance`、`versatility`、
`facing`、**`x` / `y`（坐标）**、`mapID`、`resourceActor`。

加上 `includeResources: true` 才能拿回来。**其中 `itemLevel` 是代码真正依赖的**
——`aggregation._item_levels_by_actor` 靠它算全团装等，缺了会让名册的装等静默变成
`None`（前端那一列直接空掉）。这个坑对拍条数发现不了，因为条数完全一致。

**只给 `cast_events` 开**：光环流一场有十万条事件，给每条都挂上这些字段会让缓存
文件成倍膨胀，而聚合层并不读光环事件的资源字段。

顺带：`x`/`y` 坐标意味着**走位分析**是可做的（V1 也有，但项目一直没用）。

### B.2 `rankings.id` 不是战斗内 actor ID

| 来源 | 阿唔 的 id |
|---|---|
| `report.rankings[].id` | **112272952**（WCL 角色 canonical ID）|
| `damage-done[].id` / 事件 `sourceID` | **15**（战斗内 actor ID）|
| `playerDetails[].guid` | 87549933（游戏 GUID，**既不是上面任何一个**）|

三者互不相同，`guid` 与 canonical ID 的交集实测为 **0**，所以 guid 不能当桥。
**唯一可靠的对齐键是角色名**（`playerDetails` 同时带战斗内 id 和名字）。

不换 id 的后果：名册和玩家 payload 里的 parse 百分位**全部静默查不到**，
看起来就像「这场没有排名」。

重名（同一场两个同名角色）时**宁可不匹配也不猜**——猜错会把别人的分数安到
这个人头上，比缺失更糟。

### B.3 `report.graph` 实测不可用

同一个查询（同一场、同一 dataType）：

- 有时返回完整的逐时间片序列（实测拿到过 22 条 series、34 个点、`pointInterval` 48s）
- 有时持续返回 `{series: []}`，跨 dataType（DamageDone / Healing / DamageTaken）、
  跨报告都如此，**与点数配额无关**（当时只用了 130/3600）

已接入取数，但拿到空时**显式标记 `available: False`** 而不是返回空列表——
后者会让前端画出一张没有数据的空图表。

另外 `pointInterval` 由 WCL 自己决定，实测一场 8 分钟的战斗只给 34 个点（约 12 秒一格），
没有参数可以调细。

### B.4 阶段数据在两处，要拼起来

| 字段 | 内容 |
|---|---|
| `fight.phaseTransitions` | `[{id, startTime}]` —— 哪个阶段从哪一毫秒开始 |
| `report.phases[].phases` | `[{id, name, isIntermission}]` —— 阶段叫什么 |

`PhaseMetadata` **只有** `{id, name, isIntermission}`，没有 `startTime`（schema 镜像说错了）。

实测一个转阶段型 BOSS 的 `phaseTransitions` 是 `1→2→1→2→1→2→1→2→1`——
**同一个 phase_id 会出现多次**，要按出现顺序逐段列出，不能按 id 去重。

**V1 的历史缓存也有阶段时间**：放在 `fight.phases` 下，形状与 `phaseTransitions`
完全相同（实测 `[{id: 1, startTime: 90372701}]`）。只读 V2 的键名会让所有历史战斗
白白丢掉阶段时间。两个都读，历史数据缺的只是阶段**名字**。

### B.5 新增的 `tables` 键

`extract_fight` 现在返回 **23** 个键（原 18 + 5）：

| 键 | 形状 | 说明 |
|---|---|---|
| `rankings` | `{available, entries: [...], encounter, duration, deaths}` | 每人的 parse 百分位。**灭团场次为空**（WCL 只给击杀排名）|
| `player_details` | `{available, entries: [...]}` | 专精 / 装等 / **爆发药水、治疗石使用次数** |
| `survivability` | `{available, entries: [...]}` | 0-1 生存分，**相对同专精**算 |
| `phases` | `{available, phases: [{id, name, isIntermission}]}` | 阶段定义 |
| `graph` | `{available, series: [...]}` | 时间序列（不可靠，见 B.3）|

⚠️ **`aggregation._total_entries` 必须只统计原来那 18 个键**。新键也是
`{"entries": [...]}` 形状、也能被 `_entries` 读到，但它们不是战斗事件——
混进去会让前端「事件数量」那个卡片莫名其妙地变大。

⚠️ 这几个键是一次请求批量取回的（alias 批量化），失败时**不影响主流程**，
只记一条 warning。

### B.6 `wcl_data_store` 的「键不存在」与「内容为空」要分开

`rankings` / `survivability` / `player_details` 是迁移到 V2 之后才有的，
V1 时代的历史缓存里没有。查询时返回 `found=False` 并说明「这份缓存太旧，
重新分析一次就有」——**不能静默返回 0 条**，否则模型会得出「这场没有 parse 排名」
的结论，而事实只是缓存旧。

---

## 附录 C：大秘境（M+）的现状（2026-09-21）

### C.1 能分析，但有两处曾经是错的

战斗数据完全正常：5 人小队的伤害/治疗/承伤/死亡/光环/施法全部拉到，
新增的生存分、药水次数、专精也都有。实测一场 12 层（夺目谷，24 分钟）：
74,470 条事件、3 次死亡、52,550 条光环事件。

但有两处曾经不对：

**1. parse 百分位对 M+ 是误导的**（已修）

实测 WCL 对一场限时通关的大秘境，给**每个人**都返回：

```
飞鹰凝固汽油  rankPercent=100  rank=~1  bracketData=12  totalParses=11880
乐芙兰      rankPercent=100  rank=~1  bracketData=12  totalParses=46517
```

`bracketData` 是**钥匙层数**（12）而不是装等区间。把 100 当成
「同装等区间前 1%」展示、或者按提示词里「90 = 前 10%」去叙述，
会得出「五个人都完美发挥」的荒谬结论。

**现在大秘境不产出 `parse_ranking`**，改为产出一条 `parse_ranking_note`
说明原因（前端把它当空状态文案，提示词让模型读它而不是自己猜原因）。

**2. M+ 元数据曾经全丢**（已修）

迁移时只挑了 `ReportFight` 的一部分字段，丢掉了 14 个键
（V1 时代是原样透传、白捡的）。已补回：
`keystoneLevel`、`keystoneAffixes`、`keystoneBonus`、`keystoneTime`、
`rating`、`countReached`、`countRequired`、`dungeonPulls`、
`npcCountMap`、`boundingBox`、`maps`、`averageItemLevel`、`friendlySpecs`、
`completeRaid`、`hardModeLevel`、`layer`。

**`keystoneLevel` 是干净的 M+ 判定信号**（团本为 null）。

⚠️ V2 的 `ReportFight` **没有** `completionTime` / `medal` / `partial` /
`zoneDifficulty` / `dungeonReplay` —— 这几个 V1 有，V2 要用
`keystoneTime` / `boundingBox` 等替代（schema 镜像又一次说错了）。

### C.2 还没做但可做

- **`dungeonPulls`（每次小怪拉怪的分段）已经拿到**（实测 11 段，含名字和击杀与否），
  但还没接进分析。这是 M+ 最有价值的维度——「哪波小怪灭了」现在完全看不到
- **`boss_timeline` 对 M+ 名不副实**：它按 `sourceIsFriendly = false` 收集敌方施法，
  在副本里收的是**全部小怪**的施法（实测顶到 1000 条上限），不是 BOSS 时间轴。
  这是迁移前就有的行为，不是本次引入
- **提示词是团本框架**：讲「阶段」「灭团原因」「团队承伤」，
  对 5 人本不完全贴切；`find_boss_guide` 对副本 ID 也匹配不到攻略（实测 `boss=12859` 无攻略）
- **「击杀/灭团」标签**对 M+ 语义不对（应该是「限时/超时」）

---

## 附录 D：大秘境与团本分流（2026-09-21）

### D.1 为什么必须分流

实测把一场大秘境的 DeepSeek payload 构造出来扫描，「团本字样」的计数：

| 位置 | 改造前 |
|---|---|
| SYSTEM | `阶段`×26、`boss_guide`×8、`灭团`×8、`BOSS`×5、`团队`×5、`攻略`×4、`mechanic_hits`×2 |
| USER | **`Boss`×3996**、`parse`×3、`boss_guide`、`mechanic_hits`、`腐蚀浪潮`、`raid` |

那 3996 个 "Boss" 来自 `boss_timeline` —— 它把**每一只小怪的施法**都标成
`"Boss 施放 X"`（117,658 字符），`timeline` 又照抄了一份（116,151 字符）。
模型收到的世界观是「BOSS 释放了 3996 次技能」。

### D.2 设计原则：靠数据缺席，不靠禁止句

**不在提示词里写**「不要提阶段」「忽略 boss_guide」这类禁止句 —— 那等于把
这些概念塞进大秘境提示词的语义空间，反而诱导模型往团本叙事靠。
写这版时我自己违反了两次（初稿里有「与团本不同…」「价值远高于团本」），
扫描时被自己的测试抓出来。

正确做法：
1. **聚合层就不产出**团本专有的键
2. **payload 里连键都不出现**（不是 `null` —— 那也带概念）
3. **提示词只正面描述**大秘境的分析框架

### D.3 分流点

| 层 | 团本 | 大秘境 |
|---|---|---|
| 判定 | — | `keystoneLevel is not None`（`wow.is_mythic_plus`）|
| 聚合 | `mechanic_hits` / `boss_timeline` / `phases` / `parse_ranking` | 不产出这四键；改为 `keystone` / `pull_summary` / `time_accounting` |
| `timeline` | `deaths` + 敌方施法 | `deaths` + **拉怪段边界**（`_pull_timeline`）|
| `_light_fight` | `boss`/`kill`/`difficulty`/`fight_percentage` | `keystone_level`/`affixes`/`timed`/`bonus`/`rating`/进度/`pull_count` |
| DeepSeek payload | `boss_guide`（可为 null） | **不带 `boss_guide` 键** |
| 提示词 | `RAID_SYSTEM_PROMPT` 等三份 | `MYTHIC_PLUS_*` 三份（**独立撰写，不是追加**）|
| 前端标签 | 击杀 / 灭团 | 限时 / 超时 |

**缓存签名**（`cache._signature`）把两个提示词都哈希进去。这样不用给
`get()` / `read_record()` / `list_all()` 加 fight 参数，而且加入大秘境提示词这件事
本身就会让**上一轮用团本提示词生成的大秘境报告正确失效**。

### D.4 大秘境专有的数据

| 字段 | 实测 |
|---|---|
| `keystone` | 层数、词缀（**中文名**，见下）、`timed`（`keystoneBonus > 0`）、评分、进度 |
| `pull_summary` | 11 段拉怪。**`is_boss` 的判据是 `encounterID != 0`**（实测正好切出 4 段 = 4 个 BOSS）——**不能用 `kill`**，小怪段的 `kill` 恒为 false |
| `time_accounting` | 拉怪 1325s / 总 1432s → **107s 非战斗时间**；`slowest_pulls` 最拖的三段 |

**词缀中文名**：`gameData.affixes { id name }` 在 **cn 端点**返回中文
（`10=强韧`、`9=残暴`、`147=萨拉塔斯的狡诈`），www 端点返回英文。
`gameData` 在 `reportData` 之外、不接受 `translate`，本地化完全取决于站点。
进程内缓存（一个赛季才变一次），取不到时降级为英文名，绝不返回数字 ID。

### D.5 验收方式

`backend/tests/test_mythic_plus.py` 里的**污染扫描**是核心验收：

- 断言大秘境 payload 里 `团本/团队/BOSS/Boss/阶段/灭团/攻略/boss_guide/wipe_reasons/checklist/mechanic_hits/腐蚀浪潮/parse/raid/击杀` **出现 0 次**
- **反向断言**团本 payload 里这些词仍在 —— 没有这条，把团本也改成大秘境提示词测试照样全绿
- 断言三份 M+ 提示词文本里不含"团本/阶段/灭团/parse"等词（自洽性，不是打了补丁）

### D.6 还没做的

- `dungeonPulls` 目前只进了 summary 与提示词，**没有做逐段的伤害/治疗切片**
  （比如"第 5 段的 DPS"）。数据都在，`pull_summary` 已经有起止时间
- 词缀的**机制解读**没有内置知识，模型只能靠 `affixes` 的名字自己推
- 大秘境没有 `boss_guide` 可用，副本的常见灭团点/路线建议全靠模型自身知识

---

## 附录 E：两轮代码审查的发现（2026-09-21）

用两个 agent 分别从「静默故障」和「M+/团本双路径一致性」两个角度审了全项目，
20 条发现，全部在真实缓存上复现过。**已修的见下，未修的按严重度留档。**

### E.1 已修

**这一轮大秘境改动引入的回归（6 条）**

| 问题 | 症状 |
|---|---|
| `_light_fight` 与 `summary.keystone` 各读一遍键名 | 同一份 payload 里 `fight.affixes=[]` 却 `summary.keystone.affixes=[强韧,残暴,…]`；`timed` 一处 false 一处 null |
| skill 脚本无条件用团本提示词 | `/wcl-rotation-compare` 跑大秘境时，payload 里既没有 `phases` 也没有 `parse`，提示词却在讲它们 |
| M+ summary 缺 `parse_ranking_note` | 前端显示团本兜底文案「灭团场次没有 parse 排名」（把限时通关说成灭团） |
| MCP `boss_guide` 无副本守卫 | 对副本返回团本攻略语义 |
| `main.py` 抽取 warning 对副本说「第 N 场 Boss 战（击杀）」 | 前端已用 `fightOutcome` 修过，后端没跟上 |
| 一条测试是空转的 | 断言常量真值，改名删名都照样绿 —— 而它本该拦住上面那条 skill 的破口 |

**既有 bug（4 条，本轮批准修的）**

| 问题 | 实测 | 修法 |
|---|---|---|
| **团本时间轴丢掉真 BOSS 施法** | 528 次敌方施法只留 217；丢的是祖尔加(177)、玛拉卡斯(134)——它们靠**自我治疗**出现在 `healing` 表里，被当成友方 | 只在事件缺 `sourceIsFriendly` 时才退回目录判断；标签改用实际单位名 |
| **`query_data(player=<数字>)` 按值相等匹配** | `"100"` 返回 9485 条（真值 581），8904 条来自 `classResources[0].max == 100` | 数字按 actor id 解释且必须存在；文本兜底只比字符串 |
| **`first_seen_ms` 取的是最后一个 band 的起点** | 全库 35 份缓存 2341 行错值（「水之护盾」三段起于 0/447982/449613，报 449613） | 改成 `min`（`last_seen` 同理改 `max`） |
| **找不到玩家返回 `found: true, matched_count: 0`** | 模型写成「该玩家全程没有输出」——纯粹由查询失败编出的结论 | 返回 `found: false` + 在场名单 |

顺带修了 actor 目录的污染：`_visit_actor_catalog` 没跳过 `gear`/`talents`，
装备和天赋被当成「在场的人」（实测一场团本 270 个条目，混着「伤员外衣」「充能沙石指环」），现在 83。

### E.2 未修

（本轮已全部处理，见 E.3 / E.4。剩余可做但不紧急的见 E.5。）

### E.3 已修：死亡归因（`killingBlow`）

**问题**：WCL 的 `deaths` 表里**根本没有击杀者字段**，只有：
- 死者（`name`/`id`/`type`）
- 致死技能（`killingBlow: {name, guid, type, abilityIcon}` —— `name` 是**技能名**）
- 承伤统计（`damage: {total, abilities: [...]}`）
- 内嵌的死亡窗口事件（`events`）

代码把 `killingBlow.name` 当成击杀者（`source`），把 `ability` 一路回退到 `entry["type"]`（**职业名**）。
实测（`kpjAyZTN83...` 盘卷祭坛）：

| | 修复前 | 修复后 |
|---|---|---|
| 羽阁 | `source='Unknown'`、`ability='Mage'` | 致死技能=**恐惧威仪**；承伤构成 恐惧威仪(604k)、幽暗炸弹(263k)、折射(82k) |
| 赵小帅 | `source='毒液爆裂'`（技能当成凶手）、`ability='Shaman'` | 致死技能=**毒液爆裂**；承伤构成 毒液爆裂(470k)、凝结的毒液(255k)、烈性毒液(219k) |

送进模型的句子原来是「赵小帅 死亡，击杀者：毒液爆裂」，现在是「赵小帅 死亡，致死技能：毒液爆裂」。

**修法**：
- `deaths[].killing_ability` = `killingBlow.name`，缺失时退回承伤构成首位
  （实测 5 条死亡里 2 条没有 `killingBlow`）
- `deaths[].damage_breakdown` = 承伤构成 Top 3（新增，用来区分「被一发秒了」和「被多段磨死」）
- **删掉 `source`** —— 不再假装知道凶手
- 前端列名「击杀来源」→「致死技能」

**刻意没做**：从 `deaths[].events` 的最后一条伤害推击杀者。看着可行，但实测有
死者自己（折射）和明显不是致命一击的情况 —— 猜错比留空更糟。

⚠️ `tests/test_aggregation.py` 原来的 fixture 是
`killingBlow: {"name": "Boss", "ability": {"name": "Cleave"}}`，
**这个形状在 36 份真实缓存里出现 0 次**，而断言是 `deaths[0].source == "Boss"` ——
测试把错误语义固化成了规范。已换成真实形状。


### E.4 已修：非玩家混进按人统计的地方（`is_player_entry`）

WCL 榜单条目的 `type`：玩家是职业名（`Shaman`/`Mage`…），非玩家是 `Boss`/`NPC`/`Pet`。
实测 36 份缓存里**两者零重叠**，所以「能解析出职业」就是玩家 —— 这是最干净的判据。

不筛的后果（实测）：**会自我治疗的 BOSS**（妖术领主玛拉卡斯，988 万）以
`type: "Boss"` 挤进治疗榜**第 10/25 位**，还能被勾选生成「玩家报告」，
输出 `spec: "Boss"`、`cast_count: 0` 这种荒谬结果；宠物（`光诞鞭笞者`）出现在
4 份缓存的伤害榜与名册里。

**顺带修掉一个数值失真**：`percent` 的分母是榜单总量，混进 BOSS 的自我治疗会把
所有玩家的占比压低。

改动点：`aggregation._rank_rows`（三个榜单）、`player_compare` 的 `raid_total` 分母
（原先是两个不同的分母，同一个玩家在两处占比不一样）、MCP 的 `list_players`
（实测 25 → 23 人）。

### E.5 已修：另外三条

| 问题 | 症状 | 修法 |
|---|---|---|
| **时间窗在聚合表上静默失效** | 对 `damage-done` 传时间窗返回全部行、`matched_count` 看着权威，调用方当成「那 10 秒的数据」解读 | 返回 `time_filter_ignored: true` + 说明 |
| **分享页把「数据缺失」渲染成「0 死亡」** | 正文在小缓存里、原始数据在 1GB 上限的目录里；文件被清掉后收件人看到「死亡数量 0 · 本场战斗没有死亡记录」 | 接口返回 `data_missing`，前端渲染成「原始数据已不在本地」 |
| **玩家报告没有 stale 概念** | 改了玩家提示词之后旧报告不会被标记（实测磁盘上 12 份全部已过期） | `report_store._signature()` 哈希四份提示词 |

`affixes.json` 也加进了整场复盘的缓存签名 —— 它是大秘境的「攻略库」对等物，
赛季更新后旧报告里的词缀名会过期。

### E.6 剩下的（不紧急）

- **`deaths[].events` 里能推出击杀者**，但实测不可靠（有死者自己的折射、有
  明显不是致命一击的），所以刻意没做
- 名册/榜单之外，`_build_actor_catalog` 仍会把宠物收进目录（`query_data` 的
  玩家解析用得到），只是不再进入按人统计的输出

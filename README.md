# WCL 战斗日志分析网站

输入一条 Warcraft Logs 正式服公开战斗链接，后端通过 WCL V1 tables 接口拉取伤害、治疗、承伤、死亡、Buff/Debuff 和施法数据，再调用 DeepSeek 生成中文深度复盘。

## 技术栈

- 前端：React + Vite
- 后端：FastAPI + HTTPX
- AI：DeepSeek `chat/completions` 接口
- WCL：V1 REST `fights` + 7 个 `tables` 视图（Client Key）

## 当前拉取的 WCL tables

- `damage-done`：伤害排名
- `healing`：治疗排名
- `damage-taken`：承伤排名
- `deaths`：玩家死亡数据
- `buffs`：玩家 Buff 数据
- `debuffs`：玩家 Debuff 数据
- `casts`：玩家施法时间轴

## 目录结构

- `backend/`：FastAPI 服务、WCL 拉取、数据聚合、DeepSeek 调用
- `backend/storage/analysis_cache/`：本地分析报告缓存，按 `report_code + fight_id` 存储
- `backend/storage/wcl_data/`：本地完整 WCL tables 数据，供 DeepSeek Tool Call 按需查询
- `backend/storage/player_reports/`：玩家报告（单人 / 双人对比）
- `backend/scripts/backfill_report_meta.py`：一次性回填脚本，把 Boss 元数据补进旧缓存
- `frontend/`：React 单页应用
- `backend/.env.example`：配置模板

## 启动方式

### 1. 配置并启动后端

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env，至少填写：
#   WCL_V1_API_KEY
#   WCL_V1_CLIENT_NAME
#   DEEPSEEK_API_KEY
uvicorn app.main:app --reload --port 8001
```

后端默认监听 `http://127.0.0.1:8001`，健康检查：

```bash
curl http://127.0.0.1:8001/api/health
```

### 1.1 获取 WCL V1 密钥

- 登录 `https://www.warcraftlogs.com`
- 打开右上角账户菜单中的 `Settings`，或直接访问 `https://www.warcraftlogs.com/accounts/changeuser`
- 页面底部的 `Public key` 就是 `WCL_V1_API_KEY`

`WCL_V1_CLIENT_NAME` 不是 WCL 下发的固定密钥，而是这个应用的标记名称，V1 请求实际只使用 `api_key`；填任意可识别名字即可，例如 `moshou_log`。

### 2. 前端开发模式

新开一个终端：

```bash
cd frontend
npm install
npm run dev
```

开发访问 `http://localhost:5173`。Vite 会把 `/api` 代理到 `http://127.0.0.1:8001`。

首次打开页面时，需要输入公会名登录。公会名来自 `backend/.env` 的 `ALLOWED_GUILD_NAME`，
**不写在代码里**——这个仓库是公开的。

### 3. 生产模式

先构建前端：

```bash
cd frontend
npm install
npm run build
```

再启动后端；后端会自动托管 `frontend/dist`，所以直接访问：

```text
http://localhost:8001
```

修改前端代码后应重新执行 `npm run build`。

## 支持的链接格式

仅支持正式服公开日志，并且必须包含精确的战斗 ID：

```text
https://www.warcraftlogs.com/reports/REPORT_CODE#fight=12&type=damage-done
```

也支持：

```text
https://cn.warcraftlogs.com/reports/REPORT_CODE#fight=12&type=damage-done
```

不支持：

- `classic.warcraftlogs.com`
- `#fight=last`
- 只到 `reports/REPORT_CODE` 而没有 `#fight=<id>` 的链接

## API

### `POST /api/extract`

请求体：

```json
{ "url": "https://www.warcraftlogs.com/reports/CODE#fight=12" }
```

返回中包含 `task_id`、`fight` 和聚合后的 `summary`。

### `POST /api/analysis/{task_id}` + `GET /api/analysis/{task_id}`

整场复盘要跑 DeepSeek 工具循环 1-3 分钟，**不能挂在一个请求上等** —— Cloudflare 的代理读
超时是 100 秒，静默太久直接返回 524（免费/Pro 版不可调）。所以拆成启动 + 轮询：

```text
POST /api/analysis/{task_id}?ignore_cache=false   → 立刻返回，不等 DeepSeek
  {"status": "running"}                        # 已在后台跑，接着轮询
  {"status": "done", "analysis": "…", "cached": true}   # 命中缓存，直接给结果

GET  /api/analysis/{task_id}                      → 查状态
  {"status": "running"}
  {"status": "done", "analysis": "…"}
  {"status": "error", "message": "…"}
```

每个请求都是短请求，任何反向代理都能过；客户端拿到的仍然是**整段正文**，没有流式输出。

同一 `report_code + fight_id` 的分析会优先从本地缓存读取，未命中时才调用 DeepSeek。

> 更早的版本是 SSE（`GET /api/analysis/{task_id}/stream`），靠 15 秒心跳对抗反代超时。
> 心跳能解决问题，但轮询更简单、对任何代理都成立，所以换成了现在这套。
> 玩家报告实测 12 秒，远在 100 秒以内，仍然是单次 `POST` 直接返回，不需要轮询。

DeepSeek 使用 Tool Call 模式：后端先保存完整 WCL 数据，只发送摘要给模型；模型需要更多数据时调用 `query_wcl_data`，由服务器读取本地文件并返回对应片段。

### 报告库与分享

页面左侧是报告库：列出所有已分析过的战斗，展开可以看到该场已经生成过的玩家报告。
玩家报告分两种 —— 单人复盘（选 1 名玩家）和双人对比（选 2 名）。

| 方法 | 路径 | 鉴权 | 用途 |
| --- | --- | --- | --- |
| `GET` | `/api/reports` | 需登录 | 侧边栏报告库（战斗列表 + 每场的玩家报告） |
| `GET` | `/api/reports/{report_code}/{fight_id}` | 公开 | 单场战斗报告 + 玩家名册 |
| `GET` | `/api/reports/{report_code}/{fight_id}/players/{kind}/{slug}` | 公开 | 单份玩家报告，`kind` 为 `single` 或 `comparison` |
| `POST` | `/api/player-reports/analyze` | 需登录 | 生成玩家报告，body `{report_code, fight_id, players}`，一次性返回 `{kind, players, slug, analysis}` |

**读接口是公开的**，这样分享链接对方不用登录就能打开。
不可猜测的部分是 **WCL report code 本身**（WCL 下发的 16 位随机串），和 WCL 自己
unlisted report 的模型一致。副作用是：拿到 report code 的人能看这一场的全部报告。

前端路由用 **hash**（`#/f/{code}/{fight}`、`#/p/{code}/{fight}/{kind}/{slug}`）而不是路径。
原因：后端的 `StaticFiles(html=True)` **不是 SPA fallback**，`/f/CODE/12` 这种路径在生产环境
会直接 404（dev 下能用只是因为 Vite 会兜底，这个坑只有 `npm run build` 之后才暴露）。
fragment 不会发到服务端，整个问题绕开。

玩家报告存在 `backend/storage/player_reports/`，文件名是
`{report_code}__{fight_id}__{kind}__{slug}.json`，`slug` 由玩家名算出，
所以同一个人重新生成是覆盖而不是新增。分析结果**不做 signature 失效**：
分享出去的链接必须一直有效。

## 配置

| 变量 | 说明 |
| --- | --- |
| `WCL_V1_API_KEY` | WCL V1 API Client Key |
| `WCL_V1_CLIENT_NAME` | WCL V1 Client Name，仅记录，不直接参与请求 |
| `DEEPSEEK_API_KEY` | DeepSeek API Key |
| `DEEPSEEK_BASE_URL` | 默认 `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 默认 `deepseek-flash` |
| `MAX_WCL_EVENT_PAGES` | 单条事件流的分页上限，超出会截断并在返回里给出警告 |

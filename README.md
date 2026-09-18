# WCL 战斗日志分析网站

输入一条 Warcraft Logs 正式服公开战斗链接，后端通过 WCL V1 tables 接口拉取伤害、治疗、承伤、死亡、Buff/Debuff 和施法数据，再调用 DeepSeek 流式生成中文深度复盘。

## 技术栈

- 前端：React + Vite
- 后端：FastAPI + HTTPX
- AI：DeepSeek `chat/completions` SSE 流式接口
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

### 2. 前端开发模式

新开一个终端：

```bash
cd frontend
npm install
npm run dev
```

开发访问 `http://localhost:5173`。Vite 会把 `/api` 代理到 `http://127.0.0.1:8001`。

首次打开页面时，需要输入公会名 `圣光的祝福` 登录。

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

### `GET /api/analysis/{task_id}/stream`

返回 `text/event-stream`，事件类型：

- `progress`
- `delta`：DeepSeek 输出片段
- `done`
- `error`

同一 `report_code + fight_id` 的分析会优先从本地缓存读取，未命中时才调用 DeepSeek。

DeepSeek 使用 Tool Call 模式：后端先保存完整 WCL 数据，只发送摘要给模型；模型需要更多数据时调用 `query_wcl_data`，由服务器读取本地文件并返回对应片段。

## 配置

| 变量 | 说明 |
| --- | --- |
| `WCL_V1_API_KEY` | WCL V1 API Client Key |
| `WCL_V1_CLIENT_NAME` | WCL V1 Client Name，仅记录，不直接参与请求 |
| `DEEPSEEK_API_KEY` | DeepSeek API Key |
| `DEEPSEEK_BASE_URL` | 默认 `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 默认 `deepseek-flash` |
| `MAX_DEEPSEEK_EVENT_CHARS` | tables 数据送入 DeepSeek 前的字符上限 |
| `MAX_WCL_EVENT_PAGES` | 保留字段，当前 tables 模式不再使用 |

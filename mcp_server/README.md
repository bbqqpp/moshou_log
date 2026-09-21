# wcl-analyzer MCP server

把本项目的 WCL 取数、查询、深度分析和 DeepSeek 复盘暴露成 MCP 工具，
让 Claude 能在分析过程中主动查数据，而不是只能一次性跑完拿一份报告。

## 工具

| 工具 | 用途 |
| --- | --- |
| `fetch_fight` | 下载（或确认已缓存）一场战斗的全量数据，约 20 秒 |
| `list_players` | 这场有谁、什么专精 |
| `query_data` | 查原始表/事件流，可按玩家和时间窗过滤 |
| `player_rotation` | 某人的施法序列、大招真实使用次数、资源曲线 |
| `compare_players` | 两人深度对比（含空窗交叉判定与公平性分析） |
| `deepseek_review` | 跑完整复盘，返回中文 Markdown（1-3 分钟，消耗 DeepSeek token） |
| `boss_guide` | 这场 BOSS 的本地攻略（阶段/机制/灭团点/检查清单） |

## 安装

```bash
cd mcp_server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 为什么用独立 venv

**不要把它装进 `backend/.venv`。** `mcp` 依赖 `pydantic>=2.12` 和 `starlette>=0.49`，
而后端的 `fastapi 0.115.6` 要求 `starlette<0.42`、`pydantic` 锁在 `2.10.5`。
实测把 mcp 装进后端环境后，FastAPI 会直接起不来：

```
TypeError: Router.__init__() got an unexpected keyword argument
```

所以服务器只 import 后端中**不依赖 FastAPI** 的模块（`app.wcl`、`app.aggregation`、
`app.player_compare`、`app.deep_analysis`、`app.deepseek_agent` 等），
绝不 import `app.main`。

## 配置

在**项目根目录**建一个 `.mcp.json`（这个文件是机器本地配置、不入库，因为里面是绝对路径）：

```json
{
  "mcpServers": {
    "wcl-analyzer": {
      "command": "/home/longmengshen/moshou_log/mcp_server/.venv/bin/python",
      "args": ["/home/longmengshen/moshou_log/mcp_server/server.py"]
    }
  }
}
```

凭证从 `backend/.env` 读取（`WCL_CLIENT_ID` / `WCL_CLIENT_SECRET` / `DEEPSEEK_API_KEY`）。

## 手动验证

```bash
cd /home/longmengshen/moshou_log
mcp_server/.venv/bin/python -c "
import sys; sys.path.insert(0, 'mcp_server')
import server as srv
print(srv.list_players('AbCd1234EfGh5678', 87)[:200])
"
```

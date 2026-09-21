"""WCL V2 (GraphQL) 传输层。

只负责「把 GraphQL 请求发出去、把错误分类」这一层；把 V2 的响应形状翻译回
V1 兼容形状是 `wcl.py` 的事。

## 三个必须显式传的查询参数

V2 的默认值会让数据**静默出错**——没有异常、没有警告，只是内容不对或为空。
实测结论（见 `backend/scripts/verify_v2_parity.py` 与 `docs/wcl-api-roadmap.md` 附录 A）：

- ``translate: false`` —— 默认会翻译成**站点语言**（www 站＝英文），把中文技能名
  和 Boss 名全变成英文。`aggregation.py` 里的机制识别规则按中文技能名硬编码
  且没有 GUID 兜底，一旦变英文，机制检测会全部匹配不到。
- ``useActorIDs: true`` —— 默认返回嵌套的 `source: {id, name}` 对象且**没有
  `sourceID`**，而代码库所有跨表 join 都依赖扁平的整数 actor ID。
- ``useAbilityIDs: false`` —— 默认只给 `abilityGameID`，没有技能名和图标。

`hostilityType` 默认只返回友方，所以每条事件流都要显式指定阵营（见 `wcl.py`）。
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import httpx

from .config import Settings
from .errors import WCLApiError, WCLAuthError
from .wow import save_affix_names

TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"
GRAPHQL_URL = "https://www.warcraftlogs.com/api/v2/client"
#: 国服端点。**词缀的中文名只在这里有** —— `gameData` 在 `reportData` 之外、
#: 不接受 `translate`，本地化完全取决于站点。实测 www 返回 "Fortified"、
#: cn 返回 "强韧"。凭证是同一套（已验证）。
CN_GRAPHQL_URL = "https://cn.warcraftlogs.com/api/v2/client"

_AFFIX_QUERY = "{ gameData { affixes { id name } } }"
#: 词缀一个赛季才变一次，进程内缓存即可
_AFFIX_CACHE: dict[int, str] = {}
_AFFIX_LOCK = threading.Lock()

# 见模块 docstring：这三个不传就会静默出错
PINNED_ARGS = "translate: false"

MAX_RETRIES = 2

#: 部分失败的 GraphQL 错误会挂在这个保留键下，不会与真实字段冲突
PARTIAL_ERRORS_KEY = "__errors"


class TokenCache:
    """client credentials token 的内存缓存。

    **用 `threading.Lock` 而不是 `asyncio.Lock`**：`player_compare.load_fight`
    内部用 `asyncio.run(...)`，每次调用都会新建一个事件循环，模块级的 asyncio
    原语会在第二次调用时抛 "attached to a different loop"。`resolve_fight_reference`
    还是纯同步的，两条路径要共用同一个缓存。
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._transport = transport
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = 0.0

    @property
    def configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def get(self, *, force: bool = False) -> str:
        with self._lock:
            if not force and self._token and time.time() < self._expires_at:
                return self._token

            if not self.configured:
                raise WCLAuthError(
                    "未配置 WCL V2 凭证，请在 backend/.env 里填写 "
                    "WCL_CLIENT_ID 与 WCL_CLIENT_SECRET"
                )

            kwargs: dict[str, Any] = {"timeout": 30}
            if self._transport is not None:
                kwargs["transport"] = self._transport
            try:
                with httpx.Client(**kwargs) as http:
                    response = http.post(
                        TOKEN_URL,
                        data={"grant_type": "client_credentials"},
                        auth=(self._client_id, self._client_secret),
                    )
            except httpx.HTTPError as exc:
                raise WCLApiError(f"换取 WCL access token 失败：{exc}") from exc

            if response.status_code in (400, 401, 403):
                raise WCLAuthError(
                    "WCL 拒绝了这个 client 凭证，请确认 client 是 confidential 类型"
                    "（不是 Public Client），且 WCL_CLIENT_ID / WCL_CLIENT_SECRET 填写正确"
                )
            if response.status_code >= 400:
                raise WCLApiError(
                    f"换取 WCL access token 失败：HTTP {response.status_code}"
                )

            # 形状守卫：token 端点也可能返回 200 + HTML（CDN 拦截页）或
            # 200 + {"error": ...}。JSONDecodeError / KeyError / TypeError 都不是
            # WCLApiError 的子类，会穿透所有调用方的 except 分支变成裸 500。
            try:
                body = response.json()
            except ValueError as exc:
                raise WCLApiError(
                    f"换取 WCL access token 时收到非 JSON 响应：{response.text[:200]}"
                ) from exc

            if not isinstance(body, dict) or not body.get("access_token"):
                raise WCLAuthError(
                    f"WCL 的 token 响应里没有 access_token：{str(body)[:200]}"
                )

            self._token = str(body["access_token"])
            # expires_in 实测是 31104000（360 天）。留 60s 余量。
            try:
                lifetime = float(body.get("expires_in") or 3600)
            except (TypeError, ValueError):
                lifetime = 3600.0
            self._expires_at = time.time() + lifetime - 60
            return self._token

    def invalidate(self) -> None:
        with self._lock:
            self._token = None
            self._expires_at = 0.0


#: 进程级的 token 缓存，按 client 凭证共享。
#:
#: `WCLClient` 是每次 HTTP 请求、每次 `load_fight` 未命中都会新建的，如果缓存挂在
#: 实例上，每次真正下载都要重新 POST 一次 /oauth/token —— 把实测 360 天有效期的
#: token 白白丢掉，skill 脚本里 resolve_fight_reference + load_fight 一次运行就要付两遍。
_SHARED_TOKEN_CACHES: dict[tuple[str, str], TokenCache] = {}
_SHARED_LOCK = threading.Lock()


def _shared_token_cache(
    client_id: str, client_secret: str, transport: httpx.BaseTransport | None
) -> TokenCache:
    # 注入了 transport 的（测试）不共享，否则用例之间会串味
    if transport is not None:
        return TokenCache(client_id, client_secret, transport=transport)
    key = (client_id, client_secret)
    with _SHARED_LOCK:
        cache = _SHARED_TOKEN_CACHES.get(key)
        if cache is None:
            cache = TokenCache(client_id, client_secret)
            _SHARED_TOKEN_CACHES[key] = cache
        return cache


class WCLV2Client:
    """GraphQL 传输层。

    `transport` 用于测试时注入 `httpx.MockTransport`（httpx 已是依赖，不必新增包）。
    """

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport
        # 与同步路径（resolve_fight_reference）共用同一个进程级缓存
        self.tokens = _shared_token_cache(
            settings.wcl_client_id, settings.wcl_client_secret, transport
        )
        self.timeout = settings.request_timeout
        self.max_event_pages = settings.max_wcl_event_pages

    @property
    def configured(self) -> bool:
        return self.tokens.configured

    # ------------------------------------------------------------------ #
    # 请求
    # ------------------------------------------------------------------ #
    async def graphql(
        self,
        client: httpx.AsyncClient,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        label: str = "",
    ) -> dict[str, Any]:
        """发一个 GraphQL 请求，返回 `data`。"""
        for attempt in range(MAX_RETRIES):
            # 放到线程里取：TokenCache.get 在缓存未命中时会做一次阻塞的 POST
            # （timeout 最长 30s），直接调会把整个事件循环卡住——并发请求、
            # 健康检查全都得等它。走线程池就不阻塞 loop。
            token = await asyncio.to_thread(self.tokens.get, force=attempt > 0)
            try:
                response = await client.post(
                    GRAPHQL_URL,
                    headers={"Authorization": f"Bearer {token}"},
                    json={"query": query, "variables": variables or {}},
                )
            except httpx.HTTPError as exc:
                raise WCLApiError(f"访问 WCL 失败：{exc}") from exc

            if response.status_code in (401, 403) and attempt < MAX_RETRIES - 1:
                # token 可能被吊销了，清掉重取一次
                self.tokens.invalidate()
                continue

            return self._parse(response, label)

        raise WCLAuthError("WCL 持续拒绝请求，请检查 V2 client 凭证")  # pragma: no cover

    def graphql_sync(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        label: str = "",
    ) -> dict[str, Any]:
        """同步版本，给 `resolve_fight_reference` 用（skill 脚本同步调用它）。"""
        timeout = httpx.Timeout(self.timeout, connect=15.0)
        with httpx.Client(timeout=timeout, transport=self.transport) as client:
            for attempt in range(MAX_RETRIES):
                token = self.tokens.get(force=attempt > 0)
                try:
                    response = client.post(
                        GRAPHQL_URL,
                        headers={"Authorization": f"Bearer {token}"},
                        json={"query": query, "variables": variables or {}},
                    )
                except httpx.HTTPError as exc:
                    raise WCLApiError(f"访问 WCL 失败：{exc}") from exc

                if response.status_code in (401, 403) and attempt < MAX_RETRIES - 1:
                    self.tokens.invalidate()
                    continue

                return self._parse(response, label)

        raise WCLAuthError("WCL 持续拒绝请求，请检查 V2 client 凭证")  # pragma: no cover

    async def affix_names(self, client: httpx.AsyncClient) -> dict[int, str]:
        """大秘境词缀 id → 中文名。取不到就返回空字典（调用方降级为英文名）。

        走**国服端点**：`gameData` 在 `reportData` 之外、不接受 `translate`，
        中文名只在 cn 站上有（实测 www 是 "Fortified"、cn 是 "强韧"）。
        一个赛季才变一次，进程内缓存。
        """
        with _AFFIX_LOCK:
            if _AFFIX_CACHE:
                return dict(_AFFIX_CACHE)

        token = await asyncio.to_thread(self.tokens.get)
        try:
            response = await client.post(
                CN_GRAPHQL_URL,
                headers={"Authorization": f"Bearer {token}"},
                json={"query": _AFFIX_QUERY},
            )
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return {}

        affixes = ((payload.get("data") or {}).get("gameData") or {}).get("affixes")
        if not isinstance(affixes, list):
            return {}

        mapping = {
            int(item["id"]): str(item["name"])
            for item in affixes
            if isinstance(item, dict) and item.get("id") is not None and item.get("name")
        }
        if mapping:
            with _AFFIX_LOCK:
                _AFFIX_CACHE.update(mapping)
            # 落一份到磁盘：V1 时代的历史缓存里只有词缀 id，靠这份表才能解析出
            # 中文名（聚合层只读文件，不发网络请求）
            save_affix_names(mapping)
        return mapping

    # ------------------------------------------------------------------ #
    # 响应
    # ------------------------------------------------------------------ #
    def _parse(self, response: httpx.Response, label: str) -> dict[str, Any]:
        where = f"（{label}）" if label else ""

        if response.status_code in (401, 403):
            raise WCLAuthError(
                f"WCL 鉴权失败{where}，请检查 backend/.env 中的 "
                "WCL_CLIENT_ID / WCL_CLIENT_SECRET"
            )

        if response.status_code == 429:
            reset = response.headers.get("Retry-After", "?")
            raise WCLApiError(
                f"WCL 配额用尽{where}，重置时间 {reset}。"
                "V2 按点数计费（3600 点/小时），events 分页按页扣点"
            )

        if response.status_code >= 400:
            raise WCLApiError(f"WCL 返回 {response.status_code}{where}：{response.text[:300]}")

        try:
            body = response.json()
        except ValueError as exc:
            raise WCLApiError(f"WCL 返回了非 JSON 响应{where}") from exc

        errors = body.get("errors")
        data = body.get("data")

        # GraphQL 是「HTTP 200 + 部分成功」：errors 可以只影响个别 alias。
        # 只有整个 data 都拿不到时才算彻底失败；部分失败挂到 `__errors` 上，
        # 交给调用方按 alias 归因（见 partial_errors）。
        if errors and not data:
            raise WCLApiError(f"WCL GraphQL 错误{where}：{_format_errors(errors)}")

        if data is None:
            raise WCLApiError(f"WCL 返回了空 data{where}")

        if errors:
            data[PARTIAL_ERRORS_KEY] = errors

        return data


def _format_errors(errors: Any) -> str:
    if not isinstance(errors, list):
        return str(errors)[:300]
    parts = []
    for item in errors:
        if isinstance(item, dict):
            parts.append(str(item.get("message") or item)[:200])
        else:
            parts.append(str(item)[:200])
    return "；".join(parts)[:300]


def partial_errors(data: dict[str, Any]) -> list[Any]:
    """取出「不影响整体请求」的那些 GraphQL 错误，供调用方按 alias 归因。"""
    errors = data.get(PARTIAL_ERRORS_KEY)
    return errors if isinstance(errors, list) else []


def alias_of(error: Any) -> str:
    """从 GraphQL error 的 `path` 里取出出错的 alias。

    path 形如 ``["reportData", "report", "t_deaths"]`` —— alias 是**最后一个**
    元素，不是第一个。取错位置会让错误归因整个失效（每个 alias 都归到
    "reportData" 上，于是又被当成都市失败）。
    """
    if not isinstance(error, dict):
        return ""
    path = error.get("path")
    if isinstance(path, list) and path:
        return str(path[-1])
    return ""


def errors_by_alias(data: dict[str, Any]) -> dict[str, str]:
    """把部分失败按 alias 归因成 `{alias: message}`。"""
    result: dict[str, str] = {}
    for error in partial_errors(data):
        alias = alias_of(error)
        if alias:
            result[alias] = _format_errors([error])
    return result

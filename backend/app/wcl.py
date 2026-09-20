from __future__ import annotations

import asyncio
import re
import urllib.parse
from typing import Any, Mapping

import httpx

from .config import Settings
from .errors import WCLApiError, WCLAuthError, WCLUrlError

ALLOWED_HOSTS = {"www.warcraftlogs.com", "warcraftlogs.com", "cn.warcraftlogs.com"}
ARCHON_HOSTS = {"www.archon.gg", "archon.gg"}
REPORT_PATH_RE = re.compile(r"(?:^|/)reports/([A-Za-z0-9]{8,64})(?:/|$)")
FIGHT_RE = re.compile(r"(?:[#?&])fight=(\d+)")
LAST_FIGHT_RE = re.compile(r"(?:[#?&])fight=last", re.IGNORECASE)
# archon.gg 是 WCL 的第三方前端，战斗 ID 在路径里而不是 fragment：
# /wow/reports/{code}/fights/{id}/raid
ARCHON_FIGHT_PATH_RE = re.compile(r"/reports/([A-Za-z0-9]{8,64})(?:/fights/(\d+))?")

TABLE_VIEWS = (
    "damage-done",
    "healing",
    "damage-taken",
    "deaths",
    "buffs",
    "debuffs",
    "casts",
    "interrupts",
    "dispels",
    "summons",
)

BUFF_DEBUFF_EVENT_FILTER = (
    'type in ("applybuff","removebuff","refreshbuff","applydebuff",'
    '"removedebuff","refreshdebuff","applybuffstack","removebuffstack",'
    '"applydebuffstack","removedebuffstack")'
)
CAST_EVENT_FILTER = 'type in ("cast","begincast")'
DEATH_EVENT_FILTER = 'type in ("damage","heal","absorbed")'
INTERRUPT_EVENT_FILTER = 'type in ("interrupt")'
DISPEL_EVENT_FILTER = 'type in ("dispel")'
RESOURCE_EVENT_FILTER = 'type in ("resourcechange")'
COMBATANT_INFO_EVENT_FILTER = 'type in ("combatantinfo")'
SPAWN_EVENT_FILTER = 'type in ("summon","create")'
EVENT_VIEWS = {
    "buff_debuff_events": BUFF_DEBUFF_EVENT_FILTER,
    "cast_events": CAST_EVENT_FILTER,
    "interrupt_events": INTERRUPT_EVENT_FILTER,
    "dispel_events": DISPEL_EVENT_FILTER,
    "resource_events": RESOURCE_EVENT_FILTER,
    "combatant_info_events": COMBATANT_INFO_EVENT_FILTER,
    "spawn_events": SPAWN_EVENT_FILTER,
}

DEATH_WINDOW_PRE_MS = 15_000
DEATH_WINDOW_POST_MS = 3_000

# A big raid fight needs well over a hundred WCL requests. Firing them strictly
# one at a time took ~3 minutes, which is long enough for a reverse proxy in
# front of us (Cloudflare drops at ~100s) to kill the request. Keep some
# concurrency, but bounded so we don't trip WCL's own rate limiting.
MAX_CONCURRENT_WCL_REQUESTS = 6


def normalize_url(url: str) -> str:
    value = url.strip()
    if not value:
        raise WCLUrlError("WCL 链接不能为空")
    if not re.match(r"^https?://", value, re.IGNORECASE):
        value = f"https://{value}"
    return value


def resolve_fight_reference(url: str, settings: Settings) -> tuple[str, int]:
    """把用户给的链接解析成 `(report_code, fight_id)`。

    与 `parse_wcl_url` 的区别是会**把 `#fight=last` 真的解析出来** —— 那需要一次
    fights 列表的 API 调用（'last' 是 WCL 前端的语法糖，V1 API 不认）。
    """
    report_code, fight_id = parse_wcl_url(url)
    if fight_id is not None:
        return report_code, fight_id

    encoded = urllib.parse.quote(report_code, safe="")
    response = httpx.get(
        f"https://www.warcraftlogs.com/v1/report/fights/{encoded}",
        params={"api_key": settings.wcl_v1_api_key},
        timeout=30,
    )
    if response.status_code != 200:
        raise WCLApiError(f"WCL 返回 {response.status_code}：{response.text[:300]}")

    target = _last_boss_fight(response.json().get("fights") or [])
    if target is None:
        raise WCLApiError(f"{report_code} 里找不到 Boss 战，无法解析 `fight=last`")
    return report_code, int(target["id"])


def _parse_archon_url(parsed: urllib.parse.ParseResult) -> tuple[str, int]:
    """Extract report code and fight id from an archon.gg report URL."""
    path = urllib.parse.unquote(parsed.path or "")
    match = ARCHON_FIGHT_PATH_RE.search(path)
    if not match:
        raise WCLUrlError("无法从 archon.gg 链接中解析 WCL report code")

    report_code, fight_id = match.group(1), match.group(2)
    if not fight_id:
        raise WCLUrlError(
            "archon.gg 链接缺少战斗 ID，请先打开具体战斗再复制链接"
            "（形如 /reports/CODE/fights/12）"
        )

    return report_code, int(fight_id)


def parse_wcl_url(url: str) -> tuple[str, int | None]:
    """Return ``(report_code, fight_id)``.

    ``fight_id`` is ``None`` when the link asks for ``#fight=last``. That is a
    WCL frontend convenience the V1 API does not understand, so the caller
    resolves it to the report's last boss fight once it has the fight list.
    """
    normalized = normalize_url(url)
    parsed = urllib.parse.urlparse(normalized)
    host = (parsed.hostname or "").lower()

    if host in ARCHON_HOSTS:
        return _parse_archon_url(parsed)

    if host not in ALLOWED_HOSTS:
        raise WCLUrlError(f"暂不支持 WCL 域名：{host or '空域名'}")

    path = urllib.parse.unquote(parsed.path or "")
    report_match = REPORT_PATH_RE.search(path)
    if not report_match:
        raise WCLUrlError("无法从链接中解析 WCL report code")

    report_code = report_match.group(1)

    if LAST_FIGHT_RE.search(url):
        return report_code, None

    fight_match = FIGHT_RE.search(url)
    if not fight_match:
        raise WCLUrlError("链接缺少 `#fight=<id>`，请从 WCL 具体战斗页面复制链接")

    return report_code, int(fight_match.group(1))


class WCLClient:
    """Small async client for the legacy WCL V1 REST API."""

    def __init__(self, settings: Settings):
        self.api_key = settings.wcl_v1_api_key
        self.client_name = settings.wcl_v1_client_name
        self.timeout = settings.request_timeout
        self.table_views = TABLE_VIEWS
        self.max_event_pages = settings.max_wcl_event_pages

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        params = {k: v for k, v in params.items() if v is not None}
        try:
            response = await client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise WCLApiError(f"访问 WCL 失败：{exc}") from exc

        if response.status_code in (401, 403):
            raise WCLAuthError("WCL 鉴权失败，请检查 backend/.env 中的 WCL_V1_API_KEY")

        if response.status_code >= 400:
            snippet = response.text[:300]
            raise WCLApiError(f"WCL 返回 {response.status_code}：{snippet}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise WCLApiError("WCL 返回了非 JSON 响应") from exc

        if isinstance(payload, dict) and "error" in payload:
            detail = payload.get("error")
            if isinstance(detail, dict):
                detail = detail.get("message", str(detail))
            raise WCLApiError(f"WCL API 错误：{detail}")

        return payload

    async def extract_fight(
        self,
        report_code: str,
        fight_id: int,
    ) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
        """Return ``(fight, tables, warnings)`` for one WCL fight.

        Every table and event stream is independent, so they are fetched
        concurrently behind a semaphore rather than one after another.
        """
        timeout = httpx.Timeout(self.timeout, connect=15.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            fight = await self._get_fight(client, report_code, fight_id)

            semaphore = asyncio.Semaphore(MAX_CONCURRENT_WCL_REQUESTS)
            warnings: list[str] = []

            table_results = await asyncio.gather(
                *(
                    self._fetch_table(client, semaphore, report_code, fight, view)
                    for view in self.table_views
                )
            )
            event_results = await asyncio.gather(
                *(
                    self._fetch_event_stream(
                        client,
                        semaphore,
                        report_code,
                        fight,
                        event_key,
                        filter_value,
                    )
                    for event_key, filter_value in EVENT_VIEWS.items()
                )
            )

            tables: dict[str, Any] = {}
            for view, payload, warning in table_results:
                tables[view] = payload
                if warning:
                    warnings.append(warning)
            for event_key, payload, warning in event_results:
                tables[event_key] = payload
                if warning:
                    warnings.append(warning)

            await self._extract_death_events(
                client,
                semaphore,
                report_code,
                fight,
                tables,
                warnings,
            )

        return fight, tables, warnings

    async def _fetch_table(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        report_code: str,
        fight: dict[str, Any],
        view: str,
    ) -> tuple[str, dict[str, Any], str]:
        """Fetch one aggregate table, converting failures into a warning."""
        async with semaphore:
            try:
                payload = await self._get_table(client, report_code, fight, view)
            except WCLApiError as exc:
                return (
                    view,
                    {"available": False, "error": str(exc)},
                    f"无法获取 {view} 表：{exc}",
                )
        return view, payload, ""

    async def _fetch_event_stream(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        report_code: str,
        fight: dict[str, Any],
        event_key: str,
        filter_value: str,
    ) -> tuple[str, dict[str, Any], str]:
        """Fetch one filtered event stream, converting failures into a warning."""
        async with semaphore:
            try:
                event_payload, truncated = await self._get_filtered_events(
                    client,
                    report_code,
                    fight,
                    filter_value,
                )
            except WCLApiError as exc:
                return (
                    event_key,
                    {"available": False, "error": str(exc)},
                    f"无法获取 {event_key}：{exc}",
                )

        warning = (
            f"{event_key} 达到分页上限，个别明细可能不完整" if truncated else ""
        )
        return (
            event_key,
            {
                "available": True,
                "events": event_payload.get("events") or [],
                "truncated": truncated,
            },
            warning,
        )

    async def _extract_death_events(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        report_code: str,
        fight: dict[str, Any],
        tables: dict[str, Any],
        warnings: list[str],
    ) -> None:
        deaths = _table_entries(tables.get("deaths"))
        if not deaths:
            tables["death_events"] = {
                "available": True,
                "events": [],
                "deaths": [],
                "truncated": False,
            }
            return

        fight_start = float(fight.get("start_time") or 0)
        fight_end = float(fight.get("end_time") or 0)

        windows: list[tuple[dict[str, Any], float, int, int]] = []
        for death in deaths:
            raw_time = death.get("timestamp")
            if raw_time is None:
                raw_time = death.get("deathTime")
            if raw_time is None:
                continue

            death_time = float(raw_time)
            window_start = int(max(fight_start, death_time - DEATH_WINDOW_PRE_MS))
            window_end = int(min(fight_end, death_time + DEATH_WINDOW_POST_MS))
            if window_end <= window_start:
                continue

            windows.append((death, death_time, window_start, window_end))

        async def fetch_window(
            window: tuple[dict[str, Any], float, int, int],
        ) -> tuple[tuple[dict[str, Any], float, int, int], dict[str, Any] | None, bool]:
            _, _, window_start, window_end = window
            async with semaphore:
                try:
                    payload, truncated = await self._get_filtered_events(
                        client,
                        report_code,
                        fight,
                        DEATH_EVENT_FILTER,
                        start_override=window_start,
                        end_override=window_end,
                    )
                except WCLApiError as exc:
                    warnings.append(f"无法获取死亡窗口事件：{exc}")
                    return window, None, False
            return window, payload, truncated

        results = await asyncio.gather(*(fetch_window(window) for window in windows))

        death_windows: list[dict[str, Any]] = []
        all_events: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        any_truncated = False

        # Deduplicate in the original death order so the output stays stable.
        for window, payload, truncated in results:
            if payload is None:
                continue

            death, death_time, window_start, window_end = window
            actor_id = death.get("id")
            any_truncated = any_truncated or truncated
            window_events: list[dict[str, Any]] = []
            for event in payload.get("events") or []:
                if not isinstance(event, dict):
                    continue
                if actor_id is not None and str(event.get("targetID")) != str(actor_id):
                    continue
                ability = event.get("ability") or {}
                ability_guid = ability.get("guid") if isinstance(ability, dict) else ability
                key = (
                    event.get("timestamp"),
                    event.get("type"),
                    event.get("sourceID"),
                    event.get("targetID"),
                    ability_guid,
                    event.get("amount"),
                    event.get("absorbed"),
                    event.get("hitType"),
                )
                if key in seen:
                    continue
                seen.add(key)
                window_events.append(event)
                all_events.append(event)

            death_windows.append(
                {
                    "player": death.get("name"),
                    "actor_id": actor_id,
                    "death_time": death_time,
                    "window_start": window_start,
                    "window_end": window_end,
                    "event_count": len(window_events),
                }
            )

        tables["death_events"] = {
            "available": True,
            "events": all_events,
            "deaths": death_windows,
            "truncated": any_truncated,
        }
        if any_truncated:
            warnings.append("death_events 达到分页上限，个别死亡窗口明细可能不完整")

    async def _get_fight(
        self,
        client: httpx.AsyncClient,
        report_code: str,
        fight_id: int | None,
    ) -> dict[str, Any]:
        encoded_code = urllib.parse.quote(report_code, safe="")
        url = f"https://www.warcraftlogs.com/v1/report/fights/{encoded_code}"
        payload = await self._get_json(client, url, {"api_key": self.api_key})

        fights = payload.get("fights") or []
        if not isinstance(fights, list):
            raise WCLApiError("WCL fights 响应格式异常")

        if fight_id is None:
            last_boss = _last_boss_fight(fights)
            if last_boss is None:
                raise WCLApiError("该 report 中找不到任何 Boss 战，无法解析 `fight=last`")
            return last_boss

        target = next((f for f in fights if str(f.get("id")) == str(fight_id)), None)
        if target is None:
            available = ", ".join(str(f.get("id")) for f in fights[:10])
            raise WCLApiError(f"该 report 中找不到 fight id={fight_id}，可用战斗：{available or '无'}")

        if not isinstance(target, dict):
            raise WCLApiError("WCL fight 数据格式异常")

        return target

    async def _get_table(
        self,
        client: httpx.AsyncClient,
        report_code: str,
        fight: dict[str, Any],
        view: str,
    ) -> dict[str, Any]:
        start = fight.get("start_time")
        end = fight.get("end_time")

        if start is None or end is None:
            raise WCLApiError("WCL 未返回 fight 的 start_time/end_time")

        encoded_code = urllib.parse.quote(report_code, safe="")
        encoded_view = urllib.parse.quote(view, safe="-")
        url = f"https://www.warcraftlogs.com/v1/report/tables/{encoded_view}/{encoded_code}"

        return await self._get_json(
            client,
            url,
            {
                "api_key": self.api_key,
                "start": start,
                "end": end,
            },
        )

    async def _get_filtered_events(
        self,
        client: httpx.AsyncClient,
        report_code: str,
        fight: dict[str, Any],
        filter_value: str,
        start_override: float | None = None,
        end_override: float | None = None,
    ) -> tuple[dict[str, Any], bool]:
        start = int(start_override) if start_override is not None else fight.get("start_time")
        end = int(end_override) if end_override is not None else fight.get("end_time")

        if start is None or end is None:
            raise WCLApiError("WCL 未返回 fight 的 start_time/end_time")

        encoded_code = urllib.parse.quote(report_code, safe="")
        url = f"https://www.warcraftlogs.com/v1/report/events/{encoded_code}"

        events: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        cursor = start
        page_index = -1

        for page_index in range(self.max_event_pages):
            payload = await self._get_json(
                client,
                url,
                {
                    "api_key": self.api_key,
                    "start": cursor,
                    "end": end,
                    "filter": filter_value,
                },
            )

            page = payload.get("events") or []
            if not isinstance(page, list) or not page:
                break

            for event in page:
                if not isinstance(event, dict):
                    continue
                ability = event.get("ability") or {}
                key = (
                    event.get("id"),
                    event.get("timestamp"),
                    event.get("type"),
                    event.get("sourceID"),
                    event.get("targetID"),
                    ability.get("guid") if isinstance(ability, dict) else ability,
                )
                if key in seen:
                    continue
                seen.add(key)
                events.append(event)

            next_timestamp = payload.get("nextPageTimestamp")
            if next_timestamp is None or next_timestamp <= cursor:
                break
            cursor = next_timestamp

        truncated = len(events) > 0 and page_index >= self.max_event_pages - 1
        return {"events": events}, truncated


def _is_boss_fight(fight: Mapping[str, Any]) -> bool:
    """Whether a V1 API fight entry is a boss encounter rather than a trash pull.

    WCL marks boss fights with ``boss``, but on some reports a fight only
    carries ``originalBoss`` (observed on wipe attempts), and on others a boss
    attempt carries neither. So accept either field; trash pulls carry both
    unset. This is the best signal the V1 API offers.
    """
    return bool(fight.get("boss")) or bool(fight.get("originalBoss"))


def _last_boss_fight(fights: list[Any]) -> dict[str, Any] | None:
    """Resolve ``#fight=last`` to the most recent boss encounter."""
    for fight in reversed(fights):
        if isinstance(fight, dict) and _is_boss_fight(fight):
            return fight
    return None


def _table_entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("entries", "events", "data", "auras", "deaths", "casts", "buffs", "debuffs"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []

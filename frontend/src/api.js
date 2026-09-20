const TOKEN_KEY = 'wcl_analyzer_token'
const GUILD_KEY = 'wcl_analyzer_guild'

export function getAuthToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function getGuildName() {
  return localStorage.getItem(GUILD_KEY) || ''
}

export function saveSession({ token, guildName }) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  if (guildName) localStorage.setItem(GUILD_KEY, guildName)
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(GUILD_KEY)
}

function authHeaders(headers = {}) {
  const token = getAuthToken()
  if (!token) return headers
  return { ...headers, Authorization: `Bearer ${token}` }
}

function requestError(payload, fallback, status) {
  const error = new Error(payload?.detail || fallback)
  error.status = status
  return error
}

async function getJson(url) {
  const response = await fetch(url, { headers: authHeaders() })
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    throw requestError(payload, `请求失败：HTTP ${response.status}`, response.status)
  }
  return payload
}

export async function login(guildName) {
  const response = await fetch('/api/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ guild_name: guildName }),
  })

  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    throw requestError(payload, '登录失败', response.status)
  }

  saveSession({ token: payload.token, guildName: payload.guild_name })
  return payload
}

export async function fetchMe() {
  const response = await fetch('/api/me', { headers: authHeaders() })

  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    throw requestError(payload, `请求失败：HTTP ${response.status}`, response.status)
  }

  return payload
}

export async function extractReport(url) {
  const response = await fetch('/api/extract', {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ url }),
  })

  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    throw requestError(payload, `请求失败：HTTP ${response.status}`, response.status)
  }

  return payload
}

/** 侧边栏报告库（需登录）。 */
export function fetchReports() {
  return getJson('/api/reports')
}

/** 单场战斗报告。公开只读 —— 分享链接靠它免登录打开。 */
export function fetchFightReport(reportCode, fightId) {
  return getJson(
    `/api/reports/${encodeURIComponent(reportCode)}/${encodeURIComponent(fightId)}`,
  )
}

/** 单份玩家报告。公开只读。 */
export function fetchPlayerReport(reportCode, fightId, kind, slug) {
  return getJson(
    `/api/reports/${encodeURIComponent(reportCode)}/${encodeURIComponent(fightId)}` +
      `/players/${encodeURIComponent(kind)}/${encodeURIComponent(slug)}`,
  )
}

/** 启动整场复盘。立即返回状态，不等 DeepSeek 跑完。 */
export async function startAnalysis(taskId, options = {}) {
  const query = new URLSearchParams()
  if (options.ignoreCache) query.set('ignore_cache', 'true')
  const suffix = query.toString() ? `?${query.toString()}` : ''

  const response = await fetch(`/api/analysis/${encodeURIComponent(taskId)}${suffix}`, {
    method: 'POST',
    headers: authHeaders(),
  })

  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    throw requestError(payload, `分析失败：HTTP ${response.status}`, response.status)
  }

  return payload
}

/** 查一次整场复盘的状态。 */
export async function fetchAnalysisState(taskId) {
  const response = await fetch(`/api/analysis/${encodeURIComponent(taskId)}`, {
    headers: authHeaders(),
  })

  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    throw requestError(payload, `查询分析状态失败：HTTP ${response.status}`, response.status)
  }

  return payload
}

const POLL_INTERVAL_MS = 3000
const POLL_TIMEOUT_MS = 20 * 60 * 1000

/** 调用方主动中止轮询（组件卸载）时抛这个，据此静默退出。 */
export class StoppedError extends Error {}

/**
 * 轮询到整场复盘结束。
 *
 * 不用一根长连接等到底，是因为 Cloudflare 的代理读超时是 **100 秒** ——
 * 静默太久直接返回 524（免费/Pro 版不可调）。而整场复盘要跑 DeepSeek 工具循环
 * 1-3 分钟。拆成多次短请求任何反代都能过，客户端拿到的仍然是整段正文。
 *
 * 玩家报告实测 12 秒，远在 100 秒以内，所以那条链路不需要轮询。
 */
export async function waitForAnalysis(taskId, { shouldStop, onTick } = {}) {
  const startedAt = Date.now()
  const deadline = startedAt + POLL_TIMEOUT_MS

  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS))
    if (shouldStop?.()) throw new StoppedError('stopped')

    onTick?.(Math.round((Date.now() - startedAt) / 1000))

    const state = await fetchAnalysisState(taskId)
    if (state.status !== 'running') return state
  }

  throw new Error('分析超时（超过 20 分钟）。稍后可以在左侧报告库里看看结果是否已经出来。')
}

/** 生成玩家报告并等结果。1 个玩家 = 单人复盘，2 个 = 对比。 */
export async function generatePlayerReport(reportCode, fightId, players) {
  const response = await fetch('/api/player-reports/analyze', {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ report_code: reportCode, fight_id: fightId, players }),
  })

  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    throw requestError(payload, `生成失败：HTTP ${response.status}`, response.status)
  }

  return payload
}

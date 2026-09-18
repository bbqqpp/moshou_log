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

export function streamAnalysis(taskId, handlers = {}, options = {}) {
  const controller = new AbortController()
  const decoder = new TextDecoder()
  let buffer = ''

  const emit = (payload) => {
    if (!payload || typeof payload !== 'object') return
    if (payload.type === 'progress') handlers.onProgress?.(payload.message)
    if (payload.type === 'delta') handlers.onDelta?.(payload.content || '')
    if (payload.type === 'done') handlers.onDone?.(payload.message)
    if (payload.type === 'error') handlers.onError?.(payload.message)
  }

  const query = new URLSearchParams()
  if (options.ignoreCache) query.set('ignore_cache', 'true')
  const suffix = query.toString() ? `?${query.toString()}` : ''

  fetch(`/api/analysis/${taskId}/stream${suffix}`, {
    headers: authHeaders({ Accept: 'text/event-stream' }),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        const payload = await response.json().catch(() => null)
        const error = requestError(payload, `分析请求失败：HTTP ${response.status}`, response.status)
        if (response.status === 401) {
          handlers.onUnauthorized?.(error.message)
          return
        }
        throw error
      }
      if (!response.body) throw new Error('浏览器不支持流式响应')

      const reader = response.body.getReader()
      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const blocks = buffer.split('\n\n')
        buffer = blocks.pop() || ''

        for (const block of blocks) {
          for (const line of block.split('\n')) {
            if (!line.startsWith('data:')) continue
            const raw = line.slice(5).trim()
            if (!raw) continue
            try {
              emit(JSON.parse(raw))
            } catch {
              // Ignore malformed SSE lines, keep reading the stream.
            }
          }
        }
      }
    })
    .catch((error) => {
      if (error?.name !== 'AbortError') handlers.onError?.(error.message)
    })

  return () => controller.abort()
}

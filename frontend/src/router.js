import { useEffect, useState } from 'react'

// 用 hash 路由，不用路径路由。
//
// 原因：后端 `main.py` 末尾是 `StaticFiles(directory=dist, html=True)`，而 Starlette 的
// `html=True` **不是 SPA fallback** —— 它只在路径能解析成真实目录时才找 index.html。
// 所以 `/f/ABC/12` 这种路径在生产环境直接 404。dev 下 `:5173` 能用只是因为 Vite 的
// `appType: 'spa'` 会兜底，这个坑只有 `npm run build` 之后才暴露。
// fragment 根本不会发到服务端，整个问题绕开。

const ROUTE_FIGHT = 'fight'
const ROUTE_PLAYER = 'player'

function decode(value) {
  try {
    return decodeURIComponent(value)
  } catch {
    return value
  }
}

function positiveInt(value) {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

export function parseRoute(hash) {
  const segments = String(hash || '')
    .replace(/^#/, '')
    .split('/')
    .filter(Boolean)
    .map(decode)

  if (segments[0] === 'f') {
    const fightId = positiveInt(segments[2])
    if (segments.length >= 3 && fightId !== null) {
      return { name: ROUTE_FIGHT, reportCode: segments[1], fightId }
    }
  }

  if (segments[0] === 'p') {
    const fightId = positiveInt(segments[2])
    if (segments.length >= 5 && fightId !== null) {
      return {
        name: ROUTE_PLAYER,
        reportCode: segments[1],
        fightId,
        kind: segments[3],
        slug: segments[4],
      }
    }
  }

  return { name: 'home' }
}

export function useHashRoute() {
  const [hash, setHash] = useState(() => window.location.hash)

  useEffect(() => {
    const onHashChange = () => setHash(window.location.hash)
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  return parseRoute(hash)
}

export function navigate(path) {
  if (!path || path === '#') {
    if (window.location.hash) window.location.hash = ''
    return
  }
  const next = path.startsWith('#') ? path : `#${path}`
  if (window.location.hash !== next) window.location.hash = next
}

export function fightPath(reportCode, fightId) {
  return `#/f/${encodeURIComponent(reportCode)}/${fightId}`
}

export function playerPath(reportCode, fightId, kind, slug) {
  return (
    `#/p/${encodeURIComponent(reportCode)}/${fightId}` +
    `/${encodeURIComponent(kind)}/${encodeURIComponent(slug)}`
  )
}

/** 当前页面 + 指定路由的完整可分享链接。 */
export function shareUrl(path) {
  // 用 pathname 而不是硬编码 '/'：生产环境前端挂在 /，dev 下是 :5173
  return `${window.location.origin}${window.location.pathname}${path}`
}

export { ROUTE_FIGHT, ROUTE_PLAYER }

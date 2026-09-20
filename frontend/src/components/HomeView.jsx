import { useEffect, useRef, useState } from 'react'

import { StoppedError, extractReport, startAnalysis, waitForAnalysis } from '../api'
import { fightPath, navigate } from '../router'
import FightDashboard from './FightDashboard'
import Markdown from './Markdown'

const WCL_HOSTS = new Set(['www.warcraftlogs.com', 'warcraftlogs.com', 'cn.warcraftlogs.com'])
// archon.gg 是 WCL 的第三方前端，战斗 ID 在路径里：/reports/CODE/fights/12
const ARCHON_HOSTS = new Set(['www.archon.gg', 'archon.gg'])

function validateUrl(url) {
  const value = url.trim()
  if (!value) return '请输入 WCL 战斗链接'

  let normalized = value
  if (!/^https?:\/\//i.test(normalized)) normalized = `https://${normalized}`

  let parsed
  try {
    parsed = new URL(normalized)
  } catch {
    return '链接格式不正确'
  }

  const host = parsed.hostname.toLowerCase()

  if (ARCHON_HOSTS.has(host)) {
    if (!/\/reports\/[A-Za-z0-9]{8,64}\/fights\/\d+/.test(parsed.pathname)) {
      return 'archon.gg 链接缺少战斗 ID，请先打开具体战斗再复制链接'
    }
    return null
  }

  if (!WCL_HOSTS.has(host)) {
    return '仅支持正式服 www.warcraftlogs.com、cn.warcraftlogs.com 或 archon.gg 的公开日志链接'
  }

  if (!/\/reports\/[A-Za-z0-9]{8,64}/.test(parsed.pathname)) {
    return '无法从链接中解析 WCL report code'
  }

  const fragmentAndQuery = `${parsed.hash || ''}${parsed.search || ''}`

  // `fight=last` 由后端解析为该 report 的最后一场 Boss 战
  if (!/(?:[#?&])fight=(\d+|last)(?:&|$)/i.test(fragmentAndQuery)) {
    return '链接缺少 `#fight=<id>`，请从 WCL 具体战斗页面复制链接'
  }

  return null
}

/** 贴链接 → 拉数据 → 流式复盘。这是原有的主页流程，行为保持不变。 */
export default function HomeView({ onUnauthorized, onReportsChanged }) {
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')
  const [phase, setPhase] = useState('idle')
  const [deepseekEnabled, setDeepseekEnabled] = useState(false)
  const [ignoreCache, setIgnoreCache] = useState(false)
  const [data, setData] = useState(null)
  const [analysis, setAnalysis] = useState('')
  const [analysisStatus, setAnalysisStatus] = useState('')
  // 切走时中止轮询。后端任务不受影响，跑完照样写进缓存、出现在侧边栏。
  const stoppedRef = useRef(false)

  useEffect(() => {
    stoppedRef.current = false
    return () => {
      stoppedRef.current = true
    }
  }, [])

  function reset() {
    setData(null)
    setAnalysis('')
    setAnalysisStatus('')
    setPhase('idle')
    setError('')
  }

  async function handleAnalyze(event) {
    event.preventDefault()
    reset()

    const validationError = validateUrl(url)
    if (validationError) {
      setError(validationError)
      return
    }

    setPhase('extracting')
    let extracted
    try {
      extracted = await extractReport(url)
    } catch (extractError) {
      if (extractError.status === 401) {
        onUnauthorized?.(extractError.message)
        return
      }
      setError(extractError.message || '拉取战斗数据失败')
      setPhase('error')
      return
    }

    setData(extracted)

    if (!deepseekEnabled) {
      setPhase('done')
      setAnalysisStatus('本次仅获取 WCL 数据，未调用 DeepSeek 分析')
      return
    }

    // 整场复盘要跑 DeepSeek 工具循环 1-3 分钟。不能挂在一个请求上等（Cloudflare
    // 100 秒静默就 524），所以启动后改成轮询，拿到的仍然是整段正文。
    setPhase('analyzing')
    setAnalysisStatus('已获取 WCL 数据，DeepSeek 正在分析，请稍候（约 1-3 分钟）...')
    try {
      let state = await startAnalysis(extracted.task_id, { ignoreCache })
      if (state.status === 'running') {
        state = await waitForAnalysis(extracted.task_id, {
          shouldStop: () => stoppedRef.current,
          onTick: (seconds) =>
            setAnalysisStatus(`DeepSeek 正在分析，已等待 ${seconds} 秒（约需 1-3 分钟）...`),
        })
      }

      if (state.status === 'error') {
        throw new Error(state.message || '分析失败')
      }

      // 先落到本地状态，万一下面的跳转没生效，用户至少还能看到报告
      setAnalysis(state.analysis || '')
      setAnalysisStatus(state.cached ? '命中本地分析缓存，直接返回结果' : '分析完成')
      setPhase('done')
      // 分析落盘后这一场才会出现在侧边栏里
      onReportsChanged?.()

      // 跳到战斗报告页。玩家勾选、已存玩家报告、分享链接都在那边 ——
      // 分析在这个页面里做完的话，用户本来没有入口去做玩家对比。
      // 此时报告已经写进缓存，FightView 直接读得到。
      navigate(fightPath(extracted.report_code, extracted.fight_id))
    } catch (analysisError) {
      if (analysisError instanceof StoppedError) return
      if (analysisError.status === 401) {
        onUnauthorized?.(analysisError.message)
        return
      }
      setError(analysisError.message || 'DeepSeek 分析失败')
      setPhase('error')
    }
  }

  // 分析期间也禁用表单：整场复盘要跑 1-3 分钟，中途重复提交没有意义
  const busy = phase === 'extracting' || phase === 'analyzing'

  return (
    <>
      <header className="hero">
        <p className="eyebrow">Warcraft Logs Analysis</p>
        <h1>WCL 战斗日志深度复盘</h1>
        <p className="hero-subtitle">粘贴一场正式服公开战斗链接，先看数据，再看 AI 复盘。</p>
      </header>

      <form className="search-form" onSubmit={handleAnalyze}>
        <input
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          placeholder="https://www.warcraftlogs.com/reports/CODE#fight=12&type=damage-done"
          aria-label="WCL 战斗链接"
        />
        <button type="submit" disabled={busy}>
          {phase === 'extracting' ? '获取数据中...' : busy ? 'AI 分析中...' : '确认分析'}
        </button>
      </form>

      <label className="deepseek-toggle">
        <input
          type="checkbox"
          checked={deepseekEnabled}
          onChange={(event) => {
            const enabled = event.target.checked
            setDeepseekEnabled(enabled)
            if (!enabled) setIgnoreCache(false)
          }}
          disabled={busy}
        />
        <span>启用 DeepSeek 分析（会消耗 token）</span>
      </label>

      <label className="deepseek-toggle ignore-cache-toggle">
        <input
          type="checkbox"
          checked={ignoreCache}
          onChange={(event) => setIgnoreCache(event.target.checked)}
          disabled={!deepseekEnabled || busy}
        />
        <span>忽略本地缓存，强制重新分析</span>
      </label>

      {error && <div className="error-banner">{error}</div>}
      {phase === 'extracting' && <p className="status-line">正在从 WCL 拉取战斗数据，请稍候...</p>}

      {data && (
        <FightDashboard fight={data.fight} summary={data.summary} warnings={data.warnings}>
          <section className="analysis-panel">
            <h2>DeepSeek 深度复盘</h2>
            {(phase === 'analyzing' || phase === 'done') && (
              <p className="status-line">{analysisStatus || '等待分析结果...'}</p>
            )}
            {phase === 'analyzing' && !analysis && (
              <p className="status-line">AI 正在生成报告，完成后会一次性显示...</p>
            )}
            {analysis ? (
              <Markdown>{analysis}</Markdown>
            ) : !deepseekEnabled && phase === 'done' ? (
              <p className="muted">本次未选择 DeepSeek 分析，仅展示 WCL 数据。</p>
            ) : (
              phase !== 'error' && <p className="muted">分析报告将在这里显示</p>
            )}
          </section>
        </FightDashboard>
      )}
    </>
  )
}

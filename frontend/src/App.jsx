import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  clearSession,
  extractReport,
  fetchMe,
  getAuthToken,
  getGuildName,
  login,
  streamAnalysis,
} from './api'

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

function formatNumber(value) {
  const number = Number(value || 0)
  if (number >= 100000000) return `${(number / 100000000).toFixed(1)}亿`
  if (number >= 10000) return `${(number / 10000).toFixed(1)}万`
  return number.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

function compactRows(rows, limit = 7) {
  return (rows || []).slice(0, limit)
}

function renderCell(value) {
  if (value === null || value === undefined) return ''
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function formatDuration(fight) {
  if (!fight?.start_time && !fight?.end_time) return '未知'
  const seconds = Math.max(0, ((fight.end_time || 0) - (fight.start_time || 0)) / 1000)
  const minutes = Math.floor(seconds / 60)
  const remaining = Math.round(seconds % 60)
  return `${minutes}分${String(remaining).padStart(2, '0')}秒`
}

function formatElapsedMs(value) {
  const milliseconds = Number(value || 0)
  if (!Number.isFinite(milliseconds)) return '未知'

  const totalSeconds = Math.floor(Math.max(milliseconds, 0) / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = Math.floor(totalSeconds % 60)
  return `${minutes}分${String(seconds).padStart(2, '0')}秒`
}

const CLASS_COLORS = {
  '战士': '#C69B6D',
  '圣骑士': '#F48CBA',
  '猎人': '#AAD372',
  '潜行者': '#FFF468',
  '牧师': '#FFFFFF',
  '死亡骑士': '#C41E3B',
  '萨满祭司': '#0070DE',
  '法师': '#3FC7EB',
  '术士': '#8788EE',
  '武僧': '#00FF98',
  '德鲁伊': '#FF7C0A',
  '恶魔猎手': '#A330C9',
  '唤魔师': '#33937F',
}

function getClassColor(row) {
  return CLASS_COLORS[row?.class] || '#E6E9EF'
}

function StatCard({ label, value }) {
  return (
    <div className="stat-card">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
    </div>
  )
}

function BarTable({ title, rows, rateLabel }) {
  if (!rows?.length) {
    return (
      <section className="data-table bar-chart-panel">
        <h3>{title}</h3>
        <p className="muted">暂无数据</p>
      </section>
    )
  }

  const maxAmount = Math.max(...rows.map((row) => Number(row.amount || 0)), 1)

  return (
    <section className="data-table bar-chart-panel">
      <h3>{title}</h3>
      <div className="bar-list">
        {rows.map((row, index) => (
          <div className="bar-row" key={`${title}-${index}`}>
            <span className="bar-actor">
              <span className="class-swatch" style={{ backgroundColor: getClassColor(row) }} />
              <span className="actor-name" style={{ color: getClassColor(row) }}>{row.actor || 'Unknown'}</span>
            </span>
            <span className="bar-role">{row.role_label || row.class || ''}</span>
            <span className="bar-track">
              <span
                className="bar-fill"
                style={{
                  width: `${Math.max((Number(row.amount || 0) / maxAmount) * 100, 1)}%`,
                  background: getClassColor(row),
                }}
              />
            </span>
            <span className="bar-value">{formatNumber(row.amount)}</span>
            <span className="bar-rate">
              {formatNumber(row.per_second)} {rateLabel}
            </span>
          </div>
        ))}
      </div>
    </section>
  )
}

function Table({ title, rows, columns, empty }) {
  if (!rows?.length) {
    return (
      <section className="data-table">
        <h3>{title}</h3>
        <p className="muted">{empty || '暂无数据'}</p>
      </section>
    )
  }

  return (
    <section className="data-table">
      <h3>{title}</h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column.key}>{column.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${title}-${index}`}>
                {columns.map((column) => (
                  <td key={column.key}>{column.render ? column.render(row) : renderCell(row[column.key])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

const rankingViews = [
  { key: 'damage_done', title: '伤害量', rateLabel: 'DPS' },
  { key: 'healing_done', title: '治疗量', rateLabel: 'HPS' },
  { key: 'damage_taken', title: '承伤量', rateLabel: 'DTPS' },
]

function App() {
  const [authToken, setAuthToken] = useState(() => getAuthToken())
  const [sessionChecked, setSessionChecked] = useState(() => !getAuthToken())
  const [verifiedGuild, setVerifiedGuild] = useState(() => getGuildName())
  const [guildName, setGuildName] = useState('')
  const [loginError, setLoginError] = useState('')
  const [loginLoading, setLoginLoading] = useState(false)
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')
  const [phase, setPhase] = useState('idle')
  const [deepseekEnabled, setDeepseekEnabled] = useState(false)
  const [ignoreCache, setIgnoreCache] = useState(false)
  const [data, setData] = useState(null)
  const [analysis, setAnalysis] = useState('')
  const [analysisStatus, setAnalysisStatus] = useState('')
  const closeStreamRef = useRef(null)

  const stopStream = () => {
    closeStreamRef.current?.()
    closeStreamRef.current = null
  }

  function clearAnalysisState() {
    stopStream()
    setData(null)
    setAnalysis('')
    setAnalysisStatus('')
    setPhase('idle')
    setError('')
  }

  function handleUnauthorized(message) {
    clearSession()
    setAuthToken('')
    setVerifiedGuild('')
    setSessionChecked(true)
    clearAnalysisState()
    setLoginError(message || '登录已失效，请重新输入公会名')
  }

  // Validate a token restored from localStorage before showing the dashboard,
  // so an expired session (e.g. backend restarted) lands on the login page directly.
  useEffect(() => {
    if (sessionChecked) return

    let cancelled = false
    fetchMe()
      .then((payload) => {
        if (cancelled) return
        setVerifiedGuild(payload.guild_name || '')
        setSessionChecked(true)
      })
      .catch((failed) => {
        if (cancelled) return
        if (failed.status === 401) {
          handleUnauthorized(failed.message)
          return
        }
        // 后端不可用等其它错误先放行，交给后续请求去报错
        setSessionChecked(true)
      })

    return () => {
      cancelled = true
    }
  }, [sessionChecked])

  async function handleLogin(event) {
    event.preventDefault()
    setLoginError('')
    setLoginLoading(true)
    try {
      const result = await login(guildName)
      setAuthToken(result.token)
      setVerifiedGuild(result.guild_name || '')
      setGuildName('')
      clearAnalysisState()
    } catch (loginFailed) {
      setLoginError(loginFailed.message || '登录失败')
    } finally {
      setLoginLoading(false)
    }
  }

  function handleLogout() {
    clearSession()
    setAuthToken('')
    setVerifiedGuild('')
    setGuildName('')
    setLoginError('')
    clearAnalysisState()
  }

  async function handleAnalyze(event) {
    event.preventDefault()
    clearAnalysisState()

    const validationError = validateUrl(url)
    if (validationError) {
      setError(validationError)
      return
    }

    setPhase('extracting')
    try {
      const extracted = await extractReport(url)
      setData(extracted)

      if (!deepseekEnabled) {
        setPhase('done')
        setAnalysisStatus('本次仅获取 WCL 数据，未调用 DeepSeek 分析')
        return
      }

      setPhase('streaming')
      setAnalysisStatus('已获取 WCL 数据，正在请求 DeepSeek 分析...')
      closeStreamRef.current = streamAnalysis(
        extracted.task_id,
        {
          onProgress: (message) => setAnalysisStatus(message),
          onDelta: (content) => setAnalysis((current) => current + content),
          onDone: (message) => {
            setAnalysisStatus(message)
            setPhase('done')
          },
          onError: (message) => {
            setError(message)
            setPhase('error')
          },
          onUnauthorized: handleUnauthorized,
        },
        { ignoreCache },
      )
    } catch (extractError) {
      if (extractError.status === 401) {
        handleUnauthorized(extractError.message)
        return
      }
      setError(extractError.message || '分析失败')
      setPhase('error')
    }
  }

  const fight = data?.fight
  const summary = data?.summary

  if (authToken && !sessionChecked) {
    return (
      <div className="page login-page">
        <p className="status-line">正在验证登录状态...</p>
      </div>
    )
  }

  if (!authToken) {
    return (
      <div className="page login-page">
        <header className="hero">
          <p className="eyebrow">Warcraft Logs Analysis</p>
          <h1>WCL 战斗日志深度复盘</h1>
          <p className="hero-subtitle">请先通过公会验证，再开始分析。</p>
        </header>

        <form className="login-panel" onSubmit={handleLogin}>
          <h2>公会验证</h2>
          <input
            value={guildName}
            onChange={(event) => setGuildName(event.target.value)}
            placeholder="请输入公会名"
            aria-label="公会名"
            autoFocus
          />
          <button type="submit" disabled={loginLoading}>
            {loginLoading ? '验证中...' : '登录'}
          </button>
          {loginError && <p className="login-error">{loginError}</p>}
        </form>
      </div>
    )
  }

  return (
    <div className="page">
      <header className="hero">
        <p className="eyebrow">Warcraft Logs Analysis</p>
        <h1>WCL 战斗日志深度复盘</h1>
        <p className="hero-subtitle">粘贴一场正式服公开战斗链接，先看数据，再看 AI 复盘。</p>
        <div className="auth-bar">
          {verifiedGuild && <span>已验证公会：{verifiedGuild}</span>}
          <button type="button" className="ghost-button" onClick={handleLogout}>
            退出登录
          </button>
        </div>
      </header>

      <form className="search-form" onSubmit={handleAnalyze}>
        <input
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          placeholder="https://www.warcraftlogs.com/reports/CODE#fight=12&type=damage-done"
          aria-label="WCL 战斗链接"
        />
        <button type="submit" disabled={phase === 'extracting'}>
          {phase === 'extracting' ? '获取数据中...' : '确认分析'}
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
          disabled={phase === 'extracting'}
        />
        <span>启用 DeepSeek 分析（会消耗 token）</span>
      </label>

      <label className="deepseek-toggle ignore-cache-toggle">
        <input
          type="checkbox"
          checked={ignoreCache}
          onChange={(event) => setIgnoreCache(event.target.checked)}
          disabled={!deepseekEnabled || phase === 'extracting'}
        />
        <span>忽略本地缓存，强制重新分析</span>
      </label>

      {error && <div className="error-banner">{error}</div>}

      {phase === 'extracting' && <p className="status-line">正在从 WCL 拉取战斗数据，请稍候...</p>}

      {data && (
        <div className="dashboard">
          <section className="overview">
            <h2>战斗概览</h2>
            <div className="stat-grid">
              <StatCard label="战斗名称" value={fight?.name || fight?.boss || '未知'} />
              <StatCard label="结果" value={fight?.kill ? '击杀' : '灭团'} />
              <StatCard label="战斗时长" value={formatDuration(fight)} />
              <StatCard label="事件数量" value={summary?.event_count || 0} />
              <StatCard label="死亡数量" value={summary?.death_count || 0} />
            </div>
          </section>

          {data.warnings?.length > 0 && (
            <div className="warning-banner">{data.warnings.join('；')}</div>
          )}

          <div className="rankings-grid">
            {rankingViews.map((view) => {
              return (
                <BarTable
                  key={view.key}
                  title={view.title}
                  rows={summary?.[view.key]}
                  rateLabel={view.rateLabel}
                />
              )
            })}
          </div>

          <Table
            title="死亡记录"
            columns={[
              { key: 'timestamp', label: '战斗时间', render: (row) => formatElapsedMs(row.timestamp) },
              { key: 'player', label: '死亡目标', render: (row) => <span style={{ color: getClassColor(row) }}>{renderCell(row.player)}</span> },
              { key: 'source', label: '击杀来源' },
              { key: 'role_label', label: '职业' },
            ]}
            rows={compactRows(summary?.deaths)}
            empty="本场战斗没有死亡记录"
          />

          <section className="analysis-panel">
            <h2>DeepSeek 深度复盘</h2>
            {(phase === 'streaming' || phase === 'done') && (
              <p className="status-line">{analysisStatus || '等待分析结果...'}</p>
            )}
            {phase === 'streaming' && !analysis && <p className="status-line">AI 正在生成报告...</p>}
            {analysis ? (
              <div className="markdown">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{analysis}</ReactMarkdown>
              </div>
            ) : !deepseekEnabled && phase === 'done' ? (
              <p className="muted">本次未选择 DeepSeek 分析，仅展示 WCL 数据。</p>
            ) : (
              phase !== 'error' && <p className="muted">分析报告将在这里流式显示</p>
            )}
          </section>
        </div>
      )}
    </div>
  )
}

export default App

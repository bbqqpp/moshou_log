import { useEffect, useState } from 'react'

import { fetchPlayerReport } from '../api'
import { fightPath, navigate, playerPath } from '../router'
import Markdown from './Markdown'
import ShareButton from './ShareButton'
import { StatCard, formatDate } from './tables'

/** 单份玩家报告（单人复盘或双人对比）。公开只读。 */
export default function PlayerReportView({ reportCode, fightId, kind, slug, onUnauthorized }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')

    fetchPlayerReport(reportCode, fightId, kind, slug)
      .then((payload) => {
        if (cancelled) return
        setData(payload)
        setLoading(false)
      })
      .catch((failed) => {
        if (cancelled) return
        if (failed.status === 401) {
          onUnauthorized?.(failed.message)
          return
        }
        setError(failed.message || '加载报告失败')
        setLoading(false)
      })

    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reportCode, fightId, kind, slug])

  if (loading) return <p className="status-line">正在加载报告...</p>
  if (error) return <div className="error-banner">{error}</div>
  if (!data) return null

  const players = data.players || []
  const specs = data.specs || []
  const label = data.kind === 'single' ? '单人复盘' : '双人对比'

  return (
    <>
      <header className="hero">
        <p className="eyebrow">{label}</p>
        <h1>{players.join(' vs ')}</h1>
        <p className="hero-subtitle">
          {specs.filter(Boolean).join(' vs ')} · 生成于 {formatDate(data.created_at)}
        </p>
        <div className="auth-bar">
          <button
            type="button"
            className="ghost-button"
            onClick={() => navigate(fightPath(reportCode, fightId))}
          >
            查看整场战斗
          </button>
          <ShareButton path={playerPath(reportCode, fightId, kind, slug)} />
        </div>
      </header>

      <div className="dashboard">
        <section className="overview">
          <h2>报告信息</h2>
          <div className="stat-grid">
            <StatCard label="类型" value={label} />
            <StatCard label="玩家" value={players.join(' / ')} />
            <StatCard label="战斗" value={`${reportCode} 第 ${fightId} 场`} />
            <StatCard label="生成时间" value={formatDate(data.created_at)} />
          </div>
        </section>

        <section className="analysis-panel">
          <h2>AI 分析报告</h2>
          {data.analysis ? (
            <Markdown>{data.analysis}</Markdown>
          ) : (
            <p className="muted">这份报告没有正文</p>
          )}
        </section>
      </div>
    </>
  )
}

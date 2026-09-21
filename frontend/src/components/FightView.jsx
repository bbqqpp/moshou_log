import { useEffect, useState } from 'react'

import { fetchFightReport, generatePlayerReport } from '../api'
import { fightPath, navigate, playerPath } from '../router'
import FightDashboard from './FightDashboard'
import Markdown from './Markdown'
import ShareButton from './ShareButton'
import { ParseBadge, fightOutcome, formatDate, formatDuration } from './tables'

/**
 * 浏览一份已经存在的战斗报告（从侧边栏或分享链接进来）。
 *
 * `readOnly` 为真时不显示玩家勾选 —— 未登录的分享访客不该看到会 401 的按钮。
 */
export default function FightView({
  reportCode,
  fightId,
  readOnly = false,
  onUnauthorized,
  onReportsChanged,
}) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [reloadToken, setReloadToken] = useState(0)
  const [selected, setSelected] = useState([])
  const [generation, setGeneration] = useState({
    phase: 'idle',
    text: '',
    status: '',
    error: '',
  })

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')

    fetchFightReport(reportCode, fightId)
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
  }, [reportCode, fightId, reloadToken])

  // 换一场战斗时清掉上一场的勾选和生成结果
  useEffect(() => {
    setSelected([])
    setGeneration({ phase: 'idle', text: '', status: '', error: '' })
  }, [reportCode, fightId])

  function togglePlayer(name) {
    setSelected((current) => {
      if (current.includes(name)) return current.filter((item) => item !== name)
      if (current.length >= 2) return current
      return [...current, name]
    })
  }

  async function generate() {
    if (!selected.length) return
    setGeneration({ phase: 'running', text: '', status: '正在生成报告，请稍候...', error: '' })

    try {
      const result = await generatePlayerReport(reportCode, fightId, selected)
      setGeneration({
        phase: 'done',
        text: result.analysis || '',
        status: '分析完成',
        error: '',
      })
      // 新报告落盘了，重新拉一次这场的数据让下面「这场的玩家报告」即时更新
      setReloadToken((token) => token + 1)
      onReportsChanged?.()
    } catch (failed) {
      if (failed.status === 401) {
        onUnauthorized?.(failed.message)
        return
      }
      setGeneration({ phase: 'error', text: '', status: '', error: failed.message || '生成失败' })
    }
  }

  if (loading) return <p className="status-line">正在加载报告...</p>
  if (error) return <div className="error-banner">{error}</div>
  if (!data) return null

  const fight = data.fight || {}
  const busy = generation.phase === 'running'
  const savedReports = data.player_reports || []

  return (
    <>
      <header className="hero">
        <p className="eyebrow">已保存的战斗报告</p>
        <h1>{fight.name || reportCode}</h1>
        <p className="hero-subtitle">
          {fightOutcome(fight)} · {formatDuration(fight)} · 生成于{' '}
          {formatDate(data.created_at)}
        </p>
        <div className="auth-bar">
          <ShareButton path={fightPath(reportCode, fightId)} />
        </div>
      </header>

      {data.stale && (
        <div className="warning-banner">
          这份报告是用旧版提示词生成的，内容仍然有效。想按当前提示词重新生成，可以在主页把链接再分析一次。
        </div>
      )}

      <FightDashboard
        fight={fight}
        summary={data.summary}
        dataMissing={data.data_missing}
      >
        {/* 原始数据不在时名册是空的，选择器没有意义 —— 只留正文 */}
        {!readOnly && !data.data_missing && (
          <section className="data-table player-picker">
            <h3>生成玩家报告</h3>
            <p className="muted">选 1 名玩家出单人复盘，选 2 名出对比分析。</p>

            <div className="picker-grid">
              {(data.roster || []).map((player) => {
                const checked = selected.includes(player.player)
                // 已选满 2 人时禁用其余选项，而不是弹错误
                const disabled = !checked && selected.length >= 2
                const classes = ['picker-item']
                if (checked) classes.push('checked')
                if (disabled) classes.push('disabled')

                return (
                  <label key={player.player_id} className={classes.join(' ')}>
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={disabled || busy}
                      onChange={() => togglePlayer(player.player)}
                    />
                    <span className="picker-name">{player.player}</span>
                    <span className="picker-spec">{player.spec}</span>
                    {player.item_level ? (
                      <span className="picker-ilvl">{player.item_level}</span>
                    ) : null}
                    {/* parse 百分位：同装等区间内的水平。灭团场次没有，显示占位 */}
                    <ParseBadge percent={player.parse_percent} />
                  </label>
                )
              })}
            </div>

            <div className="picker-actions">
              <button
                type="button"
                className="primary-button"
                disabled={!selected.length || busy}
                onClick={generate}
              >
                {selected.length === 2
                  ? '对比这两人'
                  : selected.length === 1
                    ? '生成单人报告'
                    : '请先选择玩家'}
              </button>
              {selected.length > 0 && !busy && (
                <button type="button" className="ghost-button" onClick={() => setSelected([])}>
                  清空选择
                </button>
              )}
            </div>

            {generation.error && <div className="error-banner">{generation.error}</div>}
            {busy && <p className="status-line">{generation.status || '正在生成...'}</p>}
            {generation.text && <Markdown>{generation.text}</Markdown>}
          </section>
        )}

        <section className="analysis-panel">
          <h2>DeepSeek 深度复盘</h2>
          {data.analysis ? (
            <Markdown>{data.analysis}</Markdown>
          ) : (
            <p className="muted">这份报告没有正文</p>
          )}
        </section>
      </FightDashboard>

      {savedReports.length > 0 && (
        <section className="data-table saved-reports-panel">
          <h3>这场的玩家报告</h3>
          <ul className="saved-reports">
            {savedReports.map((report) => (
              <li key={`${report.kind}-${report.slug}`}>
                <button
                  type="button"
                  className="ghost-button"
                  onClick={() =>
                    navigate(playerPath(reportCode, fightId, report.kind, report.slug))
                  }
                >
                  {report.kind === 'single' ? '单人' : '对比'} ·{' '}
                  {(report.players || []).join(' vs ')}
                </button>
                <span className="muted">{formatDate(report.created_at)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  )
}

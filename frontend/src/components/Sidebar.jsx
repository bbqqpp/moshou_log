import { useEffect, useState } from 'react'

import { fetchReports } from '../api'
import { fightPath, navigate, playerPath } from '../router'
import { fightOutcome, formatDate, formatDuration } from './tables'

export default function Sidebar({ version, activeKey, isHome, onUnauthorized }) {
  const [reports, setReports] = useState(null)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState(() => new Set())

  useEffect(() => {
    let cancelled = false
    setError('')

    fetchReports()
      .then((payload) => {
        if (!cancelled) setReports(payload.reports || [])
      })
      .catch((failed) => {
        if (cancelled) return
        if (failed.status === 401) {
          onUnauthorized?.(failed.message)
          return
        }
        // 列表拉不到不能让整页白屏，退化成一条错误提示
        setError(failed.message || '加载报告列表失败')
        setReports([])
      })

    return () => {
      cancelled = true
    }
    // onUnauthorized 刻意不进依赖：它是内联箭头函数，每次渲染都是新引用，进来会死循环
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version])

  // 从分享链接或其他入口直接进来时，自动展开当前所在的那一场
  useEffect(() => {
    if (!activeKey) return
    setExpanded((current) => {
      if (current.has(activeKey)) return current
      const next = new Set(current)
      next.add(activeKey)
      return next
    })
  }, [activeKey])

  function toggle(key) {
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-head">
        <h2>报告库</h2>
        {reports && reports.length > 0 && <span className="muted">{reports.length} 场</span>}
      </div>

      {/* 点进任何一份报告后 hash 就变了，这里必须留一个回首页的入口，
          否则用户只能自己去地址栏删掉 #/f/... 才能回到输入链接的界面 */}
      <button
        type="button"
        className={`sidebar-new${isHome ? ' active' : ''}`}
        onClick={() => navigate('#')}
      >
        <span className="sidebar-new-title">＋ 分析新战斗</span>
        <span className="sidebar-new-hint">粘贴 WCL 链接</span>
      </button>

      {reports === null && !error && <p className="muted sidebar-note">加载中...</p>}
      {error && <p className="sidebar-error">{error}</p>}
      {reports?.length === 0 && !error && (
        <p className="muted sidebar-note">还没有分析过的战斗。贴一条 WCL 链接开始。</p>
      )}

      <ul className="report-tree">
        {reports?.map((item) => {
          const key = `${item.report_code}__${item.fight_id}`
          const fight = item.fight || {}
          const isOpen = expanded.has(key)
          const isActive = activeKey === key
          const hasChildren = (item.player_reports || []).length > 0
          const title = fight.name || item.report_code

          return (
            <li key={key} className="report-node">
              <div className={`report-row${isActive ? ' active' : ''}`}>
                <button
                  type="button"
                  className="report-caret"
                  aria-label={isOpen ? '收起玩家报告' : '展开玩家报告'}
                  aria-expanded={isOpen}
                  onClick={() => toggle(key)}
                  disabled={!hasChildren}
                >
                  {hasChildren ? (isOpen ? '▾' : '▸') : '·'}
                </button>
                <button
                  type="button"
                  className="report-main"
                  onClick={() => navigate(fightPath(item.report_code, item.fight_id))}
                >
                  <span className="report-title">{title}</span>
                  <span className="report-meta">
                    {fight.name ? (
                      <span className={fight.kill ? 'tag-kill' : 'tag-wipe'}>
                        {fightOutcome(fight)}
                      </span>
                    ) : (
                      <span className="tag-plain">第 {item.fight_id} 场</span>
                    )}
                    {fight.name && <span>{formatDuration(fight)}</span>}
                    <span>{formatDate(item.created_at)}</span>
                    {item.stale && <span className="tag-stale">旧版</span>}
                  </span>
                </button>
              </div>

              {isOpen && (
                <ul className="report-children">
                  {(item.player_reports || []).map((report) => (
                    <li key={`${report.kind}-${report.slug}`}>
                      <button
                        type="button"
                        className="report-child"
                        onClick={() =>
                          navigate(
                            playerPath(
                              report.report_code || item.report_code,
                              report.fight_id || item.fight_id,
                              report.kind,
                              report.slug,
                            ),
                          )
                        }
                      >
                        <span className="report-child-kind">
                          {report.kind === 'single' ? '单人' : '对比'}
                        </span>
                        <span className="report-child-names">
                          {(report.players || []).join(' vs ')}
                        </span>
                      </button>
                    </li>
                  ))}
                  {!hasChildren && <li className="muted sidebar-note">还没有玩家报告</li>}
                </ul>
              )}
            </li>
          )
        })}
      </ul>
    </aside>
  )
}

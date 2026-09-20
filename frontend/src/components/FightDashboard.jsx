import {
  BarTable,
  StatCard,
  Table,
  compactRows,
  formatDuration,
  formatElapsedMs,
  getClassColor,
  rankingViews,
  renderCell,
} from './tables'

/**
 * 战斗数据面板。实时分析（HomeView）和浏览已存报告（FightView）共用，
 * 两边的数据来源不同但展示必须一致。
 */
export default function FightDashboard({ fight, summary, warnings, children }) {
  return (
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

      {warnings?.length > 0 && <div className="warning-banner">{warnings.join('；')}</div>}

      <div className="rankings-grid">
        {rankingViews.map((view) => (
          <BarTable
            key={view.key}
            title={view.title}
            rows={summary?.[view.key]}
            rateLabel={view.rateLabel}
          />
        ))}
      </div>

      <Table
        title="死亡记录"
        columns={[
          { key: 'timestamp', label: '战斗时间', render: (row) => formatElapsedMs(row.timestamp) },
          {
            key: 'player',
            label: '死亡目标',
            render: (row) => (
              <span style={{ color: getClassColor(row) }}>{renderCell(row.player)}</span>
            ),
          },
          { key: 'source', label: '击杀来源' },
          { key: 'role_label', label: '职业' },
        ]}
        rows={compactRows(summary?.deaths)}
        empty="本场战斗没有死亡记录"
      />

      {children}
    </div>
  )
}

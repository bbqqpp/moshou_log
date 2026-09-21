import {
  BarTable,
  ParseBadge,
  StatCard,
  Table,
  compactRows,
  fightOutcome,
  formatDuration,
  formatElapsedMs,
  getClassColor,
  isMythicPlus,
  rankingViews,
  renderCell,
} from './tables'

/**
 * 战斗数据面板。实时分析（HomeView）和浏览已存报告（FightView）共用，
 * 两边的数据来源不同但展示必须一致。
 */
export default function FightDashboard({ fight, summary, warnings, dataMissing, children }) {
  const mythicPlus = isMythicPlus(fight)
  const keystone = summary?.keystone || {}

  // 逐场数据文件不在时（分享链接常见：正文在小缓存里，原始数据在 1GB 上限的目录里），
  // **不要渲染那些靠 summary 的区块** —— `summary?.death_count || 0` 会把「数据缺失」
  // 显示成「0 死亡 · 本场战斗没有死亡记录」。只保留正文。
  if (dataMissing) {
    return (
      <div className="dashboard">
        <div className="warning-banner">
          这场的**原始战斗数据已不在本地**（只有分析正文被保留），所以看不到伤害/治疗/
          死亡等面板。重新分析一次这场战斗可以恢复。
        </div>
        {children}
      </div>
    )
  }

  return (
    <div className="dashboard">
      <section className="overview">
        <h2>战斗概览</h2>
        <div className="stat-grid">
          <StatCard
            label={mythicPlus ? '副本名称' : '战斗名称'}
            value={fight?.name || fight?.boss || '未知'}
          />
          <StatCard label="结果" value={fightOutcome(fight)} />
          <StatCard label="战斗时长" value={formatDuration(fight)} />
          {mythicPlus ? (
            <>
              <StatCard label="层数" value={keystone.level ? `+${keystone.level}` : '—'} />
              <StatCard
                label="词缀"
                value={(keystone.affixes || []).join(' / ') || '—'}
              />
              <StatCard
                label="小怪进度"
                value={
                  keystone.count_required
                    ? `${Math.round((keystone.count_reached / keystone.count_required) * 100)}%`
                    : '—'
                }
              />
            </>
          ) : null}
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

      {/* 拉怪分段：大秘境的基本结构单位（团本才按阶段组织）。
          `is_boss` 的判据是 encounterID 非 0，不是 kill —— 小怪段的 kill 恒为 false。 */}
      {mythicPlus ? (
        <Table
          title="拉怪分段"
          columns={[
            { key: 'index', label: '段', render: (row) => `第 ${row.index} 段` },
            { key: 'name', label: '目标' },
            {
              key: 'kind',
              label: '类型',
              render: (row) => (row.is_boss ? '首领' : '小怪'),
            },
            { key: 'start_ms', label: '开始', render: (row) => formatElapsedMs(row.start_ms) },
            {
              key: 'duration_ms',
              label: '时长',
              render: (row) => formatElapsedMs(row.duration_ms),
            },
            {
              key: 'deaths',
              label: '死亡',
              render: (row) => (row.deaths || []).join('、') || '—',
            },
          ]}
          rows={summary?.pull_summary}
          empty="这场没有拉怪分段数据"
        />
      ) : null}

      {/* 阶段时间轴：来自 WCL 记录的真实阶段数据，不是攻略里手写的划分。
          同一个 phase_id 会出现多次 —— 转阶段型 BOSS 会反复进出同一阶段。
          大秘境没有相位，不渲染这张表。 */}
      {mythicPlus ? null : (
      <Table
        title="阶段时间轴"
        columns={[
          {
            key: 'name',
            label: '阶段',
            render: (row) => row.name || `第 ${row.phase_id} 阶段`,
          },
          {
            key: 'kind',
            label: '类型',
            render: (row) => (row.is_intermission ? '转阶段' : '常规'),
          },
          { key: 'start_ms', label: '开始', render: (row) => formatElapsedMs(row.start_ms) },
          { key: 'end_ms', label: '结束', render: (row) => formatElapsedMs(row.end_ms) },
          {
            key: 'duration_ms',
            label: '时长',
            render: (row) => formatElapsedMs(row.duration_ms),
          },
        ]}
        rows={summary?.phases}
        empty="该 BOSS 没有阶段数据，或这场战斗太短未记录到阶段切换"
      />
      )}

      {/* parse 百分位衡量的是「相对同专精同装等所有记录的位置」，不是绝对水平。
          灭团场次 WCL 不给排名，所以这里会是空的。
          大秘境整张表不渲染：WCL 对限时通关的场次给所有人返回恒为 100 的数值，
          展示出来只会误导。 */}
      {mythicPlus ? null : (
      <Table
        title="水平定位（同装等区间内的 parse 百分位）"
        columns={[
          {
            key: 'player',
            label: '玩家',
            render: (row) => (
              <span style={{ color: getClassColor(row) }}>{renderCell(row.player)}</span>
            ),
          },
          { key: 'spec', label: '专精', render: (row) => row.spec || row.role || '' },
          {
            key: 'rank_percent',
            label: '百分位',
            render: (row) => <ParseBadge percent={row.rank_percent} />,
          },
          {
            key: 'bracket_ilvl',
            label: '装等区间',
            render: (row) => renderCell(row.bracket_ilvl),
          },
          {
            key: 'total_parses',
            label: '样本量',
            render: (row) => renderCell(row.total_parses),
          },
        ]}
        rows={compactRows(summary?.parse_ranking, 25)}
        empty={summary?.parse_ranking_note || '灭团场次没有 parse 排名，或这场战斗尚未被 WCL 统计'}
      />
      )}

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
          // 是致死**技能**，不是击杀者 —— WCL 的 deaths 表里没有击杀者字段。
          // 这一列原来叫「击杀来源」并显示技能名，读了会以为那是凶手。
          { key: 'killing_ability', label: '致死技能' },
          { key: 'role_label', label: '职业' },
        ]}
        rows={compactRows(summary?.deaths)}
        empty="本场战斗没有死亡记录"
      />

      {children}
    </div>
  )
}

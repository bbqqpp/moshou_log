export function formatNumber(value) {
  const number = Number(value || 0)
  if (number >= 100000000) return `${(number / 100000000).toFixed(1)}亿`
  if (number >= 10000) return `${(number / 10000).toFixed(1)}万`
  return number.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

export function compactRows(rows, limit = 7) {
  return (rows || []).slice(0, limit)
}

export function renderCell(value) {
  if (value === null || value === undefined) return ''
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export function formatDuration(fight) {
  if (!fight?.start_time && !fight?.end_time) return '未知'
  const seconds = Math.max(0, ((fight.end_time || 0) - (fight.start_time || 0)) / 1000)
  const minutes = Math.floor(seconds / 60)
  const remaining = Math.round(seconds % 60)
  return `${minutes}分${String(remaining).padStart(2, '0')}秒`
}

export function formatElapsedMs(value) {
  const milliseconds = Number(value || 0)
  if (!Number.isFinite(milliseconds)) return '未知'

  const totalSeconds = Math.floor(Math.max(milliseconds, 0) / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = Math.floor(totalSeconds % 60)
  return `${minutes}分${String(seconds).padStart(2, '0')}秒`
}

export function formatDate(value) {
  const seconds = Number(value || 0)
  if (!Number.isFinite(seconds) || seconds <= 0) return ''
  const date = new Date(seconds * 1000)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(
    date.getDate(),
  ).padStart(2, '0')}`
}

const CLASS_COLORS = {
  战士: '#C69B6D',
  圣骑士: '#F48CBA',
  猎人: '#AAD372',
  潜行者: '#FFF468',
  牧师: '#FFFFFF',
  死亡骑士: '#C41E3B',
  萨满祭司: '#0070DE',
  法师: '#3FC7EB',
  术士: '#8788EE',
  武僧: '#00FF98',
  德鲁伊: '#FF7C0A',
  恶魔猎手: '#A330C9',
  唤魔师: '#33937F',
}

export function getClassColor(row) {
  return CLASS_COLORS[row?.class] || '#E6E9EF'
}

export function StatCard({ label, value }) {
  return (
    <div className="stat-card">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
    </div>
  )
}

export function BarTable({ title, rows, rateLabel }) {
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
              <span className="actor-name" style={{ color: getClassColor(row) }}>
                {row.actor || 'Unknown'}
              </span>
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

export function Table({ title, rows, columns, empty }) {
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
                  <td key={column.key}>
                    {column.render ? column.render(row) : renderCell(row[column.key])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export const rankingViews = [
  { key: 'damage_done', title: '伤害量', rateLabel: 'DPS' },
  { key: 'healing_done', title: '治疗量', rateLabel: 'HPS' },
  { key: 'damage_taken', title: '承伤量', rateLabel: 'DTPS' },
]

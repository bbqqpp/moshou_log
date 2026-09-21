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

/** 大秘境判定：`keystoneLevel` 只有副本才有（团本是 null）。 */
export function isMythicPlus(fight) {
  return fight?.keystoneLevel !== null && fight?.keystoneLevel !== undefined
}

/**
 * 战斗结果的标签。
 *
 * 大秘境用「限时 / 超时」——`keystoneBonus` 为 0 表示超时（黑掉），
 * 1/2/3 表示 +1/+2/+3。团本才是「击杀 / 灭团」，两者语义不能混。
 */
export function fightOutcome(fight) {
  if (isMythicPlus(fight)) {
    const bonus = fight?.keystoneBonus
    // V1 时代的历史缓存没有 keystoneBonus（那时叫 medal 且语义不同）。
    // **不能把「未知」显示成「超时」** —— 那会把一场限时的副本说成黑掉的。
    if (bonus === null || bonus === undefined) {
      return fight?.kill ? '通关' : '未通关'
    }
    return bonus > 0 ? '限时' : '超时'
  }
  return fight?.kill ? '击杀' : '灭团'
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

// WCL parse 百分位的档位配色，和网站上一致（灰 → 绿 → 蓝 → 紫 → 橙 → 粉）
const PARSE_COLORS = [
  { min: 99, color: '#e268a8' },
  { min: 95, color: '#ff8000' },
  { min: 75, color: '#a335ee' },
  { min: 50, color: '#0070dd' },
  { min: 25, color: '#1eff00' },
]

export function parseColor(percent) {
  const value = Number(percent)
  if (!Number.isFinite(value)) return '#8b93a7'
  const tier = PARSE_COLORS.find((entry) => value >= entry.min)
  return tier ? tier.color : '#9aa0a6'
}

/** parse 百分位徽标。没有数据时显示占位而不是 0 —— 0 和「没有排名」是两回事。 */
export function ParseBadge({ percent, size }) {
  if (percent === null || percent === undefined) {
    return <span className="parse-badge parse-none">—</span>
  }
  const value = Number(percent)
  return (
    <span
      className="parse-badge"
      style={{ color: parseColor(value), fontSize: size }}
      title={`同装等区间内的百分位：${value}`}
    >
      {Math.round(value)}
    </span>
  )
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

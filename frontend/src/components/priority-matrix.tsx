import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { concentration, selectedIssues, type Selection } from '@/lib/decision'
import type { Category, Issue } from '@/lib/domain'

type Props = { issues: Issue[]; categories: Category[]; selection: Selection; onSelect: (selection: Selection) => void }

export function PriorityMatrix({ issues, categories, selection, onSelect }: Props) {
  const labelId = useId()
  const container = useRef<HTMLElement>(null)
  const [width, setWidth] = useState(500)
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(260, Math.round(entry.contentRect.width))))
    if (container.current) observer.observe(container.current)
    return () => observer.disconnect()
  }, [])
  const groups = useMemo(() => {
    const points = new Map<string, { reviews: number; share: number; issues: Issue[] }>()
    for (const issue of issues) {
      const share = concentration(issue, categories)
      const key = `${issue.reviews}:${share}`
      const point = points.get(key)
      if (point) point.issues.push(issue)
      else points.set(key, { reviews: issue.reviews, share, issues: [issue] })
    }
    return [...points.values()]
  }, [issues, categories])
  const selected = new Set(selectedIssues(issues, selection).map(issue => issue.id))
  const maxReviews = Math.max(1, ...issues.map(issue => issue.reviews))
  const right = width - 20
  const x = (reviews: number) => 45 + Math.log1p(reviews) / Math.log1p(maxReviews) * (right - 45)
  const y = (share: number) => 235 - share * 197
  const guideX = x(Math.min(15, maxReviews))
  const showImpactGuide = maxReviews > 15
  const currentIssue = issues.find(issue => selection.kind === 'issue' && issue.id === selection.id)

  return <section ref={container} className="priority-matrix" aria-labelledby={labelId}>
    <header className="matrix-heading"><div><h2 id={labelId}>问题规模与类别内集中度</h2></div><span>{issues.length} 个问题</span></header>
    {issues.length > 0 ? <>
      <svg viewBox={`0 0 ${width} 286`} role="group" aria-label="问题规模与类别内集中度：横轴为关联评论数，纵轴为类别内反馈占比。点击圆点选择问题，同坐标圆点显示问题数量并可循环选择。">
        {showImpactGuide && <rect className="matrix-zone" x={guideX} y="38" width={right - guideX} height={y(.5) - 38} rx="4" aria-hidden="true"/>}
        <g className="matrix-axis" aria-hidden="true">
          <text x="45" y="16">类别内反馈占比</text>
          <path d={`M45 38V235H${right}`} fill="none" stroke="currentColor" />
          {[0, 0.5, 1].map(value => <text key={value} x="39" y={y(value) + 4} textAnchor="end">{value * 100}%</text>)}
          <text x="45" y="255" textAnchor="middle">0</text>
          {showImpactGuide && <text x={guideX} y="255" textAnchor="middle">15</text>}
          <text x={right} y="255" textAnchor="end">{maxReviews}</text>
          <text x={right} y="279" textAnchor="end">关联评论数（对数刻度）→</text>
        </g>
        <g className="matrix-guide" aria-hidden="true" fill="none" stroke="currentColor" strokeDasharray="3 5">
          <path d={`M45 ${y(0.5)}H${right}`} />
          {showImpactGuide && <path d={`M${guideX} 38V235`} />}
        </g>
        <g className="matrix-quadrant" aria-hidden="true">
          <text x="56" y="68">低频集中</text>
          <text x="56" y="212">低频分散</text>
          {showImpactGuide && <><text x={right - 12} y="68" textAnchor="end">高频集中</text><text x={right - 9} y="212" textAnchor="end">高频分散</text></>}
        </g>
        {[...groups].sort((a, b) => Number(a.issues.some(issue => selected.has(issue.id))) - Number(b.issues.some(issue => selected.has(issue.id)))).map(point => {
          const active = point.issues.some(issue => selected.has(issue.id))
          const multiple = point.issues.length > 1
          const label = `${point.reviews} 条评论，类别内反馈占比 ${(point.share * 100).toFixed(1)}%，${multiple ? `${point.issues.length} 个问题，点击依次选择` : point.issues[0].title}`
          const choose = () => {
            const index = point.issues.findIndex(issue => selection.kind === 'issue' && selection.id === issue.id)
            onSelect({ kind: 'issue', id: point.issues[(index + 1) % point.issues.length].id })
          }
          return <g key={`${point.reviews}:${point.share}`} className={`matrix-point${active ? ' selected' : ''}`} transform={`translate(${x(point.reviews)}, ${y(point.share)})`} role="button" tabIndex={0} aria-label={label} aria-pressed={active} onClick={choose} onKeyDown={event => {
            if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); choose() }
          }}>
            <title>{label + (multiple ? `：${point.issues.map(issue => issue.title).join('；')}` : '')}</title>
            <circle className="matrix-point-hit" r="12" fill="transparent" />
            <circle className="matrix-point-dot" r={multiple ? 9 : 4.5} />
            {multiple && <text className="matrix-point-count" textAnchor="middle" dominantBaseline="central" pointerEvents="none">{point.issues.length}</text>}
          </g>
        })}
      </svg>
      <label className="matrix-picker"><span>{currentIssue ? '当前问题' : '定位问题'}</span><select aria-label="选择矩阵中的问题" value={currentIssue?.id || ''} onChange={event => onSelect({ kind: 'issue', id: event.target.value })}>
        <option value="" disabled>选择问题</option>
        {groups.map(point => <optgroup key={`${point.reviews}:${point.share}`} label={`${point.reviews} 条评论 · 类别内 ${(point.share * 100).toFixed(1)}%${point.issues.length > 1 ? ` · ${point.issues.length} 个同坐标问题` : ''}`}>
          {point.issues.map(issue => <option key={issue.id} value={issue.id}>{issue.title} · {issue.category_label}</option>)}
        </optgroup>)}
      </select></label>
    </> : <p className="matrix-note">暂无可比较的问题</p>}
  </section>
}

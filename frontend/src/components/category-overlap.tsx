import { useMemo, useState } from 'react'
import { ArrowUpRight } from 'lucide-react'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { EvidenceCard } from '@/components/product-ui'
import { useResource } from '@/lib/api'
import { categoryIntersections, type CategoryIntersection } from '@/lib/decision'
import type { Category, Issue } from '@/lib/domain'

function ReviewEvidence({ issue, reviewId }: { issue: Issue; reviewId: string }) {
  const result = useResource<Issue>(`/api/chatbi/issues/${encodeURIComponent(issue.id)}`)
  const evidence = result.data?.evidence_items.filter(item => item.review_id === reviewId)
  return <section className="overlap-issue-evidence"><h4>{issue.title}</h4>
    {result.loading ? <p role="status">正在加载评论…</p> : result.error ? <p role="alert">{result.error} <button className="text-action" onClick={result.reload}>重试</button></p>
      : evidence?.length ? evidence.map((item, index) => <EvidenceCard key={`${item.observation_id || reviewId}-${index}`} item={item} index={index}/>) : <p>暂无相关原话</p>}
  </section>
}

export function SharedReviews({ categories, reviewIds, issues }: { categories: Category[]; reviewIds: string[]; issues: Issue[] }) {
  const [chosen, setChosen] = useState(reviewIds[0])
  const reviewId = reviewIds.includes(chosen) ? chosen : reviewIds[0]
  if (!reviewId) return <p>暂无关联评论</p>
  return <>
    <label className="overlap-review-picker">选择评论<select value={reviewId} onChange={event => setChosen(event.target.value)}>{reviewIds.map((id, index) => <option key={id} value={id}>{index + 1} / {reviewIds.length} · 评论 #{id}</option>)}</select></label>
    <div className="overlap-evidence-columns">{categories.map(category => <section key={category.key} data-category={category.key}><h3>{category.label}</h3>{issues.filter(issue => issue.category === category.key && issue.review_ids.includes(reviewId)).map(issue => <ReviewEvidence key={issue.id} issue={issue} reviewId={reviewId}/>)}</section>)}</div>
  </>
}

export function CategoryOverlap({ issues, categories }: { issues: Issue[]; categories: Category[] }) {
  const groups = useMemo(() => categoryIntersections(issues, categories), [issues, categories])
  const [includeSingle, setIncludeSingle] = useState(false)
  const [chosen, setChosen] = useState('')
  const [open, setOpen] = useState(false)
  const visible = groups.filter(group => includeSingle || group.categories.length > 1)
  const active = visible.find(group => group.key === chosen) || visible[0]
  const counts = categories.map(category => groups.reduce((sum, group) => sum + (group.categories.some(item => item.key === category.key) ? group.reviewIds.length : 0), 0))
  const total = visible.reduce((sum, group) => sum + group.reviewIds.length, 0)
  const maxCount = Math.max(1, ...visible.map(group => group.reviewIds.length))
  const rowHeight = 22, barHeight = 100, chartHeight = barHeight + categories.length * rowHeight
  const label = (group: CategoryIntersection) => group.categories.map(category => category.label).join(' + ')
  // UpSet's matrix + bars encode exact combinations; no pairwise expansion or empty power set.
  return <section className="category-overlap" aria-label="同一评论的类别交叉">
    <div className="upset-summary"><p><strong>{total}</strong> 条评论 · {visible.length} 种{includeSingle ? '' : '跨类'}组合</p><label><input type="checkbox" checked={includeSingle} onChange={event => setIncludeSingle(event.target.checked)}/>包含单一类别</label></div>
    <div className="upset-chart">
      <div className="upset-labels"><div className="upset-axis-label" style={{ height: barHeight }}>组合评论数 ↑<span>类别 / 涉及评论数</span></div>{categories.map((category, index) => <div className={`upset-category ${active?.categories.some(item => item.key === category.key) ? 'active' : ''}`} key={category.key} data-category={category.key} data-count={counts[index]} style={{ height: rowHeight }}><span>{category.label}</span><b>{counts[index]}</b></div>)}</div>
      <div className="upset-scroll" role="region" aria-label="全部类别组合，横向滚动查看" tabIndex={0}>
        <div className="upset-columns">{visible.map(group => {
          const members = categories.flatMap((category, index) => group.categories.some(item => item.key === category.key) ? [index] : [])
          const height = group.reviewIds.length / maxCount * 68
          return <button key={group.key} className={`upset-column ${active?.key === group.key ? 'active' : ''}`} data-key={group.key} data-count={group.reviewIds.length} data-degree={group.categories.length}
            aria-label={`仅涉及${label(group)}，${group.reviewIds.length} 条评论，查看证据`} title={`仅涉及${label(group)} · ${group.reviewIds.length} 条评论`} onPointerEnter={() => setChosen(group.key)} onFocus={() => setChosen(group.key)} onClick={() => { setChosen(group.key); setOpen(true) }}>
            <svg width="32" height={chartHeight} viewBox={`0 0 32 ${chartHeight}`} aria-hidden="true">
              <text className="upset-count" x="16" y={barHeight - 18 - height} textAnchor="middle">{group.reviewIds.length}</text>
              <rect className="upset-bar" x="7" y={barHeight - 12 - height} width="18" height={height}/>
              {categories.map((category, index) => <rect key={category.key} className="upset-row" x="0" y={barHeight + index * rowHeight} width="32" height={rowHeight} opacity={index % 2 ? 0 : 1}/>)}
              <line className="upset-connector" x1="16" x2="16" y1={barHeight + (members[0] + .5) * rowHeight} y2={barHeight + (members[members.length - 1] + .5) * rowHeight}/>
              {categories.map((category, index) => <circle key={category.key} className={`upset-dot ${members.includes(index) ? 'active' : ''}`} cx="16" cy={barHeight + (index + .5) * rowHeight} r="3.5"/>)}
            </svg>
          </button>
        })}</div>
        {!visible.length && <p className="upset-empty">{groups.length ? '暂无跨类别评论；可选择“包含单一类别”查看全部。' : '暂无已归类评论'}</p>}
      </div>
    </div>
    <div className="overlap-footer"><span>{active ? `仅涉及 ${label(active)} · ${active.reviewIds.length} 条` : '暂无组合'}</span><button className="text-action" disabled={!active} onClick={() => setOpen(true)}>查看评论<ArrowUpRight/></button></div>
    <Dialog open={open} onOpenChange={setOpen}><DialogContent className="issue-dialog overlap-dialog"><DialogHeader><DialogTitle>组合评论</DialogTitle><DialogDescription>{active && `仅涉及 ${label(active)} · ${active.reviewIds.length} 条评论`}</DialogDescription></DialogHeader>{open && active && <SharedReviews key={active.key} categories={active.categories} reviewIds={active.reviewIds} issues={issues}/>}</DialogContent></Dialog>
  </section>
}

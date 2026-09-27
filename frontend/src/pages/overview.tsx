import { useRef, useState } from 'react'
import { ArrowUpRight, CalendarDays, ChevronDown } from 'lucide-react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { CategoryOverlap } from '@/components/category-overlap'
import { CategoryIssueBubbles } from '@/components/issue-bubble-map'
import { PriorityMatrix } from '@/components/priority-matrix'
import { Empty, ErrorState, Loading } from '@/components/product-ui'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { forGame, useResource } from '@/lib/api'
import type { Selection } from '@/lib/decision'
import type { Overview } from '@/lib/domain'
import './overview.css'

type Props = { game: string; openIssue: (id: string) => void }
const countText = (value: number) => value.toLocaleString('zh-CN')
const shareText = (value: number | null) => value === null ? '—' : `${(value * 100).toFixed(1)}%`

function GlobalWorkspace({ data, openIssue }: Props & { data: Overview }) {
  const { scope, recommendation, categories, issues, timeline } = data.global
  const [categoryKey, setCategoryKey] = useState<string>()
  const [categoryOpen, setCategoryOpen] = useState(false)
  const categoryTitle = useRef<HTMLHeadingElement>(null)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [matrixSelection, setMatrixSelection] = useState<Selection>({ kind: 'category', id: '' })
  const [period, setPeriod] = useState<'years' | 'months'>('years')
  const category = categories.find(item => item.key === categoryKey)
  const members = issues.filter(issue => issue.category === categoryKey)
  const selectedIssue = issues.find(issue => matrixSelection.kind === 'issue' && issue.id === matrixSelection.id)
  const reviewLimit = Math.max(1, ...categories.map(item => item.reviews))
  const issueLimit = Math.max(1, ...categories.map(item => item.issues))
  const recommendationParts = [
    { key: 'recommended', label: '推荐', count: recommendation.recommended },
    { key: 'not-recommended', label: '不推荐', count: recommendation.not_recommended },
    { key: 'unknown', label: '未知', count: recommendation.unknown },
  ]
  const coverageLabel = scope.coverage === 'exhausted' ? '本次采集已完成' : scope.coverage === 'bounded_recent_sample' ? '限定范围样本' : '采集范围未知'
  const chooseCategory = (key: string) => {
    setCategoryKey(key)
    setCategoryOpen(true)
  }

  return <div className="decision-workspace neutral-overview" data-rule-version={data.global.rule_version}>
    <header className="overview-heading">
      <div><div className="overview-eyebrow">{data.game.name.toLocaleUpperCase()} / STEAM</div><h1>玩家反馈总览</h1></div>
      <div className="overview-snapshot"><span><strong data-metric="collected" data-count={scope.collected}>{countText(scope.collected)}</strong> 条评论</span><span><CalendarDays aria-hidden="true"/>{scope.observed_at ? <>采集于 <time dateTime={scope.observed_at}>{new Date(scope.observed_at).toLocaleDateString('zh-CN', { timeZone: 'UTC' })}</time></> : '采集时间未知'}</span></div>
    </header>

    <div className="overview-charts">
      <section className="overview-panel sentiment-panel" aria-labelledby="recommendation-title">
        <header className="overview-chart-heading"><div><span className="chart-index">01</span><h2 id="recommendation-title">整体推荐情况</h2></div><span>Steam 推荐标记</span></header>
        <div className="recommendation-readout">{recommendationParts.map(part => <div key={part.key} className={part.key}><span><i/>{part.label}</span><strong>{shareText(scope.collected ? part.count / scope.collected : null)}</strong><small>{countText(part.count)} 条评论</small></div>)}</div>
        <div className="neutral-recommendation-bar" role="img" aria-label={scope.collected ? recommendationParts.map(part => `${part.label} ${part.count} 条，占全部采集评论${shareText(part.count / scope.collected)}`).join('；') : '暂无评论，无法计算推荐分布'}>{scope.collected > 0 && recommendationParts.map(part => <span key={part.key} className={part.key} data-recommendation={part.key} data-count={part.count} style={{ width: `${part.count / scope.collected * 100}%` }}/>)}</div>
      </section>

      <section className="overview-panel categories-panel" aria-labelledby="category-title">
        <header className="overview-chart-heading"><div><span className="chart-index">02</span><h2 id="category-title">反馈类别分布</h2></div><span>全部 {categories.length} 类</span></header>

        <div className="neutral-category-legend" aria-label="图例"><span><i className="category-review-key" aria-hidden="true"/>评论数</span><span><i className="category-issue-key" aria-hidden="true"/>问题数</span></div>
        <div className="neutral-category-head"><span>类别</span><span>评论</span><span>问题</span><span>占比</span></div>
        <div className="neutral-category-bars">{categories.map(item => <button key={item.key} className="neutral-category-row" data-category={item.key} data-count={item.reviews} data-issues={item.issues} data-share={item.share ?? ''} aria-label={`${item.label}，${item.reviews} 条评论，${item.issues} 个问题，占已分析评论${shareText(item.share)}，查看问题`} aria-haspopup="dialog" onClick={() => chooseCategory(item.key)}>
          <span className="category-name">{item.label}</span><span className="neutral-category-track" aria-hidden="true"><span className="category-review-mark" style={{ width: `${item.reviews / reviewLimit * 100}%` }}/><span className="category-issue-mark" data-empty={!item.issues} style={{ width: `${item.issues / issueLimit * 100}%` }}/></span><strong>{countText(item.reviews)}</strong><span className="category-issue-count">{item.issues}</span><span className="category-share">{shareText(item.share)}</span>
        </button>)}</div>
        <div className="neutral-category-axis" aria-hidden="true"><span>0</span><span>50%</span><span>100%</span></div>
      </section>

      <section className="overview-panel timeline-panel" aria-labelledby="timeline-title">
        <header className="overview-chart-heading"><div><span className="chart-index">03</span><h2 id="timeline-title">评论发表时间</h2></div><div className="timeline-switch" aria-label="时间粒度">{(['years', 'months'] as const).map(value => <button key={value} aria-pressed={period === value} onClick={() => setPeriod(value)}>{value === 'years' ? '按年' : '按月'}</button>)}</div></header>
        <p className="chart-description">{timeline?.start && timeline.end ? `${timeline.start} — ${timeline.end}` : '暂无有效发表日期'} <span>UTC</span></p>
        <div className="overview-time-chart" data-period={period}>
          {timeline?.dated ? <ResponsiveContainer width="100%" height="100%" minWidth={0}><BarChart data={timeline[period]} margin={{ top: 8, right: 5, bottom: 0, left: -23 }} accessibilityLayer>
            <CartesianGrid vertical={false} stroke="#314556" strokeDasharray="3 4"/>
            <XAxis dataKey="period" tick={{ fill: '#9eb4c5', fontSize: 10 }} tickLine={false} axisLine={false} minTickGap={24}/>
            <YAxis allowDecimals={false} tick={{ fill: '#9eb4c5', fontSize: 10 }} tickLine={false} axisLine={false} width={52}/>
            <Tooltip cursor={{ fill: '#b7d8ed0d' }} contentStyle={{ background: '#142634', border: '1px solid #4a6275', borderRadius: 6, color: '#e5eef4', fontSize: 12 }} labelStyle={{ color: '#c2d5e1' }} itemStyle={{ color: '#c2d5e1' }} formatter={value => [`${value} 条`, '评论数']}/>
            <Bar dataKey="reviews" name="评论数" fill="#7ca5c0" radius={[2, 2, 0, 0]} maxBarSize={28} isAnimationActive={false}/>
          </BarChart></ResponsiveContainer> : <div className="overview-chart-empty">暂无可绘制的发表时间</div>}
        </div>
      </section>
    </div>

    <footer className="overview-footer">
      <details className="overview-data-notes"><summary>数据范围<ChevronDown/></summary><div><p>{coverageLabel}</p><p>已分析 {countText(scope.analyzed)} 条；含具体反馈 {countText(scope.with_feedback)} 条；未提取具体反馈 {countText(scope.without_feedback)} 条。</p><p>待分析 {countText(scope.pending)} 条；分析失败 {countText(scope.failed)} 条；已排除 {countText(scope.excluded)} 条。</p></div></details>
      <div className="overview-footer-links"><button className="text-action" onClick={() => setAdvancedOpen(true)}>深入分析<ArrowUpRight/></button></div>
    </footer>
    {Boolean(scope.pending || scope.failed || scope.excluded) && <p className="overview-incomplete" role="status">待分析 {scope.pending} 条，失败 {scope.failed} 条，排除 {scope.excluded} 条。</p>}

    <Dialog open={categoryOpen} onOpenChange={setCategoryOpen}><DialogContent className="overview-detail-dialog category-detail-dialog"
      onOpenAutoFocus={event => {
        event.preventDefault()
        categoryTitle.current?.focus()
      }}><DialogHeader><span className="category-detail-eyebrow">反馈洞察 <span>/</span> {data.game.name}</span><div className="category-detail-heading"><DialogTitle ref={categoryTitle} tabIndex={-1}>{category?.label}</DialogTitle><DialogDescription><span><strong>{category?.reviews ?? 0}</strong> 条去重评论</span><span><strong>{members.length}</strong> 个问题</span></DialogDescription></div></DialogHeader>
      {members.length ? <CategoryIssueBubbles issues={members} onSelect={openIssue}/> : <Empty>该类别暂无已提取的具体问题</Empty>}
    </DialogContent></Dialog>

    <Dialog open={advancedOpen} onOpenChange={setAdvancedOpen}><DialogContent className="overview-detail-dialog overview-analysis-dialog" aria-describedby={undefined}><DialogHeader><DialogTitle>深入分析</DialogTitle></DialogHeader>
      <section><h3>类别组合</h3><CategoryOverlap issues={issues} categories={categories}/></section>
      <section><h3>问题规模与类别内集中度</h3><PriorityMatrix issues={issues} categories={categories} selection={matrixSelection} onSelect={setMatrixSelection}/>{selectedIssue && <button className="text-action" onClick={() => openIssue(selectedIssue.id)}>查看问题：{selectedIssue.title}<ArrowUpRight/></button>}</section>
    </DialogContent></Dialog>
  </div>
}

export function OverviewPage(props: Props) {
  const overview = useResource<Overview>(forGame('/api/chatbi/overview', props.game))
  if (overview.loading) return <Loading/>
  if (overview.error || !overview.data?.global) return <ErrorState message={overview.error || '反馈总览加载失败'} retry={overview.reload}/>
  return <GlobalWorkspace key={`${overview.data.created_at}:${overview.data.global.rule_version}`} {...props} data={overview.data}/>
}

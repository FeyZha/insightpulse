import { useState } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Empty, ErrorState, Loading, PageHeader, TranslatedQuote } from '@/components/product-ui'
import { forGame, useResource } from '@/lib/api'
import { ObservationText } from '@/components/tracking-blocks'
import type { ComparisonScope, TrackingEvidence, TrackingMonitoring } from '@/lib/tracking'
import './tracking.css'

const date = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
const shortDate = (value: number) => new Date(value).toLocaleDateString('zh-CN', { timeZone: 'Asia/Shanghai' })
const percentage = (value: number) => `${value.toFixed(2)}%`
const difference = (value: number) => value !== 0 && Math.abs(value) < .001 ? `${value > 0 ? '+' : '−'}<0.001` : `${value > 0 ? '+' : ''}${value.toFixed(3)}`
const params = (scope: ComparisonScope) => new URLSearchParams(scope).toString()

export function EvidenceSide({ game, scope, issueId, snapshotId, side }: { game: string; scope?: ComparisonScope; snapshotId?: string; issueId: string; side: 'before' | 'after' }) {
  const [page, setPage] = useState(0)
  const query = new URLSearchParams({ issue_id: issueId, side, page_offset: String(page * 5), limit: '5' })
  const path = snapshotId ? `/api/tracking/monitoring/evidence?snapshot_id=${encodeURIComponent(snapshotId)}` : `/api/tracking/evidence?${params(scope!)}`
  const result = useResource<TrackingEvidence>(forGame(`${path}&${query}`, game))
  if (result.loading) return <Loading/>
  if (!result.data || result.error) return <ErrorState message={result.error || '证据加载失败'} retry={result.reload}/>
  const data = result.data
  return <section className="tracking-evidence-side"><h4>{snapshotId ? '评论原话' : side === 'before' ? 'A · 上次原话' : 'B · 本次原话'}<span>{data.total} 条问题评论</span></h4><p className="tracking-muted">保存于 {date(data.observed_at)}</p>
    {data.items.length ? data.items.map(row => <article className="tracking-quote" key={row.review_id}><strong>评论 #{row.review_id}</strong><small>{date(row.created_at)} 发布</small><ObservationText observations={row.observations}/><details><summary>查看评论全文</summary><TranslatedQuote text={row.text} textZh={row.text_zh} status={row.translation_status}/></details></article>) : <p className="tracking-muted">本侧样本未观察到，不代表问题已解决。</p>}
    {data.total > 5 && <div className="tracking-pagination"><button disabled={page === 0} onClick={() => setPage(page - 1)}>上一页</button><span>{page * 5 + 1}–{Math.min((page + 1) * 5, data.total)} / {data.total}</span><button disabled={(page + 1) * 5 >= data.total} onClick={() => setPage(page + 1)}>下一页</button></div>}
  </section>
}

export function TrackingPage({ game }: { game: string }) {
  return <Monitoring key={game} game={game}/>
}

function Monitoring({ game }: { game: string }) {
  const resource = useResource<TrackingMonitoring>(forGame('/api/tracking/monitoring', game))
  if (resource.loading) return <Loading/>
  if (!resource.data || resource.error) return <ErrorState message={resource.error || '监控数据加载失败'} retry={resource.reload}/>
  return <MonitoringChart key={game} game={game} data={resource.data}/>
}

function MonitoringChart({ data, game }: { data: TrackingMonitoring; game: string }) {
  const [active, setActive] = useState('')
  const [issueId, setIssueId] = useState('')
  const [search, setSearch] = useState('')
  const [limit, setLimit] = useState(12)
  const points = data.points.map(point => ({ ...point, time: Date.parse(point.snapshot.observed_at) }))
  const point = points.find(p => p.snapshot.id === active)
  const metric = point?.values[issueId]
  const openPoint = (id: string, snapshot: string) => {
    setIssueId(id); setActive(snapshot)
    requestAnimationFrame(() => document.getElementById('monitoring-detail')?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
  }
  const issue = data.issues.find(i => i.id === issueId)
  const latest = data.latest
  const rows = latest?.comparable ? latest.issues : []
  const visible = rows.filter(i => i.title.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()))
  const ceiling = Math.abs(rows[0]?.delta_pp || 0)
  return <div className="library-page tracking-page"><PageHeader title="变化追踪"/>
    {!points.length ? <Empty>暂无保存的监控时点。</Empty> : <>
      <header className="monitoring-heading"><div><h2>最近一次样本变化</h2>{latest && <p>{date(latest.before.observed_at)} → {date(latest.after.observed_at)}</p>}</div><span>{points.length} 个监控时点</span></header>
      {latest?.comparable ? <>
        <div className="monitoring-toolbar"><strong>最大占比差 {Math.abs(rows[0]?.delta_pp || 0).toFixed(3)} 个百分点</strong><input type="search" aria-label="搜索问题" placeholder={`搜索全部 ${rows.length} 个问题…`} value={search} onChange={event => { setSearch(event.target.value); setLimit(12) }}/></div>
        <section className="monitoring-changes" aria-label="按占比差排列的问题变化">
          <div className="change-axis"><span>问题</span><span className="change-axis-scale"><span>−{ceiling.toFixed(3)}</span><span>0</span><span>+{ceiling.toFixed(3)}</span></span><span>占比差 · 百分点</span></div>
          {visible.slice(0, limit).map(row => <button key={row.id} className="change-row" aria-controls="monitoring-detail" aria-expanded={issueId === row.id} onClick={() => openPoint(row.id, latest.after.id)}>
            <span className="change-title">{row.title}</span>
            <span className="change-track" aria-hidden="true"><i style={{ left: `${row.delta_pp < 0 && ceiling ? 50 + row.delta_pp / ceiling * 50 : 50}%`, width: `${ceiling ? Math.abs(row.delta_pp) / ceiling * 50 : 0}%`, background: row.delta_pp < 0 ? '#8baee1' : '#83d8c9' }}/></span>
            <strong className={row.delta_pp < 0 ? 'change-minus' : 'change-plus'}>{difference(row.delta_pp)}</strong>
          </button>)}
          {!visible.length && <Empty>没有找到相关问题</Empty>}
        </section>
        <footer className="change-footer"><span>按占比差绝对值排列 · 已显示 {Math.min(limit, visible.length)} / {visible.length}</span>{visible.length > limit && <button onClick={() => setLimit(limit + 24)}>展开更多问题</button>}</footer>
      </> : <>
        <p role="status">{points.length === 1 ? '等待下一次采集，当前已有一个保存时点。' : '最近时点的分析尚未就绪。'}</p>
        {latest?.reasons.map(reason => <p key={reason}>{reason}</p>)}
      </>}
      <details className="monitoring-history"><summary>全部监控时点 · {points.length}</summary><div>{points.map(p => <button key={p.snapshot.id} onClick={() => openPoint(issueId, p.snapshot.id)}>{date(p.snapshot.observed_at)}<span>已分析 {p.snapshot.analyzed.toLocaleString()} / 采集 {p.snapshot.total.toLocaleString()}</span></button>)}</div></details>
      {point && <section id="monitoring-detail" className="tracking-issue-detail">
        <header className="monitoring-heading"><h3>{issue?.title || date(point.snapshot.observed_at)}</h3><button className="monitoring-close" onClick={() => { setActive(''); setIssueId('') }}>收起详情</button></header>
        {issue && <div className="monitoring-plot"><ResponsiveContainer width="100%" height="100%">
          <LineChart data={points} margin={{ top: 16, right: 24, bottom: 16, left: 0 }}>
            <CartesianGrid stroke="#385164" strokeDasharray="3 5" vertical={false}/>
            <XAxis dataKey="time" type="number" domain={['dataMin', 'dataMax']} tickFormatter={shortDate} stroke="#91adbf" minTickGap={35}/>
            <YAxis tickFormatter={percentage} stroke="#91adbf" width={52} domain={[0, 'auto']}/>
            <Tooltip labelFormatter={value => date(new Date(Number(value)).toISOString())} formatter={value => typeof value === 'number' ? percentage(value) : '待分析'} contentStyle={{ background: '#102333', borderColor: '#446177', color: '#d6e7ef' }}/>
            <Line name={issue.title} dataKey={p => p.values[issueId]?.share ?? null} type="linear" stroke="#80d8ce" strokeWidth={2} connectNulls={false} isAnimationActive={false} dot={{ r: 4 }} activeDot={dot => <circle cx={dot.cx} cy={dot.cy} r={7} fill="#80d8ce" onClick={() => setActive(dot.payload.snapshot.id)}/>}/>
          </LineChart>
        </ResponsiveContainer></div>}
        {latest?.issues.find(row => row.id === issueId) && (() => {
          const row = latest.issues.find(row => row.id === issueId)!
          return <p>最近两次：{row.before_count} 条 / {percentage(row.before_share)} → {row.after_count} 条 / {percentage(row.after_share)} · {difference(row.delta_pp)} 个百分点</p>
        })()}
        <p>{date(point.snapshot.observed_at)} · 采集 {point.snapshot.total.toLocaleString()} 条 · 已分析 {point.snapshot.analyzed.toLocaleString()} 条</p>
        {data.analysis && <p>{data.analysis.label} · 分析于 {date(data.analysis.analyzed_at)}</p>}
        {point.reasons.length ? <div role="status">{point.reasons.map(reason => <p key={reason}>{reason}</p>)}</div> : <>
          {metric && <p>{metric.count} 条问题评论 · {percentage(metric.share)}</p>}
          {metric && <EvidenceSide key={`${game}:${active}:${issueId}`} game={game} snapshotId={active} issueId={issueId} side="after"/>}
        </>}
      </section>}
    </>}
  </div>
}

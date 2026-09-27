import { metricLabels, opinion } from '@/lib/tracking'
import { useState } from 'react'
import { sourceBreakdownSchema, type SourceBreakdown } from '@/lib/analysis'
import { ArrowUpRight, CalendarDays, History } from 'lucide-react'
import { CartesianGrid, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { TranslatedQuote } from '@/components/product-ui'
import type { ComparisonScope, Observation, ReviewHistory, TrackingChartData } from '@/lib/tracking'
import '@/pages/tracking.css'

const percentage = (value: number | null) => value === null ? '—' : `${value.toFixed(1)}%`
const number = (value: number | null) => value === null ? '—' : value.toLocaleString('zh-CN')
const localDate = (value: string, offset = '480') => new Date(Date.parse(value) + Number(offset) * 60000).toISOString().slice(0, 19)
const date = (value: string, offset = '480') => localDate(value, offset).replace('T', ' ').slice(0, 16)


const sourceLabels: Record<keyof SourceBreakdown['groups'], string> = { shared: '共同评论', first_seen: '补采旧评论', new_review: '期间新评论',
  unknown_time: '时间无法判定', not_seen: '后期未见', scope_entered: '进入语言范围', scope_left: '移出语言范围', text_changed: '保存文本变化', recommendation_changed: '推荐状态变化' }
const sourceNotes: Record<keyof SourceBreakdown['groups'], string> = { shared: '两次记录中相同的评论 ID', first_seen: '前期截止时或更早发布，前期未采集到', new_review: '前期截止后、后期截止时或更早发布',
  unknown_time: '发布时间缺失或晚于保存时间', not_seen: '后期未采集到，不等于删除', scope_entered: '同一评论的语言信息改变，不是新评论', scope_left: '同一评论移出所选语言，不等于删除',
  text_changed: '共同评论的子集；可能包含采集格式差异，文本不同不等于态度改变', recommendation_changed: '共同评论的子集；可与文本变化重叠' }
function SourceMembers({ ids, onHistory }: { ids: string[]; onHistory: (id: string) => void }) {
  const [page, setPage] = useState(0)
  return <div className="source-members"><div>{ids.slice(page * 10, (page + 1) * 10).map(id => <button key={id} className="text-action" onClick={() => onHistory(id)}>评论 #{id}<History aria-hidden="true"/></button>)}</div>
    {ids.length > 10 && <div className="tracking-pagination"><button disabled={!page} onClick={() => setPage(page - 1)}>上一页</button><span>{page + 1} / {Math.ceil(ids.length / 10)}</span><button disabled={(page + 1) * 10 >= ids.length} onClick={() => setPage(page + 1)}>下一页</button></div>}</div>
}
export function ComparisonSources({ value, periods = false, onHistory }: { value: unknown; periods?: boolean; onHistory?: (id: string) => void }) {
  const parsed = sourceBreakdownSchema.safeParse(value)
  if (!parsed.success) return <p role="status" className="tracking-muted">这份结果暂无可核查的来源分组，请重新查询比较。</p>
  const data = parsed.data
  const primary = ['shared', 'first_seen', 'new_review', 'unknown_time', 'scope_entered'] as const
  const events = ['text_changed', 'recommendation_changed', 'not_seen', 'scope_left'] as const
  const group = (key: keyof SourceBreakdown['groups']) => {
    const g = data.groups[key]
    const title = <><span>{sourceLabels[key]}</span><strong>{g.count.toLocaleString('zh-CN')}<small> 条</small></strong></>
    return <article className={`source-group source-${key}`} key={key} data-source={key}>
      {onHistory && g.count ? <details><summary>{title}<span className="source-view">查看评论</span></summary><p>{sourceNotes[key]}</p><SourceMembers key={g.review_ids.join(',')} ids={g.review_ids} onHistory={onHistory}/></details> : <>{title}<p>{sourceNotes[key]}</p></>}
    </article>
  }
  return <section className="comparison-sources" aria-label="评论变化来源">
    <header><h3>这次比较的评论来自哪里</h3><span>A · {data.before_total} 条 → B · {data.after_total} 条</span></header>
    {periods && <p className="source-basis-note">下方说明两次保存记录的采集来源，不是上方两段发布时间内的评论数量；不能将两批玩家当作同一批人改口。</p>}
    <p className="source-summary">{data.summary}</p>
    {!!data.after_total && <div className="source-composition" role="img" aria-label={`后期评论构成：${primary.map(k => `${sourceLabels[k]} ${data.groups[k].count} 条`).join('，')}`}>
      {primary.filter(k => data.groups[k].count).map(k => <span key={k} className={`source-${k}`} style={{ flexGrow: data.groups[k].count }} title={`${sourceLabels[k]} ${data.groups[k].count} 条`}/>)}</div>}
    <div className="source-groups">{primary.filter(k => ['shared', 'first_seen', 'new_review'].includes(k) || data.groups[k].count).map(group)}</div>
    <div className="source-events">{events.filter(k => k === 'text_changed' || data.groups[k].count).map(group)}</div>
    <details className="source-rules"><summary>分组口径与时间范围</summary><p>按两次保存时点和所选语言统计全部已采集评论，包含尚未分析的评论；不随下方问题、类别或表述指标筛选变化。</p><p>{date(data.before_at)} → {date(data.after_at)} · 北京时间 · 语言：{data.language === 'all' ? '全部语言' : data.language === 'chinese' ? '中文（简体、繁体）' : data.language}</p><p>前期截止时刻发布的评论归入旧评论；各来源组按评论 ID 去重。文本变化与推荐状态变化只在共同评论内统计，译文更新不算原文变化。{onHistory && <>点击评论可查看完整历史，历史中会标明各次保存时间。</>}</p></details>
  </section>
}
const claimLabels: Record<string, string> = { complaint: '投诉', improved: '明确改善', worsened: '明确恶化' }
export function ObservationText({ observations }: { observations: Observation[] }) {
  return observations.length ? <>{observations.map(item => <div className="tracking-observation-item" key={item.observation_id}><p>{item.description}</p><TranslatedQuote text={item.evidence} textZh={item.evidence_zh} status={item.translation_status}/><small>{claimLabels[item.claim] || item.claim}</small>{item.change_evidence && item.change_evidence !== item.evidence && <TranslatedQuote text={item.change_evidence} textZh={item.change_evidence_zh} status={item.change_translation_status}/>}</div>)}</> : <p className="tracking-muted">未发现对应反馈；不表示问题已解决。</p>
}

export function Dumbbell({ issue, max, comparable }: { issue: { before_share: number | null; after_share: number | null }; max: number; comparable: boolean }) {
  const x = (value: number | null) => 14 + (value ?? 0) / max * 272
  return <div className="tracking-dumbbell">{comparable ? <svg viewBox="0 0 300 32" role="img" aria-label={`前期 ${percentage(issue.before_share)}，后期 ${percentage(issue.after_share)}`}><line x1="14" x2="286" y1="16" y2="16" stroke="#314a5c"/><line x1={x(issue.before_share)} x2={x(issue.after_share)} y1="16" y2="16" stroke="#81aabf" strokeWidth="3"/><circle cx={x(issue.before_share)} cy="16" r="5.5" fill="#162b3b" stroke="#bdcbd4" strokeWidth="2"/><circle cx={x(issue.after_share)} cy="16" r="4" fill="#66c9ff"/></svg> : <span className="tracking-unavailable-line">缺少可比较数据</span>}</div>
}

export function TrendView({ data, scope, issueId = "", measure = "share", onMeasure, onIssueChange, onCompare }: { data: TrackingChartData; scope: ComparisonScope; issueId?: string; measure?: string; onMeasure?: (m: string) => void; onIssueChange?: (id: string) => void; onCompare?: (before: string, after: string) => void }) {
  const points = data.points.map(p => ({ ...p, timestamp: Date.parse(p.at) }))
  const valid = points.filter(p => p.value !== null)
  const min = points[0]?.timestamp ?? 0
  const max = points.at(-1)?.timestamp ?? 0
  const events = data.events.filter(e => Date.parse(e.at) >= min && Date.parse(e.at) <= max)
  return <section className="tracking-trend"><div className="tracking-trend-tools">{onIssueChange ? <label>追踪对象<select value={issueId} onChange={e => onIssueChange(e.target.value)}><option value="">整体推荐情况</option>{data.issues.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label> : <h4>变化过程</h4>}<div className="tracking-segment" role="group" aria-label="走势单位"><button aria-pressed={measure === 'share'} onClick={() => onMeasure?.('share')}>占比</button><button aria-pressed={measure === 'count'} onClick={() => onMeasure?.('count')}>数量</button></div></div>
    <p className="tracking-muted">{data.granularity === 'snapshot' ? '按实际保存时间' : data.granularity === 'week' ? '按评论发布时间分周 · 日样本较少' : '按评论发布时间分日'} · {issueId ? metricLabels[scope.metric] : '推荐评论'}{points.some(p => p.small && p.total > 0) ? ' · 部分样本少于 30 条' : ''}</p>
    {valid.length < 3 && <p className="tracking-muted">{valid.length === 2 ? '仅有两个可比观察点，期间变化未知。' : '可比记录不足，先查看下方各次记录。'}</p>}
    {valid.length >= 2 && <div className="tracking-chart" role="img" aria-label="反馈变化趋势，详细数字见下方记录"><ResponsiveContainer width="100%" height="100%"><LineChart data={points} margin={{ top: 18, right: 26, left: 0, bottom: 6 }}><CartesianGrid vertical={false} stroke="#304b6055"/><XAxis type="number" scale="time" dataKey="timestamp" domain={['dataMin', 'dataMax']} tickFormatter={v => date(new Date(v).toISOString(), scope.offset).slice(5, 10)} tickLine={false} axisLine={false}/><YAxis domain={measure === 'share' ? [0, 100] : [0, 'auto']} unit={measure === 'share' ? '%' : ''} allowDecimals={measure === 'share'} width={48} tickLine={false} axisLine={false}/><Tooltip labelFormatter={v => date(new Date(Number(v)).toISOString(), scope.offset)} formatter={v => [typeof v === 'number' ? measure === 'share' ? percentage(v) : `${number(v)} 条` : '—', issueId ? metricLabels[scope.metric] : '推荐评论']} contentStyle={{ background: '#1c3446', border: '1px solid #456b86', color: '#d7e9f4' }}/>{events.map(e => e.precision === 'day' ? <ReferenceArea key={e.id} x1={Date.parse(e.at)} x2={Math.min(max, Date.parse(e.at) + 86400000)} fill="#d7b76b" fillOpacity={.12}/> : <ReferenceLine key={e.id} x={Date.parse(e.at)} stroke="#c7ab70" strokeDasharray="4 4"/>)}<Line type="linear" dataKey="value" stroke="#76c8f5" strokeWidth={2} dot={{ r: 4, fill: '#142a3b' }} connectNulls={false} isAnimationActive={false}/></LineChart></ResponsiveContainer></div>}
    {events.length > 0 && <div className="tracking-event-markers">{events.map(e => <span key={e.id}><CalendarDays/>{e.name} · {date(e.at, scope.offset)}{e.precision === 'day' ? '（版本日）' : ''}{e.url && <a href={e.url} target="_blank" rel="noreferrer">公告<ArrowUpRight/></a>}</span>)}</div>}
    <details className="tracking-data-notes" open={valid.length < 3}><summary>各次记录与数据说明 · {points.length} 项</summary>{data.reasons.length > 0 && <p>{data.reasons.join(' ')}</p>}<div className="tracking-point-list">{points.map((p, i) => <div className="tracking-point" key={`${p.at}:${i}`}><span>{date(p.at, scope.offset)}{p.end && <small>至 {date(p.end, scope.offset)}</small>}</span><span>{number(p.count)} 条 · {percentage(p.share)}<small>采集 {p.total} / 已分析 {p.analyzed}{!p.complete ? ' · 暂不可比' : p.small ? ' · 样本较少' : ''}</small></span>{scope.mode === 'snapshots' && i > 0 && p.snapshot_id && points[i - 1].snapshot_id && onCompare && <button className="text-action" onClick={() => onCompare(points[i - 1].snapshot_id!, p.snapshot_id!)}>对比前次<ArrowUpRight/></button>}</div>)}</div></details>
  </section>
}

export function HistoryView({ data, offset = "480" }: { data: ReviewHistory; offset?: string }) {
  return <section className="tracking-section"><h3>评论 #{data.review_id} 的历史</h3>{data.history.map(item => <article key={item.snapshot.id} className="tracking-history-entry"><h4>{date(item.snapshot.observed_at, offset)} 保存 · {item.status === 'analyzed' ? '已分析' : item.status === 'pending' ? '待分析' : '本次未采集'}</h4>{item.text !== null && <p className="tracking-muted">发布：{item.created_at ? date(item.created_at, offset) : '时间未知'} · 修改：{item.updated_at ? date(item.updated_at, offset) : '时间未知'} · {offset === '480' ? '北京时间' : 'UTC'}</p>}<span>{opinion(item.recommended)}</span>{item.text !== null ? <TranslatedQuote text={item.text} textZh={item.text_zh} status={item.translation_status}/> : <p>该次没有保存原文，无法判断评论删除或问题解决。</p>}<ObservationText observations={item.observations}/></article>)}</section>
}

import { metricLabels } from '@/lib/tracking'
import { Component, createContext, useContext, useRef, useState, type ReactNode, type FormEvent } from 'react'
import { useResource } from '@/lib/api'
import { parseAnalysis, parseBlock, type AnalysisResponse, type AnalysisBlock, type AnalysisResult, type AnalysisFilters, type FrozenEvidence } from '@/lib/analysis'
import type { ComparisonScope, Metric, TrackingCatalog } from '@/lib/tracking'
import { ComparisonSources, Dumbbell, HistoryView, ObservationText, TrendView } from './tracking-blocks'
import { StatsDisplay } from './tool-ui/stats-display'
import { TranslatedQuote } from './product-ui'
import './analysis-ui.css'

// oxlint-disable-next-line react/only-export-components -- shared with the single assistant entry point.
export const AnalysisActions = createContext<(filters: AnalysisFilters, question?: string) => void>(() => {})
const percent = (n: number | null) => n === null ? '—' : `${Number(n.toFixed(3))}%`
const when = (s: string | null) => s ? new Date(s).toLocaleString('zh-CN', { hour12: false }) : '无记录'
const language = (s: unknown) => ({ all: '全部语言', chinese: '中文（简体、繁体）', schinese: '简体中文', tchinese: '繁体中文', english: '英语' })[String(s)] || String(s)

class BlockBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() { return this.state.failed ? <p role="status">此分析模块暂不可用，其他结果仍可查看。</p> : this.props.children }
}

export function RangePicker({ filters = {}, question, requireComparison = false }: { filters?: AnalysisFilters; question?: string; requireComparison?: boolean }) {
  const catalog = useResource<TrackingCatalog>('/api/tracking')
  const apply = useContext(AnalysisActions)
  const [mode, setMode] = useState(String(requireComparison && filters.mode === 'current' ? 'snapshots' : filters.mode || 'snapshots'))
  const [error, setError] = useState('')
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const next: AnalysisFilters = { ...filters, mode }
    for (const [k, v] of form) next[k] = String(v)
    for (const key of ['start', 'end', 'before_start', 'before_end', 'after_start', 'after_end']) {
      if (next[key]) next[key] = new Date(`${next[key]}Z`).toISOString()
    }
    next.offset = '0'
    if (mode !== 'current' && (!next.before || !next.after || next.before === next.after)) { setError('请选择两个不同的保存时点'); return }
    setError(''); apply(next, question)
  }
  return <form className="analysis-range" onSubmit={submit}>
    <label>分析方式<select aria-label="助手分析方式" value={mode} onChange={e => setMode(e.target.value)}><option value="current">当前评论</option><option value="snapshots">两个累计时点</option><option value="periods">两批新评论</option></select></label>
    <label>语言<select name="language" defaultValue={String(filters.language || 'all')}><option value="all">全部语言</option><option value="chinese">中文（简体、繁体）</option>{catalog.data?.languages.map(l => <option key={l} value={l}>{language(l)}</option>)}</select></label>
    {mode !== 'current' && ['before', 'after'].map(side => <label key={side}>{side === 'before' ? '前期' : '后期'}原话保存时点<select name={side} required defaultValue={String(filters[side] || '')}><option value="">请选择保存记录</option>{catalog.data?.snapshots.map(s => <option key={s.id} value={s.id}>{when(s.observed_at)} · {s.total} 条</option>)}</select></label>)}
    {(mode === 'current' ? ['start', 'end'] : mode === 'periods' ? ['before_start', 'before_end', 'after_start', 'after_end'] : []).map(key => <label key={key}>{({ start: '开始', end: '截止（不含）', before_start: '前期开始', before_end: '前期截止（不含）', after_start: '后期开始', after_end: '后期截止（不含）' })[key]} · UTC<input name={key} type="datetime-local" required={mode === 'periods'} defaultValue={typeof filters[key] === 'string' ? String(filters[key]).slice(0, 16) : ''}/></label>)}

    {(error || catalog.error) && <p role="alert">{error || catalog.error}</p>}
    <button className="text-action" type="submit" disabled={catalog.loading}>应用范围并分析</button>
  </form>
}

export function AnalysisScope({ response }: { response: AnalysisResponse }) {
  const c = response.context
  const f = c.filters
  return <div className="analysis-scope" aria-label="回答数据范围">
    <strong>{c.product} · {f.mode === 'periods' ? '两批新评论' : f.mode === 'snapshots' ? '两个累计时点' : '当前评论'} · {language(f.language)}</strong>
    <span>{f.mode === 'periods' ? `${when(String(f.before_start))} — ${when(String(f.before_end))} / ${when(String(f.after_start))} — ${when(String(f.after_end))}` : f.mode === 'snapshots' ? c.samples.map(s => when(s.observed_at)).join(' → ') : `${when(typeof f.start === 'string' && f.start || c.range_start)} — ${when(typeof f.end === 'string' && f.end || c.range_end)}`}</span>
    {c.topics.length > 0 && <span>主题：{c.topics.length > 2 ? `${c.topics.length} 个已选问题` : c.topics.join('、')}</span>}
    {c.samples.map((s, i) => <span key={`${s.id}:${i}`}>{c.samples.length > 1 ? i ? 'B：' : 'A：' : ''}样本 {s.total} · 已分析 {s.analyzed} · 覆盖率 {percent(s.analysis_coverage ?? null)}</span>)}
    <span>口径：{metricLabels[f.metric as Metric]} · 更新 {when(c.updated_at)}</span>
    <small>数据版本 {c.data_version.slice(0, 8)}</small>
  </div>
}

function EvidenceList({ rows, metric }: { rows: FrozenEvidence[]; metric: Metric }) {
  const [page, setPage] = useState(0)
  const [order, setOrder] = useState('newest')
  const filtered = rows.filter(r => metric === 'mentions' || r.observations.some(o => metric === 'improved' ? o.claim === 'improved' : ['complaint', 'worsened'].includes(o.claim)))
    .toSorted((a, b) => (Date.parse(b.created_at) - Date.parse(a.created_at)) * (order === 'newest' ? 1 : -1))
  return <div className="analysis-evidence"><p>{metricLabels[metric]} · {filtered.length} 条</p>
    <label>原话排序<select value={order} onChange={e => { setOrder(e.target.value); setPage(0) }}><option value="newest">最新发表优先</option><option value="oldest">最早发表优先</option></select></label>
    {filtered.slice(page * 3, page * 3 + 3).map(row => <article key={row.review_id}><strong>评论 #{row.review_id}</strong><small>{when(row.created_at)} · {language(row.language)}</small><ObservationText observations={row.observations}/><details><summary>全文与历史版本信息</summary><TranslatedQuote text={row.text} textZh={row.text_zh} status={row.translation_status}/><small>修改 {when(row.updated_at)} · 保存 {when(row.observed_at)} · 评论版本 {row.review_version_id}</small></details></article>)}
    {!filtered.length && <p>当前范围没有此类表述；不表示问题已解决。</p>}
    {filtered.length > 3 && <div className="tracking-pagination"><button disabled={!page} onClick={() => setPage(page - 1)}>上一页</button><span>{page + 1} / {Math.ceil(filtered.length / 3)}</span><button disabled={(page + 1) * 3 >= filtered.length} onClick={() => setPage(page + 1)}>下一页</button></div>}
  </div>
}

type IssueRows = Extract<AnalysisResult, { kind: 'issues' | 'comparison' | 'evidence' }>
function IssueEvidence({ item, initial }: { item: IssueRows['items'][number]; initial: Metric }) {
  const [metric, setMetric] = useState(initial)
  const before = item.evidence.before?.items || []
  const after = item.evidence.after?.items || []
  const pairs = before.flatMap(a => { const b = after.find(r => r.review_id === a.review_id); return b && a.text !== b.text ? [{ a, b }] : [] })
  return <details className="analysis-evidence-entry"><summary>查看原话、其他表述与反例</summary>
    <div className="tracking-segment" role="group" aria-label="证据表述筛选">{(['mentions', 'complaints', 'improved'] as const).map(m => <button key={m} aria-pressed={metric === m} onClick={() => setMetric(m)}>{metricLabels[m]}</button>)}</div>
    {Object.entries(item.evidence).map(([side, data]) => data && <section key={side}><h4>{side === 'before' ? 'A · 前期' : before.length ? 'B · 后期' : '当前'}原话</h4><EvidenceList key={`${side}:${metric}`} rows={data.items} metric={metric}/></section>)}
    <details><summary>同一评论改评 · {pairs.length} 条</summary>{pairs.map(({ a, b }) => <article key={a.review_id}><strong>评论 #{a.review_id}</strong><p>A · {when(a.observed_at)}</p><TranslatedQuote text={a.text} textZh={a.text_zh} status={a.translation_status}/><p>B · {when(b.observed_at)}</p><TranslatedQuote text={b.text} textZh={b.text_zh} status={b.translation_status}/></article>)}</details>
  </details>
}

function IssueModule({ data, block }: { data: IssueRows; block: AnalysisBlock }) {
  const [sort, setSort] = useState<string>(block.sort || 'default')
  const [limit, setLimit] = useState(block.limit || 5)
  const items = [...data.items]
  if (sort === 'count') items.sort((a, b) => ('count' in b ? b.count : b.after_count) - ('count' in a ? a.count : a.after_count))
  if (sort === 'change' && data.kind === 'comparison') items.sort((a, b) => ('delta_pp' in b ? Math.abs(b.delta_pp || 0) : 0) - ('delta_pp' in a ? Math.abs(a.delta_pp || 0) : 0))
  const metric = data.kind === 'comparison' ? data.scope.metric : data.metric
  return <section><header><h3>{data.kind === 'comparison' ? data.status === 'sample' ? '样本问题差异' : '前后变化' : data.kind === 'evidence' ? '原话核查' : '主要问题'}</h3><label>排序<select value={sort} onChange={e => setSort(e.target.value)}><option value="default">分析顺序</option><option value="count">评论数量</option>{data.kind === 'comparison' && <option value="change">占比变化幅度</option>}</select></label></header>
    {data.kind === 'comparison' && <><p>分母 A：{data.before.analyzed} / B：{data.after.analyzed} · {metricLabels[metric]}</p>{data.source_breakdown && <ComparisonSources value={data.source_breakdown} periods={data.scope.mode === 'periods'}/>}</>}
    {!items.length && <p>当前范围未观察到相关已分析问题。</p>}
    {items.slice(0, limit).map(item => <article className="analysis-issue" key={item.id}><h4>{item.title}</h4>
      {'before_count' in item ? <><Dumbbell issue={item} max={100} comparable={data.kind === 'comparison' && data.comparable}/><p>{item.before_count} 条 · {percent(item.before_share)} → {item.after_count} 条 · {percent(item.after_share)}<br/>{item.delta_pp === null ? '暂不计算差值' : `${item.delta_pp < 0 ? '下降' : item.delta_pp > 0 ? '上升' : '变化'} ${Math.abs(item.delta_pp)} 个百分点`}{item.note}</p><small>全部提及 {item.before_mentions} → {item.after_mentions} · 投诉 {item.before_complaints} → {item.after_complaints} · 明确改善 {item.before_improved} → {item.after_improved}</small></> : <><p>{metricLabels[metric]}：{item.count}/{data.kind !== 'comparison' ? data.denominator : 0} · {percent(item.share)}</p><small>全部提及 {item.mentions} · 投诉 {item.complaints} · 明确改善 {item.improved}</small></>}
      <IssueEvidence item={item} initial={metric}/>
    </article>)}
    {items.length > limit && <button className="text-action" onClick={() => setLimit(items.length)}>查看其他问题（{items.length - limit}）</button>}
  </section>
}

function AnalysisModule({ response, block }: { response: AnalysisResponse; block: AnalysisBlock }) {
  let data: AnalysisResult
  try { data = parseBlock(block, response) } catch { return <p role="status">此模块未通过校验，已停止显示。可调整范围重新分析。</p> }
  switch (data.kind) {
    case 'stats': return data.stats.length ? <>{data.stats.map((s, i) => <StatsDisplay key={s.id} id={`${response.id}-stats-${i}`} title={data.stats.length > 1 ? i ? '后期概况' : '前期概况' : '样本概况'} stats={[{ key: 'total', label: '采集评论', value: s.total }, { key: 'analyzed', label: '已分析', value: s.analyzed }, { key: 'complaints', label: '含投诉评论', value: s.complaints }]}/>)}</> : <p>当前范围暂无可展示的数据。</p>
    case 'issues': case 'comparison': case 'evidence': return <IssueModule data={data} block={block}/>
    case 'trend': return <FrozenTrend data={data} scope={response.context.filters as ComparisonScope}/>
    case 'revision': return <HistoryView data={data}/>
    case 'range': return <RangePicker filters={response.context.filters} question={response.analysis_question} requireComparison/>
  }
}

function FrozenTrend({ data, scope }: { data: Extract<AnalysisResult, { kind: 'trend' }>; scope: ComparisonScope }) {
  const [measure, setMeasure] = useState('share')
  const points = data.points.map(p => ({ ...p, value: p.value === null ? null : measure === 'share' ? p.share : p.count }))
  return <TrendView data={{ ...data, points }} scope={scope} issueId={data.issue_id || ''} measure={measure} onMeasure={setMeasure}/>
}

export function AnalysisUI({ result }: { result?: unknown }) {
  const root = useRef<HTMLDivElement>(null)
  if (!result) return <div className="tool-loading" role="status">正在查询并核查分析结果…</div>
  let response: AnalysisResponse
  try { response = parseAnalysis(result) } catch { return <p className="tool-error" role="status">分析响应未通过校验，未显示未经核查的结论。请重试或使用固定页面。</p> }
  const evidenceAvailable = response.blocks.some(b => ['issues', 'comparison', 'evidence'].includes(b.type))
  return <div className="analysis-answer" data-analysis-mode={response.mode} ref={root}>
    <AnalysisScope response={response}/>
    {response.mode === 'fallback' && <p className="analysis-warning">模型分析未完成 · 当前为事实降级展示</p>}
    <p className="analysis-summary">{response.summary.text}</p>
    {evidenceAvailable && <button className="text-action analysis-evidence-shortcut" onClick={() => { const entry = root.current?.querySelector<HTMLDetailsElement>('.analysis-evidence-entry'); if (entry) { entry.open = true; entry.scrollIntoView({ block: 'nearest', behavior: 'instant' }); entry.querySelector('summary')?.focus() } }}>查看评论证据与反例</button>}
    <details className="analysis-summary-sources"><summary>核对摘要依据</summary>{response.summary.refs.map((r, i) => <p key={i}>{parseBlock({ type: (response.results[r.data_ref] as AnalysisResult).kind, data_ref: r.data_ref }, response).facts[r.fact_id]}</p>)}</details>
    {response.blocks.map((b, i) => <BlockBoundary key={`${response.id}:${i}`}><AnalysisModule response={response} block={b}/></BlockBoundary>)}
    <details className="analysis-warnings"><summary>数据限制 · {response.warnings.length} 项</summary>{response.warnings.map(w => <p key={w}>{w}</p>)}</details>
  </div>
}

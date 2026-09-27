import { useState } from 'react'
import { ArrowUpRight, CheckCircle2, CircleHelp, Compass, Eye, FlaskConical, LoaderCircle, Radar, Search } from 'lucide-react'
import { ErrorState, Loading, PageHeader, TranslatedQuote } from '@/components/product-ui'
import { forGame, post, useResource } from '@/lib/api'
import type { DecisionBrief, DecisionBriefResponse, DecisionOutcomeUpdate } from '@/lib/domain'
import type { TrackingComparison } from '@/lib/tracking'
import { EvidenceSide } from './tracking'
import './decisions.css'

type DecisionComparison = { comparison: TrackingComparison | null; reasons: string[]; issue_reasons: Record<string, string> }
type Followup = ReturnType<typeof useResource<DecisionComparison>>
const date = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })

function comparisonContext(resource: Followup, issueId: string) {
  const result = resource.data
  const comparison = result?.comparison
  const issue = comparison?.issues.find(row => row.id === issueId)
  const reason = resource.loading ? '正在读取样本对照…' : resource.error ? '样本对照读取失败，请在下方重试。' :
    result?.issue_reasons[issueId] || (!comparison?.comparable ? result?.reasons.join(' ') || '暂无可用样本对照。' : !issue ? '两侧样本均未观察到该问题。' : '')
  return { comparison, issue, reason }
}

function FollowupComparison({ game, issueId, resource }: { game: string; issueId: string; resource: Followup }) {
  const [expanded, setExpanded] = useState(false)
  if (resource.loading) return <p>正在读取样本对照…</p>
  if (resource.error) return <ErrorState message={resource.error} retry={resource.reload}/>
  const { comparison, issue, reason } = comparisonContext(resource, issueId)
  return <section className="decision-validation decision-comparison"><h3>前后样本 · 跟进参考</h3>
    {comparison && <p>A {date(comparison.before.observed_at)} · 已分析 {comparison.before.analyzed} 条{comparison.before.excluded ? ` · 排除 ${comparison.before.excluded} 条原始空文本` : ''}<br/>B {date(comparison.after.observed_at)} · 已分析 {comparison.after.analyzed} 条{comparison.after.excluded ? ` · 排除 ${comparison.after.excluded} 条原始空文本` : ''}</p>}
    {reason ? <p>{reason}</p> : issue && <p className="decision-comparison-value">{issue.before_count} 条 / {issue.before_share.toFixed(1)}% → {issue.after_count} 条 / {issue.after_share.toFixed(1)}%<br/>占比差 {issue.delta_pp > 0 ? '+' : ''}{issue.delta_pp.toFixed(2)} 个百分点</p>}

    {comparison?.analysis && <p>{comparison.analysis.label} · {date(comparison.analysis.analyzed_at)}</p>}
    {issue && comparison?.comparable && !reason && <><button className="text-action" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>查看两侧原话</button>{expanded && <div className="tracking-review-sides"><EvidenceSide game={game} scope={comparison.scope} issueId={issueId} side="before"/><EvidenceSide game={game} scope={comparison.scope} issueId={issueId} side="after"/></div>}</>}
    <p><a href={`/?page=tracking&game=${encodeURIComponent(game)}`}>打开变化追踪总览 →</a></p>
  </section>
}

function OutcomeForm({ game, brief, onSaved }: { game: string; brief: DecisionBrief; onSaved: (data: DecisionBriefResponse) => void }) {
  const [value, setValue] = useState<DecisionOutcomeUpdate>({ final_action: brief.review.final_action || brief.action, execution: brief.execution,
    validation: { hypothesis: brief.validation.hypothesis, metric: brief.validation.metric, baseline: brief.validation.baseline,
      followup_window: brief.validation.followup_window, observed_result: brief.validation.observed_result, outcome: brief.validation.outcome } })
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  if (!['accepted', 'modified'].includes(brief.review.status)) return null
  const labels = { hypothesis: '验证假设', metric: '观察指标', baseline: '基线', followup_window: '观察窗口', observed_result: '实际观察与依据（可记录比较时点）' }
  async function save() {
    setBusy(true); setMessage('')
    try { onSaved(await post<DecisionBriefResponse>('/api/chatbi/decisions/outcome', { game, decision_id: brief.id, ...value })); setMessage('已保存') }
    catch (error) { setMessage(error instanceof Error ? error.message : '保存失败') }
    finally { setBusy(false) }
  }
  return <details className="decision-outcome"><summary>执行与验证记录{brief.outcome_tracking.updated_at ? ` · 已保存 ${date(brief.outcome_tracking.updated_at)}` : ''}</summary>
    <form onSubmit={event => { event.preventDefault(); void save() }}><fieldset disabled={busy}>
      <label>最终行动<textarea required minLength={2} maxLength={500} value={value.final_action} onChange={e => setValue({ ...value, final_action: e.target.value })}/></label>
      <label>执行状态<select value={value.execution.status} onChange={e => setValue({ ...value, execution: { status: e.target.value as DecisionOutcomeUpdate['execution']['status'], shipped_at: null } })}><option value="planned">计划中</option><option value="shipped">已上线</option><option value="cancelled">已取消</option></select></label>
      {value.execution.status === 'shipped' && <label>上线时间（含时区，如 2026-09-27T10:00:00+08:00）<input required value={value.execution.shipped_at || ''} onChange={e => setValue({ ...value, execution: { ...value.execution, shipped_at: e.target.value } })}/></label>}
      {Object.entries(labels).map(([key, label]) => <label key={key}>{label}<textarea maxLength={key === 'hypothesis' || key === 'observed_result' ? 500 : 300} value={value.validation[key as keyof typeof labels]} onChange={e => setValue({ ...value, validation: { ...value.validation, [key]: e.target.value } })}/></label>)}
      <label>人工验证结论<select value={value.validation.outcome || ''} onChange={e => setValue({ ...value, validation: { ...value.validation, outcome: (e.target.value || null) as DecisionOutcomeUpdate['validation']['outcome'] } })}><option value="">尚未判断</option><option value="inconclusive">证据不足</option><option value="unchanged">未观察到变化</option><option value="improved">改善（人工判断）</option><option value="worsened">恶化（人工判断）</option></select></label>
      <button className="primary-action" type="submit">{busy ? '保存中…' : '保存跟进记录'}</button>
    </fieldset>{message && <p role="status">{message}</p>}</form>
  </details>
}

const icons = { act: CheckCircle2, investigate: Search, validate: FlaskConical, monitor: Radar }
type ReviewStatus = 'accepted' | 'modified' | 'rejected' | 'pending'
function Brief({ game, followup, onSaved, brief, index, openIssue, reviewing, reason, onReason, onReview }: { game: string; followup: Followup; onSaved: (data: DecisionBriefResponse) => void; brief: DecisionBrief; index: number; openIssue: (id: string) => void; reviewing: boolean; reason: string; onReason: (value: string) => void; onReview: (id: string, status: ReviewStatus, reason: string) => void }) {
  const TypeIcon = icons[brief.type]
  const { issue: comparisonIssue, reason: comparisonReason } = comparisonContext(followup, brief.issue.id)
  const sampleSummary = comparisonReason || (comparisonIssue ? `可对照，${comparisonIssue.before_share.toFixed(1)}% → ${comparisonIssue.after_share.toFixed(1)}%，差 ${comparisonIssue.delta_pp > 0 ? '+' : ''}${comparisonIssue.delta_pp.toFixed(2)} 个百分点` : '暂无可用样本对照。')
  return <article className={`decision-brief decision-${brief.type}`}>
    <div className="decision-index" aria-hidden="true">{String(index + 1).padStart(2, '0')}</div>
    <div className="decision-body">
      <header className="decision-heading"><div><span className="decision-type"><TypeIcon/>{brief.type_label}</span><span className={`decision-confidence confidence-${brief.confidence.level}`}>{brief.confidence.label}</span></div><button className="text-action" onClick={() => openIssue(brief.issue.id)}>查看依据问题<ArrowUpRight/></button></header>
      <p className="decision-source">回应问题 / {brief.issue.title}</p>
      <h2>{brief.title}</h2>
      <section className="decision-next"><span>下一步</span><p>{brief.action}</p></section>
      <section className="decision-next"><span>Decision Gate</span><p>{brief.gate.coverage.unique_reviews} 条独立评论 · 主表现占 {(brief.gate.concentration.largest_manifestation_share * 100).toFixed(0)}% · 具体反馈占 {(brief.gate.specificity.specific_observation_share * 100).toFixed(0)}% · {brief.gate.minority.manifestation_count} 个少数表现<br/><span className="decision-sample-summary">当前样本对照：{sampleSummary}</span></p></section>
      <FollowupComparison game={game} issueId={brief.issue.id} resource={followup}/>
      <div className="decision-facts">{brief.facts.filter(fact => fact.label !== '趋势').map(fact => <div key={fact.label}><span>{fact.label}</span><strong>{fact.value}</strong></div>)}</div>
      <div className="decision-reasoning"><section><h3>为什么是这一步</h3><p>{brief.rationale}</p></section><section><h3>仍然未知</h3><p>{brief.unknown}</p></section></div>
      <section className="decision-validation"><div><FlaskConical/><span>验证计划</span></div><p>{brief.validation.focus}</p><dl><div><dt>观察指标</dt><dd>{brief.validation.metric}</dd></div><div><dt>当前基线</dt><dd>{brief.validation.baseline}</dd></div><div><dt>成功信号</dt><dd>{brief.validation.success_signal}</dd></div><div><dt>开始比较</dt><dd>{brief.validation.minimum_sample}</dd></div></dl></section>
      <details className="decision-evidence"><summary><Eye/>查看支撑原话 <span>{brief.evidence.length} 条</span></summary><div>{brief.evidence.map((item, evidenceIndex) => <figure key={`${item.observation_id}-${evidenceIndex}`}><TranslatedQuote text={item.quote} textZh={item.quote_zh} status={item.translation_status}/><figcaption>Steam 评论 #{item.review_id}</figcaption></figure>)}</div></details>
      <footer className="decision-review"><div><span>团队判断</span><small>{brief.review.status === 'accepted' ? '已采纳为下一步' : brief.review.status === 'modified' ? '修改后采纳' : brief.review.status === 'rejected' ? '暂不采纳' : '等待评审'}</small><input value={reason} maxLength={300} onChange={event => onReason(event.target.value)} placeholder="修改或驳回时填写原因" aria-label="评审原因"/></div><div><button disabled={reviewing} aria-pressed={brief.review.status === 'accepted'} onClick={() => onReview(brief.id, brief.review.status === 'accepted' ? 'pending' : 'accepted', reason)}>采纳</button><button disabled={reviewing || (!reason.trim() && brief.review.status !== 'modified')} aria-pressed={brief.review.status === 'modified'} onClick={() => onReview(brief.id, brief.review.status === 'modified' ? 'pending' : 'modified', reason)}>修改后采纳</button><button disabled={reviewing || (!reason.trim() && brief.review.status !== 'rejected')} aria-pressed={brief.review.status === 'rejected'} onClick={() => onReview(brief.id, brief.review.status === 'rejected' ? 'pending' : 'rejected', reason)}>驳回</button></div></footer>
      <OutcomeForm key={`${brief.id}:${brief.outcome_tracking.updated_at}`} game={game} brief={brief} onSaved={onSaved}/>
    </div>
  </article>
}

export function DecisionsPage(props: { game: string; openIssue: (id: string) => void }) {
  return <DecisionContent key={props.game} {...props}/>
}

function DecisionContent({ game, openIssue }: { game: string; openIssue: (id: string) => void }) {
  const followup = useResource<DecisionComparison>(forGame('/api/chatbi/decisions/comparison', game))
  const resource = useResource<DecisionBriefResponse>(forGame('/api/chatbi/decisions', game))
  const [generated, setGenerated] = useState<DecisionBriefResponse>()
  const [generating, setGenerating] = useState(false)
  const [generationError, setGenerationError] = useState('')
  const [reviewing, setReviewing] = useState('')
  const [reviewReasons, setReviewReasons] = useState<Record<string, string>>({})
  const data = generated || resource.data
  async function generate() {
    setGenerating(true); setGenerationError('')
    try { setGenerated(await post<DecisionBriefResponse>('/api/chatbi/decisions/generate', { game })) }
    catch (error) { setGenerationError(error instanceof Error ? error.message : '生成失败') }
    finally { setGenerating(false) }
  }
  async function review(id: string, status: ReviewStatus, reason: string) {
    setReviewing(id); setGenerationError('')
    try { setGenerated(await post<DecisionBriefResponse>('/api/chatbi/decisions/review', { game, decision_id: id, status, reason })) }
    catch (error) { setGenerationError(error instanceof Error ? error.message : '评审保存失败') }
    finally { setReviewing('') }
  }
  if (resource.loading) return <Loading/>
  if (resource.error || !data) return <ErrorState message={resource.error || '决策建议加载失败'} retry={resource.reload}/>
  return <div className="decisions-page">
    <PageHeader eyebrow="DECISION BRIEFS" title="下一步行动" action={data.status === 'ready' ? <span className="decision-generated">{data.generated_at ? `${new Date(data.generated_at).toLocaleString('zh-CN')} 生成` : '已生成'}</span> : undefined}/>
    {data.status === 'not_generated' ? <section className="decision-empty"><div className="decision-empty-mark"><Compass/></div><span>当前快照尚未生成建议</span><button className="primary-action" disabled={generating} onClick={generate}>{generating ? <><LoaderCircle className="spin"/>正在生成…</> : <>生成三条行动建议<ArrowUpRight/></>}</button>{generationError && <div className="decision-error" role="alert"><CircleHelp/>{generationError}</div>}</section> : <>
      <div className="decision-intro"><span>{data.decisions.length} 条建议 · {data.decisions.filter(item => item.review.status !== 'pending').length} 条已判断</span></div>
      {generationError && <div className="decision-error" role="alert"><CircleHelp/>{generationError}</div>}
      <div className="decision-list">{data.decisions.map((brief, index) => <Brief key={brief.id} game={game} followup={followup} onSaved={setGenerated} brief={brief} index={index} openIssue={openIssue} reviewing={reviewing === brief.id} reason={reviewReasons[brief.id] ?? brief.review.reason} onReason={reason => setReviewReasons(current => ({ ...current, [brief.id]: reason }))} onReview={review}/>)}</div>
    </>}
  </div>
}

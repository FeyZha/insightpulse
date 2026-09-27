import { AlertCircle, ArrowUpRight, CheckCircle2, ChevronDown, LoaderCircle, MessageSquareQuote, ThumbsDown, ThumbsUp, X } from 'lucide-react'
import { useRef, type ReactNode } from 'react'
import { Citation } from '@/components/tool-ui/citation'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import type { Evidence, Issue, TranslationStatus } from '@/lib/domain'
import '@/pages/library.css'

export function PageHeader({ title, action, eyebrow, description }: { title: string; action?: ReactNode; eyebrow?: string; description?: string }) {
  return <header className="page-header"><div>{eyebrow && <span className="eyebrow">{eyebrow}</span>}<h1>{title}</h1>{description && <p>{description}</p>}</div>{action}</header>
}

export function Loading() { return <div className="state-box" role="status"><LoaderCircle className="spin"/><span>加载中…</span></div> }
export function ErrorState({ message, retry }: { message: string; retry?: () => void }) { return <div className="state-box error" role="alert"><AlertCircle/><span>{message}</span>{retry && <button onClick={retry}>重新加载</button>}</div> }
export function Empty({ children = '暂无符合条件的结果' }: { children?: ReactNode }) { return <div className="state-box"><MessageSquareQuote/><span>{children}</span></div> }

export function TranslatedQuote({ text, textZh, status }: { text: string; textZh?: string | null; status?: TranslationStatus }) {
  const translated = status === 'ready' && Boolean(textZh?.trim())
  const label = translated || status === 'source' ? '' : status === 'failed' ? '译文暂不可用' : '待翻译'
  return <div className="translated-quote">{label && <span className="translation-label translation-pending">{label}</span>}<blockquote lang={translated || status === 'source' ? 'zh-Hans' : undefined} dir="auto">{translated ? textZh : text}</blockquote>{translated && <details className="review-original"><summary>查看原文<ChevronDown aria-hidden="true"/></summary><blockquote dir="auto">{text}</blockquote></details>}</div>
}

export function EvidenceCard({ item, index }: { item: Evidence; index: number }) {
  const OpinionIcon = item.recommended === true ? ThumbsUp : item.recommended === false ? ThumbsDown : MessageSquareQuote
  return <article className="evidence-card">
    <div className="evidence-meta"><div className={`review-opinion ${item.recommended === true ? 'recommended' : item.recommended === false ? 'not-recommended' : ''}`}><OpinionIcon aria-hidden="true"/><span>{item.recommended === true ? '推荐' : item.recommended === false ? '不推荐' : '推荐状态未知'}</span></div><span className="review-language">{item.language}<span> / {String(index + 1).padStart(2, '0')}</span></span></div>
    <TranslatedQuote text={item.quote} textZh={item.quote_zh} status={item.translation_status}/>
    <div className="evidence-footer"><div><span>评测 #{item.review_id}</span>{item.created_at && <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleDateString('zh-CN')}</time>}</div><Citation id={`evidence-${item.review_id}-${index}`} href={item.url} title={`Steam 评测 ${item.review_id}`} snippet={item.translation_status === 'ready' && item.quote_zh?.trim() ? item.quote_zh : item.quote} domain="查看 Steam 原文 ↗" type="article" locale="zh-CN" variant="inline" /></div>
  </article>
}

function IssueDetailContent({ issue }: { issue: Issue }) {
  return <><DialogHeader><div className="detail-kicker"><span>{issue.category_label}</span></div><DialogTitle>{issue.title}</DialogTitle><DialogDescription>{issue.description}</DialogDescription></DialogHeader>
      <div className="detail-metrics"><div><strong>{issue.reviews.toLocaleString()}</strong><span>关联评论</span></div><div><strong>{issue.observations.toLocaleString()}</strong><span>反馈要点</span></div><div><strong>{issue.evidence_items.length}</strong><span>可追溯原话</span></div></div>
      <section className="detail-recommendation"><h3>反馈性质</h3>{issue.claim_review_counts ? <p>投诉 {issue.claim_review_counts.complaint} 条 · 明确恶化 {issue.claim_review_counts.worsened} 条 · 明确改善 {issue.claim_review_counts.improved} 条{issue.claim_review_counts.unverified > 0 && ' · 待核查 ' + issue.claim_review_counts.unverified + ' 条'}</p> : <p>关联 {issue.reviews} 条评论</p>}</section>
      {issue.evidence_capsule && <section className="detail-capsule"><div className="library-section-head"><div><span>Evidence Capsule</span><h3>证据摘要</h3></div><span>{issue.evidence_capsule.manifestations.length} 种具体表现</span></div><ol>{issue.evidence_capsule.manifestations.map((item, index) => {
        const representative = item.representatives[0]?.description
        const ids = new Set(item.observation_ids)
        const reviewIds = new Set<string>()
        const reviews = issue.evidence_items.filter(evidence => {
          if (!evidence.observation_id || !ids.has(evidence.observation_id) || reviewIds.has(evidence.review_id)) return false
          reviewIds.add(evidence.review_id)
          return true
        })
        return <li key={item.id}><details className="detail-manifestation"><summary><span>{String(index + 1).padStart(2, '0')}</span><div><strong>{item.label}</strong>{representative && representative !== item.label && <p>{representative}</p>}<small>{item.review_count} 条评论 · {item.observation_count} 个反馈要点</small></div><ChevronDown aria-hidden="true"/></summary><div className="manifestation-evidence"><div className="evidence-stack">{reviews.map((evidence, evidenceIndex) => <EvidenceCard key={evidence.observation_id || evidence.review_id} item={evidence} index={evidenceIndex}/>)}</div></div></details></li>
      })}</ol></section>}
    </>
}

export function IssueDetail({ issue, open, onOpenChange }: { issue?: Issue; open: boolean; onOpenChange: (open: boolean) => void }) {
  const returnFocus = useRef<HTMLElement | SVGElement | null>(null)
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="issue-dialog"
    onOpenAutoFocus={() => {
      const element = document.activeElement
      returnFocus.current = element instanceof HTMLElement || element instanceof SVGElement ? element : null
    }}
    onCloseAutoFocus={event => {
      if (returnFocus.current?.isConnected) {
        event.preventDefault()
        returnFocus.current.focus({ preventScroll: true })
      }
    }}>
    {issue && <IssueDetailContent key={issue.id} issue={issue}/>}
  </DialogContent></Dialog>
}

export function ConfigBadge({ configured, onClick }: { configured: boolean; onClick: () => void }) {
  return <button className={`config-badge ${configured ? 'ready' : ''}`} onClick={onClick}>{configured ? <CheckCircle2/> : <X/>}<span>{configured ? 'DeepSeek 已连接' : '配置 DeepSeek'}</span><ArrowUpRight/></button>
}

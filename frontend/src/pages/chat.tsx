import { createContext, useContext, useEffect, useRef, useState } from 'react'
import { AuiConfig, AssistantRuntimeProvider, Tools, type ChatModelAdapter, type Toolkit, type ThreadMessageLike, useLocalRuntime } from '@assistant-ui/react'
import { ArrowUpRight, ChevronDown, Focus } from 'lucide-react'
import { BrandMark } from '@/components/brand-mark'
import { Thread } from '@/components/thread.aui'
import { Chart } from '@/components/tool-ui/chart'
import { parseSerializableChart } from '@/components/tool-ui/chart/schema'
import { DataTable } from '@/components/tool-ui/data-table'
import { parseSerializableDataTable } from '@/components/tool-ui/data-table/schema'
import { StatsDisplay } from '@/components/tool-ui/stats-display'
import { parseSerializableStatsDisplay } from '@/components/tool-ui/stats-display/schema'
import { Citation } from '@/components/tool-ui/citation'
import { TranslatedQuote } from '@/components/product-ui'
import { parseSerializableCitation } from '@/components/tool-ui/citation/schema'
import { get, post } from '@/lib/api'
import { analysisContextFilters, contextQuestions } from '@/lib/chat-context'
import type { AnalysisContext } from '@/lib/domain'
import { parseAnalysis, type AnalysisResponse, type AnalysisFilters } from '@/lib/analysis'
import { AnalysisActions, AnalysisUI, RangePicker } from '@/components/analysis-ui'

const pending = () => <div className="tool-loading">正在核查数据…</div>
const invalid = () => <div className="tool-error">分析结果格式异常</div>
const StatsTool = ({ result }: { result?: unknown }) => { if (!result) return pending(); let props; try { props = parseSerializableStatsDisplay(result) } catch { return invalid() } return <StatsDisplay {...props}/> }
const ChartTool = ({ result }: { result?: unknown }) => { if (!result) return pending(); let props; try { props = parseSerializableChart(result) } catch { return invalid() } return <Chart {...props}/> }
const TableTool = ({ result }: { result?: unknown }) => { if (!result) return pending(); let props; try { props = parseSerializableDataTable(result) } catch { return invalid() } return <DataTable {...props}/> }
const CitationTool = ({ result }: { result?: unknown }) => { if (!result) return pending(); let props; try { props = parseSerializableCitation(result) } catch { return invalid() } const { snippet_zh, translation_status, ...citation } = props; return <div>{citation.snippet && <TranslatedQuote text={citation.snippet} textZh={snippet_zh} status={translation_status}/>}<Citation {...citation} snippet={undefined}/></div> }

const toolkit: Toolkit = {
  show_analysis: { type: 'backend', display: 'standalone', render: AnalysisUI },
  show_stats: { type: 'backend', display: 'standalone', render: StatsTool },
  show_chart: { type: 'backend', display: 'standalone', render: ChartTool },
  show_table: { type: 'backend', display: 'standalone', render: TableTool },
  show_citation: { type: 'backend', display: 'standalone', render: CitationTool },
}

const analysisContent = (response: AnalysisResponse) => [{ type: 'tool-call' as const, toolCallId: response.id, toolName: 'show_analysis', args: {}, argsText: '{}', result: response }]
const storageKey = (game: string) => `insightpulse-analysis-turn:${game}`

const ChatContext = createContext<{ context?: AnalysisContext; setQuestion: (question: string) => void } | undefined>(undefined)

function ChatWelcome() {
  const chat = useContext(ChatContext)
  const context = chat?.context
  const labels = [context?.kind === 'comparison' ? '前后变化' : '评论分布', '玩家原话', '证据核查']
  return <div className="chat-welcome">
    <div className="chat-welcome-symbol"><BrandMark/></div>
    <h2>评论分析</h2>
    <div className="chat-context-questions" role="group" aria-label="建议问题">
      {contextQuestions(context).map((question, index) => <button type="button" className="chat-context-question" key={question} onClick={() => chat?.setQuestion(question)}><span className="question-number">0{index + 1}</span><span>{labels[index]}</span><ArrowUpRight aria-hidden="true"/></button>)}
    </div>
  </div>
}

function ChatSession({ game, context, turns }: { game: string; context?: AnalysisContext; turns: AnalysisResponse[] }) {
  const [latest, setLatest] = useState(turns.at(-1))
  const override = useRef<AnalysisFilters | undefined>(undefined)
  const [appliedPage, setAppliedPage] = useState('')
  const adapter: ChatModelAdapter = {
    async *run({ messages, abortSignal }) {
      const question = messages.at(-1)?.content.filter(p => p.type === 'text').map(p => p.text).join('\n').trim()
      if (!question) throw new Error('请输入分析问题')
      const previousPart = messages.slice(0, -1).findLast(m => m.role === 'assistant')?.content.findLast(p => p.type === 'tool-call' && p.toolName === 'show_analysis' && p.result)
      const previous = previousPart?.type === 'tool-call' ? parseAnalysis(previousPart.result) : undefined
      const pageKey = context?.key || ''
      const changed = appliedPage !== pageKey
      const filters = override.current || (changed ? analysisContextFilters(context) : {})
      const pendingId = `pending-${Date.now()}`
      yield { content: [{ type: 'tool-call', toolCallId: pendingId, toolName: 'show_analysis', args: {}, argsText: '{}' }] }
      try {
        const raw = await post<unknown>('/api/analysis/ask', { game, question, parent_id: previous?.id || '', context: filters, replace_context: changed,
          ...(changed && context?.tracking?.savedId ? { saved_id: context.tracking.savedId } : {}) }, abortSignal)
        const response = parseAnalysis(raw)
        if (abortSignal.aborted) return
        setAppliedPage(pageKey); override.current = undefined
        setLatest(response)
        try { localStorage.setItem(storageKey(game), response.id) } catch { /* The in-memory conversation remains usable. */ }
        yield { content: analysisContent(response) }
      } catch (error) {
        if (abortSignal.aborted) return
        yield { content: [{ type: 'text', text: `分析未完成：${error instanceof Error ? error.message : '请求失败'}。可重试，或继续使用概览与变化追踪。` }] }
      }
    },
  }
  const initialMessages: ThreadMessageLike[] = turns.flatMap(t => [{ role: 'user', content: [{ type: 'text', text: t.question }] }, { role: 'assistant', content: analysisContent(t) }] as ThreadMessageLike[])
  const runtime = useLocalRuntime(adapter, { initialMessages })
  const pageChanged = appliedPage !== (context?.key || '')
  const activeFilters = pageChanged ? analysisContextFilters(context) : latest?.context.filters || analysisContextFilters(context)
  const apply = (filters: AnalysisFilters, question?: string) => {
    if (runtime.thread.getState().isRunning) return
    override.current = filters
    runtime.thread.append({ role: 'user', content: [{ type: 'text', text: question || latest?.analysis_question || '现在大家主要在抱怨什么？' }] })
  }
  return <div className="chat-page">
    <ChatContext.Provider value={{ context, setQuestion: question => runtime.thread.composer.setText(question) }}>
      <AnalysisActions.Provider value={apply}>
      <AssistantRuntimeProvider runtime={runtime} config={AuiConfig({ tools: Tools({ toolkit }) })}>
        <section className="chat-analysis-context" aria-label="当前分析范围">
          <div className="context-label"><Focus/><span>{context ? '当前选择' : '分析范围'}</span><span className="context-count">{context?.kind === 'comparison' ? '保存记录核查' : context ? `${context.issueIds.length} 个问题` : '当前快照'}</span></div>
          <h3 aria-live="polite">{pageChanged ? context?.label || '全部反馈' : latest?.analysis_question || context?.label || '全部反馈'}</h3>
          {context?.summary && <details className="context-details"><summary>范围说明<ChevronDown/></summary><p>{context.summary}</p></details>}
          <details className="chat-scope-controls"><summary>调整语言、时间与比较范围</summary><RangePicker key={`${latest?.id}:${context?.key}`} filters={activeFilters}/></details>
        </section>
        <Thread autoFocus={false} components={{ Welcome: ChatWelcome }} />
      </AssistantRuntimeProvider>
      </AnalysisActions.Provider>
    </ChatContext.Provider>
  </div>
}

export function ChatPage({ game, context }: { game: string; context?: AnalysisContext }) {
  const [turns, setTurns] = useState<AnalysisResponse[] | undefined>(() => localStorage.getItem(storageKey(game)) ? undefined : [])
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    const id = localStorage.getItem(storageKey(game))
    if (!id) return
    get<unknown[]>(`/api/analysis/turns/${encodeURIComponent(id)}`).then(rows => {
      if (active) setTurns(rows.map(parseAnalysis))
    }).catch(() => { if (active) { setError('历史对话暂不可用，可继续发起新分析。'); setTurns([]) } })
    return () => { active = false }
  }, [game])
  if (!turns) return pending()
  return <>{error && <p role="status">{error}</p>}<ChatSession game={game} context={context} turns={turns}/></>
}

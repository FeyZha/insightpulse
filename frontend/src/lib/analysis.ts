import { z } from 'zod'

const count = z.number().int().nonnegative()
const percent = z.number().min(0).max(100).nullable()
const timestamp = z.string().refine(v => Number.isFinite(Date.parse(v)), '日期无效')
const translation = z.enum(['ready', 'source', 'pending', 'failed'])
const metric = z.enum(['mentions', 'complaints', 'improved'])
const sourceGroup = z.object({ count, review_ids: z.array(z.string().min(1)) })
  .refine(g => g.count === g.review_ids.length && new Set(g.review_ids).size === g.count, '来源数量或去重不一致')
export const sourceBreakdownSchema = z.object({
  basis: z.literal('saved_snapshots'), language: z.string().min(1), before_id: z.string().min(1), after_id: z.string().min(1),
  before_at: timestamp, after_at: timestamp, before_total: count, after_total: count, summary: z.string(),
  groups: z.object({ shared: sourceGroup, first_seen: sourceGroup, new_review: sourceGroup, unknown_time: sourceGroup,
    not_seen: sourceGroup, scope_entered: sourceGroup, scope_left: sourceGroup, text_changed: sourceGroup, recommendation_changed: sourceGroup }).strict(),
}).superRefine((v, ctx) => {
  const g = v.groups
  const partition = [g.shared, g.first_seen, g.new_review, g.unknown_time, g.not_seen, g.scope_entered, g.scope_left].flatMap(g => g.review_ids)
  const shared = new Set(g.shared.review_ids)
  if (v.before_id === v.after_id || Date.parse(v.before_at) >= Date.parse(v.after_at) ||
      new Set(partition).size !== partition.length ||
      g.shared.count + g.not_seen.count + g.scope_left.count !== v.before_total ||
      g.shared.count + g.first_seen.count + g.new_review.count + g.unknown_time.count + g.scope_entered.count !== v.after_total ||
      [...g.text_changed.review_ids, ...g.recommendation_changed.review_ids].some(id => !shared.has(id))) {
    ctx.addIssue({ code: 'custom', message: '来源分组与样本不一致' })
  }
})
export type SourceBreakdown = z.infer<typeof sourceBreakdownSchema>
const snapshot = z.object({
  id: z.string(), label: z.string(), observed_at: timestamp, source: z.string(), coverage: z.string(),
  total: count, analyzed: count, analysis_coverage: percent.optional(), failed: count, observations: count, issues: count, recommended: count,
  negative: count, unknown: count, complaints: count, complaint_share: percent, pending: count, unassigned: count,
  started_at: z.string().nullable(), positive_share: percent, status: z.enum(['complete', 'partial']), analysis_version: z.string(),
}).refine(s => s.analyzed <= s.total, '分析分母超过样本规模')
const observation = z.object({
  observation_id: z.string(), issue_id: z.string().nullable().optional(), category: z.string(), object: z.string(),
  symptom: z.string(), description: z.string(), evidence: z.string(), evidence_zh: z.string().nullable(),
  claim: z.string(), change_evidence: z.string(), change_evidence_zh: z.string().nullable(),
  translation_status: translation, change_translation_status: translation,
})
const evidenceRow = z.object({
  review_id: z.string(), review_version_id: z.string(), snapshot_id: z.string(), observed_at: timestamp,
  text: z.string(), text_zh: z.string().nullable(), translation_status: translation, recommended: z.boolean().nullable(),
  created_at: timestamp, updated_at: timestamp, language: z.string(), observations: z.array(observation),
})
const evidence = z.record(z.enum(['before', 'after']), z.object({
  items: z.array(evidenceRow), total: count, offset: count,
  source_url: z.string().refine(v => !v || /^https?:\/\//.test(v)),
}).refine(v => v.total === v.items.length, '证据数量不一致').optional())
const commonIssue = { id: z.string(), title: z.string(), category: z.string(), evidence }
const issue = z.object({ ...commonIssue, count, share: percent, mentions: count, complaints: count, improved: count })
const comparisonIssue = z.object({ ...commonIssue, note: z.string().optional(), before_count: count, after_count: count, before_mentions: count, after_mentions: count,
  before_share: percent, after_share: percent, delta_pp: z.number().min(-100).max(100).nullable(),
  before_complaints: count, after_complaints: count, before_improved: count, after_improved: count,
})
const scope = z.record(z.string(), z.string()).and(z.object({ before: z.string(), after: z.string(),
  mode: z.enum(['snapshots', 'periods']), metric, language: z.string(), offset: z.string() }))
const event = z.object({ id: z.string(), name: z.string(), at: timestamp, precision: z.enum(['time', 'day']),
  url: z.string().refine(v => !v || /^https?:\/\//.test(v)) })
const base = { facts: z.record(z.string(), z.string().max(3000)) }
export const resultSchema = z.discriminatedUnion('kind', [
  z.object({ ...base, kind: z.literal('stats'), stats: z.array(snapshot) }),
  z.object({ ...base, kind: z.literal('issues'), items: z.array(issue), denominator: count, metric }),
  z.object({ ...base, kind: z.literal('evidence'), items: z.array(issue), denominator: count, metric }),
  z.object({ ...base, kind: z.literal('comparison'), items: z.array(comparisonIssue), before: snapshot, after: snapshot,
    scope, comparable: z.boolean(), status: z.enum(['ready', 'sample', 'observing', 'unavailable']), reasons: z.array(z.string()), source_breakdown: sourceBreakdownSchema.optional() }),
  z.object({ ...base, kind: z.literal('trend'), points: z.array(z.object({ at: timestamp, end: timestamp.optional(),
    side: z.string().optional(), snapshot_id: z.string().optional(), total: count, analyzed: count, count: count.nullable(),
    share: percent, value: z.number().nullable(), complete: z.boolean(), small: z.boolean() })), events: z.array(event),
    reasons: z.array(z.string()), granularity: z.enum(['snapshot', 'day', 'week']), measure: z.enum(['share', 'count']),
    issue_id: z.string().nullable(), issues: z.array(z.object({ id: z.string(), title: z.string() })) }),
  z.object({ ...base, kind: z.literal('revision'), review_id: z.string(), history: z.array(z.object({ snapshot,
    created_at: timestamp.nullable().optional(), updated_at: timestamp.nullable().optional(), review_version_id: z.string().nullable().optional(),
    text: z.string().nullable(), text_zh: z.string().nullable(), translation_status: translation, recommended: z.boolean().nullable(),
    status: z.enum(['analyzed', 'pending', 'not_seen']), observations: z.array(observation) })) }),
  z.object({ ...base, kind: z.literal('range') }),
])
export const blockSchema = z.object({ type: z.enum(['stats', 'issues', 'comparison', 'trend', 'evidence', 'revision', 'range']),
  data_ref: z.string(), limit: z.number().int().min(1).max(10).optional(), sort: z.enum(['default', 'count', 'change']).optional(),
}).strict()
const reference = z.object({ data_ref: z.string(), fact_id: z.string() }).strict()
const responseSchema = z.object({ schema_version: z.literal('1.0'), id: z.string().regex(/^[a-f0-9]{32}$/),
  parent_id: z.string().optional(), mode: z.enum(['model', 'fallback']), question: z.string(), analysis_question: z.string(),
  context: z.object({ product: z.string(), filters: z.record(z.string(), z.union([z.string(), z.array(z.string())])),
    topics: z.array(z.string()).default([]), data_version: z.string(), generated_at: timestamp, updated_at: timestamp.nullable(), range_start: timestamp.nullable(),
    range_end: timestamp.nullable(), samples: z.array(snapshot) }),
  summary: z.object({ text: z.string(), refs: z.array(reference).min(1).max(3) }),
  blocks: z.array(blockSchema).max(3), results: z.record(z.string(), z.unknown()), warnings: z.array(z.string()),
})
export type AnalysisResponse = z.infer<typeof responseSchema>
export type AnalysisResult = z.infer<typeof resultSchema>
export type AnalysisBlock = z.infer<typeof blockSchema>
export type FrozenEvidence = z.infer<typeof evidenceRow>
export type AnalysisFilters = AnalysisResponse['context']['filters']

export function parseAnalysis(input: unknown): AnalysisResponse {
  const response = responseSchema.parse(input)
  // Facts used by text are validated too. Each component's full data is checked separately so one failure is contained.
  const text = response.summary.refs.map(ref => {
    const data = resultSchema.parse(response.results[ref.data_ref])
    if (!Object.hasOwn(data.facts, ref.fact_id)) throw new Error('摘要事实引用无效')
    return data.facts[ref.fact_id]
  }).join(' ')
  if (text !== response.summary.text) throw new Error('摘要与可核查事实不一致')
  return response
}

export function parseBlock(block: AnalysisBlock, response: AnalysisResponse) {
  blockSchema.parse(block)
  const result = resultSchema.parse(response.results[block.data_ref])
  if (result.kind !== block.type) throw new Error('组件与数据引用不一致')
  return result
}

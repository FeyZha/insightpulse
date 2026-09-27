import type { AnalysisContext } from './domain'
import type { AnalysisFilters } from './analysis'

export function analysisContextFilters(context?: AnalysisContext): AnalysisFilters {
  if (context?.tracking) {
    const { analysis_version: _version, ...scope } = context.tracking.scope
    return { ...scope, ...(context.issueIds.length || context.filtered ? { issue_ids: context.issueIds } : {}) }
  }
  return { mode: 'current', ...(context ? { issue_ids: context.issueIds } : {}) }
}

export function scopedQuestion(input: string, context?: AnalysisContext) {
  const question = input.trim()
  if ([...question].length < 2) throw new Error('请输入至少 2 字的分析问题')
  const scope = context
    ? `\n当前分析：${{ issue: '问题', category: '类别', comparison: '前后对比' }[context.kind]}「${[...context.label].slice(0, 60).join('')}」；共 ${context.issueIds.length} 个 Issue${context.issueIds.length ? `，${context.issueIds.length > 3 ? '代表 ' : ''}Issue：${context.issueIds.slice(0, 3).join('、')}` : ''}。请依据此范围内的评论证据回答。`
    : ''
  const budget = 300 - [...scope].length
  if ([...question].length > budget) throw new Error(`问题过长，最多可输入 ${Math.max(0, budget)} 字。`)
  return question + scope
}

export function contextQuestions(context?: AnalysisContext) {
  if (context?.kind === 'comparison') return ['当前范围的前后反馈变化了多少？', '请提供当前比较的前后原话证据。', '当前比较有哪些证据不足或数据限制？']
  const subject = context ? `「${context.label}」` : '当前反馈'
  return [
    context?.kind === 'issue' ? `${subject}的关联评论数及类别内占比分别是多少？` : `${subject}的主要问题及关联评论数分别是多少？`,
    `请提供${subject}的代表性原话及场景。`,
    `请核查${subject}的结论依据，列出证据不足的结论。`,
  ]
}

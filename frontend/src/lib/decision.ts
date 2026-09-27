import type { Category, Issue } from './domain.ts'

export type Selection = { kind: 'issue' | 'category'; id: string }
export const percent = (value: number, total: number) => total > 0 ? Math.round(value / total * 100) : 0
export const uniqueReviews = (issues: Issue[]) => new Set(issues.flatMap(issue => issue.review_ids)).size
export const selectedIssues = (issues: Issue[], selection: Selection) => issues.filter(issue => selection.kind === 'issue' ? issue.id === selection.id : issue.category === selection.id)
export const concentration = (issue: Issue, categories: Category[]) => {
  const total = categories.find(category => category.key === issue.category)?.observations || 0
  return total > 0 ? issue.observations / total : 0
}

const issueSegmenter = new Intl.Segmenter('zh-CN', { granularity: 'word' })
const clusterStopWords = new Set(['游戏', '玩家', '问题', '存在', '导致', '无法', '可以', '需要', '一个', '这个', '进行', '出现', '正常', '内容', '系统', '功能', '以及', '或者', '没有', '重新', '提示', '不知', '如何', '难以', '缺乏', '飞船'])
const issueTerms = (issue: Issue) => new Set([...issueSegmenter.segment(`${issue.title} ${issue.description ?? ''}`)]
  .filter(part => part.isWordLike).map(part => part.segment.toLocaleLowerCase()).filter(term => term.length > 1 && !clusterStopWords.has(term)))

export function clusterIssues(issues: Issue[], threshold = 0.08): Issue[][] {
  const terms = issues.map(issueTerms)
  const groups: { seed: number; issues: Issue[] }[] = []
  issues.forEach((issue, index) => {
    const match = groups.find(group => {
      const shared = [...terms[index]].filter(term => terms[group.seed].has(term)).length
      return shared > 0 && 2 * shared / (terms[index].size + terms[group.seed].size) >= threshold
    })
    if (match) match.issues.push(issue)
    else groups.push({ seed: index, issues: [issue] })
  })
  return groups.map(group => group.issues).sort((a, b) => b.reduce((sum, issue) => sum + issue.reviews, 0) - a.reduce((sum, issue) => sum + issue.reviews, 0))
}

export type CategoryIntersection = { key: string; categories: Category[]; reviewIds: string[] }

export function categoryIntersections(issues: Issue[], categories: Category[]): CategoryIntersection[] {
  const knownCategories = new Set(categories.map(category => category.key))
  const reviews = new Map<string, Set<string>>()
  for (const issue of issues) {
    if (!knownCategories.has(issue.category)) continue
    for (const id of issue.review_ids) {
      if (!reviews.has(id)) reviews.set(id, new Set())
      reviews.get(id)!.add(issue.category)
    }
  }
  const intersections = new Map<string, CategoryIntersection>()
  for (const [id, membership] of reviews) {
    const key = JSON.stringify([...membership].sort())
    if (!intersections.has(key)) intersections.set(key, { key, categories: categories.filter(category => membership.has(category.key)), reviewIds: [] })
    intersections.get(key)!.reviewIds.push(id)
  }
  for (const intersection of intersections.values()) intersection.reviewIds.sort()
  return [...intersections.values()].sort((a, b) => b.reviewIds.length - a.reviewIds.length || b.categories.length - a.categories.length || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0))
}

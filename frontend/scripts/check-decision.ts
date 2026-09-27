import assert from 'node:assert/strict'
import { categoryIntersections, clusterIssues, concentration, selectedIssues, uniqueReviews } from '../src/lib/decision.ts'
import type { Issue } from '../src/lib/domain.ts'

const issue = { id: 'a', title: '运行崩溃', category: 'stability', reviews: 1, observations: 2, review_ids: ['r1'], claim_counts: { complaint: 1 } } as Issue
const other = { ...issue, id: 'b', category: 'content_narrative', reviews: 3, review_ids: ['r1', 'r2', 'r3'] }
assert.equal(uniqueReviews([issue, other]), 3, 'Shared reviews must not be added twice')
assert.equal(concentration(issue, [{ key: 'stability', label: '稳定性', observations: 4, issues: 2 }]), 0.5)
assert.equal(concentration(issue, []), 0)
assert.deepEqual(selectedIssues([issue, other], { kind: 'category', id: 'stability' }), [issue])
assert.deepEqual(selectedIssues([issue, other], { kind: 'issue', id: 'b' }), [other])
assert.equal(uniqueReviews([]), 0)

const categories = ['a', 'b', 'c', 'empty'].map(key => ({ key, label: key, observations: 0, issues: 0 }))
const overlapping = [
  { ...issue, category: 'a', review_ids: ['r1', 'r1', 'r2'] },
  { ...issue, id: 'another-a', category: 'a', review_ids: ['r2', 'r3'] },
  { ...issue, category: 'b', review_ids: ['r2', 'r3', 'r4'] },
  { ...issue, category: 'c', review_ids: ['r2'] },
  { ...issue, category: 'unknown', review_ids: ['r1', 'r2', 'r3', 'r4', 'unknown-only'] },
]
const intersections = categoryIntersections(overlapping, categories)
assert.deepEqual(intersections.map(group => [group.categories.map(category => category.key), group.reviewIds]), [
  [['a', 'b', 'c'], ['r2']], [['a', 'b'], ['r3']], [['a'], ['r1']], [['b'], ['r4']],
], 'Each review belongs to one exact combination; three-way reviews must not inflate the two-way group')
assert.equal(intersections.flatMap(group => group.reviewIds).length, 4, 'Duplicates and unknown categories must not inflate the total')
assert.equal(new Set(intersections.flatMap(group => group.reviewIds)).size, 4, 'No review can appear in two combinations')
assert.deepEqual(categoryIntersections([], categories), [], 'Empty categories must not create empty combinations')
assert.deepEqual(categoryIntersections(overlapping, []), [])
assert.deepEqual(categoryIntersections(overlapping, categories.slice(0, 1)), [{ key: '["a"]', categories: [categories[0]], reviewIds: ['r1', 'r2', 'r3'] }])
const reordered = categoryIntersections([...overlapping].reverse().map(item => ({ ...item, review_ids: [...item.review_ids].reverse() })), [...categories].reverse())
assert.deepEqual(reordered.map(({ key, reviewIds }) => ({ key, reviewIds })), intersections.map(({ key, reviewIds }) => ({ key, reviewIds })), 'Keys, counts and ordering must survive input reordering')
assert.deepEqual(reordered[0].categories.map(category => category.key), ['c', 'b', 'a'], 'Category rows follow the supplied display order')
const ranked = categoryIntersections([...overlapping, { ...issue, category: 'a', review_ids: ['r6', 'r5'] }], categories)
assert.deepEqual(ranked[0].reviewIds, ['r1', 'r5', 'r6'], 'Review count takes precedence over degree, and review IDs are stably sorted')
assert.deepEqual(categoryIntersections([{ ...issue, category: 'a', review_ids: ['r1'] }, { ...issue, category: 'b', review_ids: ['r2'] }], categories).map(group => group.key), ['["a"]', '["b"]'], 'Disjoint categories remain separate singleton combinations')
const clustered = clusterIssues([
  { ...issue, id: 'tutorial-a', title: '缺少教程', description: '没有教程或操作指引', reviews: 3 },
  { ...issue, id: 'tutorial-b', title: '教程无法工作', description: '教程内容无用', reviews: 2 },
  { ...issue, id: 'save', title: '存档损坏', description: '读取存档时丢失进度' },
])
assert.deepEqual(clustered.map(group => group.map(item => item.id)), [['tutorial-a', 'tutorial-b'], ['save']], 'Related issues cluster without merging their records')
assert.equal(clustered.flat().length, 3, 'Clustering must preserve every issue')
console.log('Decision checks passed: deduplication, concentration, selection, clustering and exact category intersections.')

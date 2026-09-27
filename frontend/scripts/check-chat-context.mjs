import assert from 'node:assert/strict'
import { contextQuestions, scopedQuestion } from '../src/lib/chat-context.ts'

const issue = { key: 'issue:a', kind: 'issue', label: '手柄操作失灵', issueIds: ['a'], summary: '' }
const category = { key: 'category:controls', kind: 'category', label: '操作与输入', issueIds: ['a', 'b', 'c', 'd'], summary: '' }
assert.equal(scopedQuestion('  先查什么？  '), '先查什么？')
assert.match(scopedQuestion('先查什么？', issue), /问题「手柄操作失灵」.*Issue：a/)
assert.match(scopedQuestion('先查什么？', category), /类别「操作与输入」.*共 4 个 Issue，代表 Issue：a、b、c/)
assert.ok(!scopedQuestion('先查什么？', category).includes('、d'))
const budget = 300 - [...scopedQuestion('测试', category)].length + 2
assert.equal([...scopedQuestion('问'.repeat(budget), category)].length, 300)
assert.throws(() => scopedQuestion('问'.repeat(budget + 1), category), /问题过长/)
assert.equal([...scopedQuestion('🎮'.repeat(300))].length, 300)
assert.throws(() => scopedQuestion('问'.repeat(301)), /问题过长/)
assert.throws(() => scopedQuestion('问', issue), /至少 2 字/)
assert.throws(() => scopedQuestion('  ', issue), /至少 2 字/)
assert.ok(contextQuestions(issue).every(question => question.includes(issue.label)))
assert.ok(contextQuestions(category).every(question => question.includes(category.label)))
assert.notDeepEqual(contextQuestions(issue), contextQuestions(category))
console.log('Chat context: scope, representative IDs, Unicode and 300-character boundary passed.')

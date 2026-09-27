import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdir, writeFile, unlink } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'
import { resolve } from 'node:path'
import { build } from 'vite'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { parseAnalysis, parseBlock, sourceBreakdownSchema } from '../src/lib/analysis.ts'
import { analysisContextFilters } from '../src/lib/chat-context.ts'

const replay = spawnSync('python', ['-c', 'import json; from scripts.test_analysis_ui import fixture; print(json.dumps(fixture()))'], { cwd: '..', encoding: 'utf8' })
assert.equal(replay.status, 0, replay.stderr)
const response = parseAnalysis(JSON.parse(replay.stdout))
for (const block of response.blocks) parseBlock(block, response)
const sources = response.results.r0.source_breakdown
assert.deepEqual([sources.before_total, sources.after_total], [100, 200])
for (const mutate of [
  v => { v.groups.shared.count++ },
  v => { v.groups.new_review.review_ids[0] = v.groups.shared.review_ids[0] },
  v => { v.groups.text_changed.review_ids[0] = v.groups.new_review.review_ids[0] },
  v => { v.after_total++ },
  v => { v.before_at = v.after_at },
]) {
  const invalid = structuredClone(sources)
  mutate(invalid)
  assert.equal(sourceBreakdownSchema.safeParse(invalid).success, false)
}
assert.match(response.summary.text, /30% → 10%/)
const bad = structuredClone(response)
bad.summary.text = '版本已经修复'
assert.throws(() => parseAnalysis(bad))
assert.throws(() => parseAnalysis({ ...response, schema_version: '2.0' }))
assert.throws(() => parseBlock({ type: 'html', data_ref: 'r0' }, response))
assert.throws(() => parseBlock({ type: 'comparison', data_ref: 'forged' }, response))
assert.throws(() => parseBlock({ type: 'comparison', data_ref: 'r0', limit: 100 }, response))
assert.deepEqual(analysisContextFilters({ tracking: { scope: { before: 'a', after: 'c', mode: 'snapshots', language: 'chinese', analysis_version: 'old' } }, issueIds: ['issue'] }), { before: 'a', after: 'c', mode: 'snapshots', language: 'chinese', issue_ids: ['issue'] })

// Exercise real React components via the installed Vite compiler; no new test framework.
const bundle = await build({ logLevel: 'silent', build: { ssr: 'src/components/analysis-ui.tsx', write: false, minify: false } })
const output = bundle.output.find(f => f.type === 'chunk' && f.isEntry)
const path = resolve('node_modules/.cache/analysis-render-check.mjs')
await mkdir(resolve('node_modules/.cache'), { recursive: true })
await writeFile(path, output.code)
try {
  const { AnalysisUI } = await import(pathToFileURL(path).href)
  const html = renderToStaticMarkup(createElement(AnalysisUI, { result: response }))
  assert.ok(html.includes('回答数据范围') && html.includes('下降20个百分点') && html.includes('查看原话'))
  for (const text of ['共同评论', '补采旧评论', '期间新评论', '保存文本不同']) assert.ok(html.includes(text), text)
  const broken = structuredClone(response)
  broken.results.r1.points = 'invalid'
  const partial = renderToStaticMarkup(createElement(AnalysisUI, { result: broken }))
  assert.ok(partial.includes('此模块未通过校验') && partial.includes('下降20个百分点'))
  assert.ok(renderToStaticMarkup(createElement(AnalysisUI, { result: bad })).includes('分析响应未通过校验'))
  assert.ok(renderToStaticMarkup(createElement(AnalysisUI)).includes('正在查询'))
  const untrusted = structuredClone(response)
  untrusted.results.r0.items[0].title = '<script>bad()</script>'
  const escaped = renderToStaticMarkup(createElement(AnalysisUI, { result: untrusted }))
  assert.ok(escaped.includes('&lt;script&gt;bad()&lt;/script&gt;') && !escaped.includes('<script>bad()'))
} finally { await unlink(path) }
console.log('Analysis checks passed: contracts, references, summary, context and real React rendering (simulated model).')

param([string]$BaseUrl = 'http://127.0.0.1:8765')
$ErrorActionPreference = 'Stop'
function Browser {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$BrowserArgs)
    & npx --yes agent-browser --session insightpulse-check @BrowserArgs
    if ($LASTEXITCODE -ne 0) { throw 'Browser command failed' }
}
function Check([string]$Code) {
    $EncodedCode = ConvertTo-Json -InputObject $Code -Compress
    "(() => eval($EncodedCode))()" | & npx --yes agent-browser --session insightpulse-check eval --stdin
    if ($LASTEXITCODE -ne 0) { throw 'Browser assertion failed' }
}
$Runtime = Join-Path (Split-Path (Split-Path $PSScriptRoot)) 'runtime'
New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
try {
    Browser set viewport 1440 900
    Browser network requests --clear
    Browser open $BaseUrl
    Browser wait '.neutral-category-row'
    Check @'
(async () => {
  window.__assert = (ok, label) => { if (!ok) throw new Error(label) };
  const { data } = await (await fetch('/api/chatbi/overview')).json();
  const g = window.__global = data.global;
  const memberships = new Map();
  for (const issue of g.issues) for (const id of new Set(issue.review_ids)) {
    if (!memberships.has(id)) memberships.set(id, new Set());
    memberships.get(id).add(issue.category);
  }
  const rows = [...document.querySelectorAll('.neutral-category-row')];
  __assert(rows.length === 13 && rows.length === g.categories.length, 'All 13 categories remain visible');
  rows.forEach((row, index) => {
    const category = g.categories[index];
    const count = [...memberships.values()].filter(keys => keys.has(category.key)).length;
    __assert(row.dataset.category === category.key && Number(row.dataset.count) === count && count === category.reviews, 'Category counts match distinct reviews and API');
    __assert(Math.abs(Number(row.dataset.share) - count / g.scope.analyzed) < 1e-9, 'Category shares use analyzed comments');
    __assert(row.querySelector('.category-name').textContent === category.label && row.querySelector('strong').textContent === count.toLocaleString('zh-CN'), 'Category labels and visible counts match API');
  });
  __assert(Number(document.querySelector('[data-metric=collected]').dataset.count) === g.scope.collected, 'Visible sample size matches API');
  __assert(g.recommendation.recommended + g.recommendation.not_recommended + g.recommendation.unknown === g.scope.collected, 'All recommendation states partition the sample');
  for (const [key, field] of [['recommended', 'recommended'], ['not-recommended', 'not_recommended'], ['unknown', 'unknown']]) {
    const count = g.recommendation[field], share = count / g.scope.collected;
    const segment = document.querySelector(`[data-recommendation=${key}]`);
    __assert(Number(segment.dataset.count) === count && Math.abs(parseFloat(segment.style.width) - share * 100) < 1e-4, 'All three bar segments use collected comments as denominator');
    __assert(document.querySelector(`.recommendation-readout .${key} strong`).textContent === `${(share * 100).toFixed(1)}%`, 'Visible recommendation percentage uses the same denominator');
  }
  __assert(g.timeline.dated + g.timeline.undated === g.scope.collected, 'Dated and undated comments partition the sample');
  for (const period of ['years', 'months']) __assert(g.timeline[period].reduce((sum, row) => sum + row.reviews, 0) === g.timeline.dated, 'Time buckets count every dated comment once');
  window.__checkHome = () => {
    __assert(!document.querySelector('.neutral-overview .evidence-card, .neutral-overview .priority-matrix, .neutral-overview .category-overlap, .neutral-overview [data-issue-id], .global-statements, .global-issue-table'), 'Home has no issue details, quotations, generated summary, or matrix');
    __assert(!document.querySelector('[role=dialog]'), 'Home does not open a detail by default');
    __assert(!document.querySelector('.overview-data-notes').open, 'Data details start collapsed');
    __assert(document.documentElement.scrollWidth <= innerWidth, `No page overflow at ${innerWidth}px`);
  };
  window.__checkTimeline = period => {
    __assert(document.querySelector('.overview-time-chart').dataset.period === period, 'Time granularity changes');
    __assert(document.querySelectorAll('.overview-time-chart .recharts-bar-rectangle').length === g.timeline[period].filter(row => row.reviews > 0).length, 'Time chart renders every nonzero bucket');
  };
  __checkHome();
  __checkTimeline('years');
  window.__chosenCategory = g.categories.find(category => category.reviews > 0);
  window.__chosenIssues = g.issues.filter(issue => issue.category === __chosenCategory.key);
  return 'PASS: all categories, honest denominators, complete time buckets, neutral desktop overview';
})()
'@
    Browser screenshot (Join-Path $Runtime 'neutral-overview-desktop.png')
    Browser find role button click --name '按月' --exact
    Browser wait '.overview-time-chart[data-period=months]'
    Check '__checkTimeline("months"); "PASS: monthly chart"'
    Browser find role button click --name '按年' --exact
    Check '__checkTimeline("years"); document.querySelector(`[data-category="${__chosenCategory.key}"]`).click(); "category opened"'
    Browser wait '.overview-category-issues'
    Check @'
(() => {
  const dialog = document.querySelector('.overview-detail-dialog');
  const ids = [...dialog.querySelectorAll('[data-issue-id]')].map(node => node.dataset.issueId);
  __assert(JSON.stringify(ids) === JSON.stringify(__chosenIssues.map(issue => issue.id)), 'Category dialog contains exactly its issues in API order');
  __assert(dialog.querySelector('[data-slot=dialog-title]').textContent === __chosenCategory.label, 'Category dialog title matches selection');
  __assert(!dialog.querySelector('.evidence-card'), 'Category drilldown does not immediately show quotations');
  return 'PASS: category-scoped drilldown';
})()
'@
    Browser click '.overview-category-issues > button:first-child'
    Browser wait '.issue-dialog .evidence-card'
    Check @'
(async () => {
  const { data } = await (await fetch(`/api/chatbi/issues/${encodeURIComponent(__chosenIssues[0].id)}`)).json();
  const cards = [...document.querySelectorAll('.issue-dialog .evidence-card')];
  const seen = new Set();
  const expected = data.evidence_items.filter(item => { if (seen.has(item.review_id)) return false; seen.add(item.review_id); return true }).slice(0, 3);
  __assert(document.querySelector('.issue-dialog [data-slot=dialog-title]').textContent === data.title, 'Issue drilldown opens the chosen issue');
  __assert(cards.length === expected.length, 'Evidence previews distinct reviews');
  cards.forEach((card, index) => {
    const item = expected[index], quote = item.translation_status === 'ready' && item.quote_zh?.trim() ? item.quote_zh : item.quote;
    __assert(card.querySelector('.translated-quote > blockquote').textContent === quote, 'Saved quotation is preserved');
    __assert(card.querySelector('.evidence-footer > div > span').textContent === `评测 #${item.review_id}`, 'Quotation belongs to the chosen issue');
  });
  return 'PASS: issue to saved quotations';
})()
'@
    Browser press Escape
    Check 'document.querySelector(`[data-category="${__chosenCategory.key}"]`).click(); "category reopened"'
    Browser find role button click --name '分析此类别' --exact
    Browser wait '.chat-analysis-context'
    Check '__assert(document.querySelector(".chat-analysis-context h3").textContent === __chosenCategory.label && document.querySelector(".context-count").textContent === `${__chosenIssues.length} 个问题`, "Assistant receives exact category context without sending a message"); "PASS: assistant context"'
    Check '__assert(new URLSearchParams(location.search).get("page") === "ask" && document.querySelector(".page-content").hidden && !document.querySelector(".chat-dock"), "Assistant opens as a dedicated page"); "PASS: dedicated assistant page"'
    Browser click '.assistant-return'
    Browser find role button click --name '深入分析' --exact
    Browser wait '.overview-analysis-dialog .priority-matrix'
    Check @'
(() => {
  __assert(document.querySelectorAll('.upset-category').length === 13, 'Advanced combinations keep all categories');
  __assert(document.querySelectorAll('.upset-column').length > 0, 'Advanced combinations are accessible');
  __assert(document.querySelector('[aria-label=选择矩阵中的问题]').options.length === __global.issues.length + 1, 'Advanced matrix contains all issues');
  __assert(!document.querySelector('.priority-matrix .matrix-point.selected'), 'No matrix issue is preselected');
  document.querySelector('.matrix-point').focus();
  return 'PASS: advanced combinations and matrix';
})()
'@
    Browser press Enter
    Check '__assert(document.querySelector("[aria-label=选择矩阵中的问题]").value && [...document.querySelectorAll(".overview-analysis-dialog .text-action")].some(node => node.textContent.startsWith("查看问题：")), "Matrix keyboard selection exposes issue drilldown"); "PASS: matrix keyboard navigation"'
    Browser press Escape
    foreach ($Width in @(390, 320)) {
        Browser set viewport $Width 844
        Check '__checkHome(); __assert(document.querySelectorAll(".neutral-category-row").length === 13, "Mobile keeps all 13 categories"); `PASS: mobile ${innerWidth}px`'
        if ($Width -eq 390) { Browser screenshot (Join-Path $Runtime 'neutral-overview-mobile.png') --full }
    }
    Browser find role button click --name '深入分析' --exact
    Browser wait '.overview-analysis-dialog .priority-matrix'
    Check '__assert(document.documentElement.scrollWidth <= innerWidth && document.querySelector(".overview-analysis-dialog").getBoundingClientRect().right <= innerWidth, "Mobile advanced dialog stays inside viewport"); "PASS: mobile advanced dialog"'
    Browser press Escape

    Browser set viewport 1440 900
    Check '[...document.querySelectorAll(".global-nav button")].find(button => button.textContent === "问题目录").click(); "issues opened"'
    Browser wait '.issue-map-tile'
    Check @'
(() => {
  window.__checkIssueDistribution = expected => {
    const rows = [...document.querySelectorAll('.issue-map-tile')];
    const keys = [...new Set(expected.map(issue => issue.category))];
    __assert(rows.length === keys.length && rows.reduce((sum, row) => sum + Number(row.dataset.count), 0) === expected.length, 'Every issue is counted in a category, without truncation');
    const totalArea = rows.reduce((sum, row) => sum + row.getBoundingClientRect().width * row.getBoundingClientRect().height, 0);
    rows.forEach(row => {
      const members = expected.filter(issue => issue.category === row.dataset.category);
      const rect = row.getBoundingClientRect(), stage = row.parentElement.getBoundingClientRect();
      __assert(members.length > 0 && Number(row.dataset.count) === members.length && row.querySelector('span').textContent === members[0].category_label, 'Category names and issue counts match saved data');
      __assert(Math.abs(rect.width * rect.height / totalArea - members.length / expected.length) < .001, 'Rectangle area represents issue count');
      __assert(rect.width > 0 && rect.height > 0 && rect.right <= stage.right + .1 && rect.bottom <= stage.bottom + .1, 'Every category has a visible rectangle inside the chart');
    });
    if (expected.length) __assert(document.querySelectorAll('.issue-map-legend button').length === __global.categories.length, 'Legend keeps every category, including zero matches');
    __assert(!document.querySelector('.issues-page table, .issues-page .priority, .issues-page .catalog-status') && !/\bP[0-3]\b|待运营确认/.test(document.querySelector('.issues-page').textContent), 'Distribution has no table, priority tiers or assumed workflow status');
    __assert(document.documentElement.scrollWidth <= innerWidth, `Map stays inside ${innerWidth}px viewport`);
  };
  __checkIssueDistribution(__global.issues);
  __assert(document.querySelector('[aria-label="问题类别"]').options.length === __global.categories.length + 1, 'Every category remains selectable');
  window.__mapIssues = __global.issues.filter(issue => issue.category === document.querySelector('.issue-map-tile').dataset.category);
  return 'PASS: complete category map, truthful areas and no invented rules';
})()
'@
    Browser screenshot (Join-Path $Runtime 'issue-map-desktop.png')
    Check 'const select = document.querySelector("[aria-label=问题类别]"); select.value = __chosenCategory.key; select.dispatchEvent(new Event("change", { bubbles: true })); "category selected"'
    Check '__checkIssueDistribution(__chosenIssues); const input = document.querySelector("[aria-label=搜索问题]"); window.__issueSearch = __chosenIssues[0].title; Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, __issueSearch); input.dispatchEvent(new Event("input", { bubbles: true })); "search applied"'
    Check '__checkIssueDistribution(__chosenIssues.filter(issue => `${issue.title} ${issue.description}`.toLocaleLowerCase().includes(__issueSearch.toLocaleLowerCase()))); "PASS: category and search intersection"'
    Browser fill '[aria-label=搜索问题]' '__no_matching_issue_8f93c__'
    Browser wait '.issue-chart-panel .state-box'
    Check '__checkIssueDistribution([]); __assert(document.querySelector(".issue-chart-panel .state-box").textContent.includes("暂无匹配问题"), "Unmatched search shows empty state"); "PASS: issue search empty state"'
    Browser find role button click --name '清除筛选' --exact
    Check '__checkIssueDistribution(__global.issues); __assert(!document.querySelector("[aria-label=搜索问题]").value && !document.querySelector("[aria-label=问题类别]").value, "Clear resets both filters"); document.querySelector(".issue-map-tile").focus(); "PASS: clearing filters restores complete map"'
    Browser press Enter
    Browser wait '.issue-category-dialog [data-issue-id]'
    Check 'const members = [...document.querySelectorAll(".issue-category-dialog [data-issue-id]")]; __assert(JSON.stringify(members.map(button => button.dataset.issueId)) === JSON.stringify(__mapIssues.map(issue => issue.id)), "Category drilldown includes every member in API order without truncation"); __assert(!document.querySelector(".issue-dialog:not(.issue-category-dialog)"), "Category opens its member list before evidence"); members[0].focus(); "PASS: complete category drilldown"'
    Browser press Enter
    Browser wait '.issue-dialog .evidence-card'
    Check @'
(async () => {
  const { data } = await (await fetch(`/api/chatbi/issues/${encodeURIComponent(__mapIssues[0].id)}`)).json();
  const item = data.evidence_items[0], quote = item.translation_status === 'ready' && item.quote_zh?.trim() ? item.quote_zh : item.quote;
  __assert(!document.querySelector('.issue-category-dialog') && document.querySelector('.issue-dialog [data-slot=dialog-title]').textContent === data.title && document.querySelector('.issue-dialog .translated-quote > blockquote').textContent === quote, 'Keyboard selection closes the category and opens the member saved quotations');
  __assert(!document.querySelector('.issue-dialog .priority, .issue-dialog .catalog-status') && !/待运营确认|反复提及|少量提及/.test(document.querySelector('.detail-kicker').textContent), 'Detail has no removed classification rules');
  return 'PASS: distribution to group to real evidence';
})()
'@
    Browser press Escape
    foreach ($Width in @(890, 390, 320)) {
        Browser set viewport $Width 844
        Check '__checkIssueDistribution(__global.issues); `PASS: issue map at ${innerWidth}px`'
        if ($Width -eq 390) { Browser screenshot (Join-Path $Runtime 'issue-map-mobile.png') }
        Browser click '.issue-map-tile:first-child'
        Browser wait '.issue-category-dialog [data-issue-id]'
        Check 'const dialog = document.querySelector(".issue-category-dialog"); __assert(dialog.querySelectorAll("[data-issue-id]").length === __mapIssues.length && dialog.scrollWidth <= dialog.clientWidth && dialog.getBoundingClientRect().right <= innerWidth && document.documentElement.scrollWidth <= innerWidth, "Category drilldown preserves every member without horizontal overflow"); "PASS: responsive category drilldown"'
        Browser press Escape
    }
    Check 'window.__smallCategory = [...document.querySelectorAll(".issue-map-legend button")].filter(button => !button.disabled).at(-1); __smallCategory.click(); "small category opened from legend"'
    Browser wait '.issue-category-dialog [data-issue-id]'
    Check '__assert(document.querySelectorAll(".issue-category-dialog [data-issue-id]").length === __global.issues.filter(issue => issue.category === __smallCategory.dataset.category).length, "Small categories remain fully accessible from the legend"); "PASS: smallest category drilldown"'
    Browser press Escape
    Browser find role button click --name 'InsightPulse 首页' --exact
    Browser wait '.neutral-category-row'

    # Exercise missing data through local response mocks; no persisted data is changed.
    $Source = Invoke-RestMethod "$BaseUrl/api/chatbi/overview"
    $Mock = @{ data = @{ created_at = $Source.data.created_at; caveat = $Source.data.caveat; global = $Source.data.global } }
    $Global = $Mock.data.global
    foreach ($Property in @('analyzed', 'with_feedback', 'without_feedback', 'pending', 'failed', 'excluded')) { $Global.scope.$Property = 0 }
    $Global.scope.collected = 3
    $Global.scope.pending = 3
    $Global.issues = @()
    foreach ($Category in $Global.categories) { $Category.reviews = 0; $Category.share = $null; $Category.review_ids = @(); $Category.observations = 0; $Category.issues = 0 }
    $Global.recommendation.recommended = 1
    $Global.recommendation.not_recommended = 1
    $Global.recommendation.unknown = 1
    $Global.timeline.dated = 0
    $Global.timeline.undated = 3
    $Global.timeline.start = $null
    $Global.timeline.end = $null
    $Global.timeline.years = @()
    $Global.timeline.months = @()
    Browser network route '**/api/chatbi/overview' --body ($Mock | ConvertTo-Json -Depth 50 -Compress)
    Browser open $BaseUrl
    Browser wait '.overview-chart-empty'
    Check @'
(() => {
  if (![...document.querySelectorAll('.recommendation-readout strong')].every(node => node.textContent === '33.3%')) throw new Error('Unknown recommendations must stay in denominator');
  if (!document.querySelector('.timeline-panel').textContent.includes('3 条日期未知')) throw new Error('Unknown dates must be disclosed');
  if (document.querySelectorAll('.neutral-category-row').length !== 13 || !document.querySelector('.overview-incomplete')) throw new Error('Pending-only data keeps categories and coverage note');
  if (/NaN|Infinity/.test(document.querySelector('.neutral-overview').textContent)) throw new Error('Missing data must not produce invalid numbers');
  return 'PASS: unknown recommendation, unknown dates, pending-only sample';
})()
'@
    Browser click '.neutral-category-row:first-child'
    Browser wait '.overview-detail-dialog .state-box'
    Check 'if (!document.querySelector(".overview-detail-dialog").textContent.includes("该类别暂无已提取的具体问题") || document.querySelector(".overview-category-issues")) throw new Error("Zero category must show empty state"); "PASS: zero category"'
    Browser press Escape
    $Global.scope.collected = 0
    $Global.scope.pending = 0
    $Global.recommendation.recommended = 0
    $Global.recommendation.not_recommended = 0
    $Global.recommendation.unknown = 0
    $Global.timeline.undated = 0
    Browser network unroute '**/api/chatbi/overview'
    Browser network route '**/api/chatbi/overview' --body ($Mock | ConvertTo-Json -Depth 50 -Compress)
    Browser open $BaseUrl
    Browser wait '.overview-chart-empty'
    Check @'
(() => {
  if (![...document.querySelectorAll('.recommendation-readout strong, .category-share')].every(node => node.textContent === '—')) throw new Error('Empty sample has undefined percentages');
  if (document.querySelectorAll('.neutral-category-row').length !== 13 || document.querySelector('[data-recommendation]')) throw new Error('Empty sample keeps category structure without recommendation bars');
  if (document.documentElement.scrollWidth > innerWidth || /NaN|Infinity/.test(document.querySelector('.neutral-overview').textContent)) throw new Error('Empty sample must render cleanly');
  return 'PASS: empty sample';
})()
'@
    Browser network unroute '**/api/chatbi/overview'
    $Capture = Browser network requests --json | Out-String | ConvertFrom-Json
    $ApiRequests = @($Capture.data.requests | Where-Object { $_.url -match '/api/' })
    if (-not $Capture.success -or $ApiRequests.Count -eq 0) { throw 'API request capture is empty' }
    if (@($ApiRequests | Where-Object { $_.method -ne 'GET' -or $_.url -match '/api/chat(?:\?|$)|/api/.*(?:refresh|collect|translate)' }).Count) { throw 'Overview must only read saved data; model/collection requests are forbidden' }
    $Errors = Browser errors --json | Out-String | ConvertFrom-Json
    if (-not $Errors.success -or @($Errors.data.errors).Count) { throw 'Browser reported JavaScript errors' }
    'PASS: neutral charts, scoped drilldowns, assistant context, advanced analysis, responsive layouts, empty/unknown states; no JavaScript errors or mutation/model requests.'
} finally {
    Browser close
}

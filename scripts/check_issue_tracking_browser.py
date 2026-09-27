"""Read-only portfolio UI regression against built assets and real cached data."""
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app
from scripts.check_tracking_browser import browser, evaluate, wait, layout, BROWSER, SESSION


def shell():
    assert evaluate("document.querySelectorAll('aside.sidebar').length") == 1
    assert evaluate("[...document.querySelectorAll('.library-nav > button span')].map(e=>e.textContent)") == [
        "反馈总览", "变化追踪", "决策建议"]
    assert not evaluate("Boolean(document.querySelector('.assistant-workspace,.assistant-launcher,.chat-page,.evidence-page,.app-rail,.analysis-dock'))")
    assert evaluate("[...document.querySelectorAll('.neutral-category-row')].every(row=>row.querySelectorAll('.category-review-mark').length===1 && row.querySelectorAll('.category-issue-mark').length===1)")
    return layout()



def check_category_return(target, title, close):
    """Closing a detail restores its existing category, focus and scroll position."""
    browser("focus", target)
    before = evaluate("""(() => {
        const dialog = document.querySelector('.category-detail-dialog');
        window.__qaCategory = dialog;
        return {title:dialog.querySelector('[data-slot=dialog-title]').textContent,
            scroll:[dialog.scrollTop,dialog.querySelector('.category-issue-list').scrollTop]};
    })()""")
    browser("press", "Enter")
    browser("wait", ".detail-capsule li")
    browser("click", ".detail-manifestation > summary")
    browser("wait", ".detail-manifestation[open] .evidence-card")
    assert evaluate("document.querySelector('.issue-dialog [data-slot=dialog-title]').textContent") == title
    assert evaluate("window.__qaCategory === document.querySelector('.category-detail-dialog') && window.__qaCategory.dataset.state === 'open'")
    if close == "button":
        browser("click", '.issue-dialog > [data-slot="dialog-close"]')
    elif close == "overlay":
        browser("mouse", "move", "10", "10")
        browser("mouse", "down", "left")
        browser("mouse", "up", "left")
    else:
        browser("press", "Escape")
    wait("!document.querySelector('.issue-dialog')")
    wait(f"document.activeElement === document.querySelector({json.dumps(target)})")
    after = evaluate("""(() => {
        const dialog = document.querySelector('.category-detail-dialog');
        return {title:dialog.querySelector('[data-slot=dialog-title]').textContent,
            scroll:[dialog.scrollTop,dialog.querySelector('.category-issue-list').scrollTop]};
    })()""")
    assert before == after, (before, after)
    assert evaluate("window.__qaCategory === document.querySelector('.category-detail-dialog')")
    if target.startswith('.category-issue-bubble'):
        browser("wait", ".category-bubble-tooltip:not([data-state=closed])")
        browser("press", "Escape")
        wait("!document.querySelector('.category-bubble-tooltip:not([data-state=closed])')")
        assert evaluate("Boolean(document.querySelector('.category-detail-dialog'))")
    return {"close": close, "title": title, "focus_restored": True, "scroll_preserved": True}


def check_dialog_return(base, output):
    """Targeted two-game check for button, Escape and outside dismissal."""
    output.mkdir(parents=True, exist_ok=True)
    browser("set", "viewport", "1774", "986")
    report = {}
    for game, category in (("dinner-date", "controls_input"), ("ostranauts", "ui_ux")):
        browser("open", base + "?game=" + game)
        browser("wait", ".neutral-category-row")
        browser("click", f'.neutral-category-row[data-category="{category}"]')
        browser("wait", ".category-issue-row")
        rows = evaluate("""[...document.querySelectorAll('.category-issue-row')].map(n=>({
            id:n.dataset.issueId,title:n.querySelector('.category-issue-name').textContent}))""")
        chosen = next((row for row in rows if '预设' in row['title']), rows[-1])
        cases = []
        for close in ("button", "escape", "overlay"):
            selector = '.category-issue-bubble' if close == "escape" else '.category-issue-row'
            target = f'{selector}[data-issue-id="{chosen["id"]}"]'
            cases.append(check_category_return(target, chosen['title'], close))
        browser("screenshot", str(output / f"{game}-returned.png"))
        browser("press", "Escape")
        wait("!document.querySelector('[role=dialog]')")
        report[game] = cases
    browser("open", base)
    browser("wait", ".neutral-category-row")
    report['errors'] = browser('errors')
    assert not report['errors'].get('errors'), report['errors']
    (output / 'dialog-return-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def check_category_bubbles(base, output):
    """Exercise the real category diagram only; no collection, model call or write."""
    output.mkdir(parents=True, exist_ok=True)
    browser("open", base + "?page=overview")
    browser("wait", ".neutral-category-row")
    source = evaluate("(async()=> (await (await fetch('/api/chatbi/overview')).json()).data.global.issues)()")
    report = {}
    browser("set", "viewport", "1774", "986")
    for category in ("controls_input", "content_narrative", "performance"):
        expected = {issue["id"]: issue for issue in source if issue["category"] == category}
        assert expected
        browser("click", f'.neutral-category-row[data-category="{category}"]')
        browser("wait", ".category-issue-row")
        nodes = evaluate("""[...document.querySelectorAll('.category-issue-bubble')].map(node => ({
            id:node.dataset.issueId,count:Number(node.dataset.reviewCount),radius:Number(node.querySelector('circle').getAttribute('r')),
            label:node.getAttribute('aria-label')}))""")
        rows = evaluate("""[...document.querySelectorAll('.category-issue-row')].map(node => ({
            id:node.dataset.issueId,title:node.querySelector('.category-issue-name').textContent,
            count:Number(node.querySelector('strong').textContent),number:node.querySelector('.category-issue-number').textContent}))""")
        assert len(nodes) == len(rows) == len(expected)
        assert {node["id"] for node in nodes} == {row["id"] for row in rows} == set(expected)
        for row in rows:
            assert (row["title"], row["count"]) == (expected[row["id"]]["title"], expected[row["id"]]["reviews"])
        assert [row["number"] for row in rows] == [f"{index + 1:02}" for index in range(len(rows))]
        ratios = []
        for node in nodes:
            assert node["count"] == expected[node["id"]]["reviews"] and node["radius"] > 0
            assert expected[node["id"]]["title"] in node["label"]
            ratios.append(node["radius"] ** 2 / node["count"])
        assert max(ratios) / min(ratios) - 1 < 1e-6, "Bubble areas must remain proportional to review counts"
        report[category] = {"issues": len(nodes), "proportional_areas": True}
        if category == "controls_input":
            assert len(nodes) == 13
            for width, height, name in ((1774, 986, "desktop"), (390, 844, "mobile")):
                browser("set", "viewport", str(width), str(height))
                wait("[...document.querySelector('.category-detail-dialog').getAnimations()].every(a=>a.playState==='finished')")
                layout()
                dimensions = evaluate("""(() => {
                    const d=document.querySelector('.category-detail-dialog'), r=d.getBoundingClientRect();
                    return {inside:r.left>=0&&r.right<=innerWidth,overflow:d.scrollWidth>d.clientWidth,
                        titles:[...d.querySelectorAll('.category-issue-name')].every(n=>
                            n.scrollWidth<=n.clientWidth+1&&n.scrollHeight<=n.clientHeight+1&&getComputedStyle(n).webkitLineClamp==='none')};
                })()""")
                assert dimensions == {"inside": True, "overflow": False, "titles": True}, dimensions
                browser("screenshot", str(output / f"bubble-{name}.png"))
                report[name] = dimensions
            browser("set", "viewport", "1774", "986")
            for selector, issue in ((".category-issue-bubble", nodes[0]), (".category-issue-row", rows[-1])):
                target = f'{selector}[data-issue-id="{issue["id"]}"]'
                browser("focus", target)
                wait(f"[...document.querySelectorAll('[data-issue-id=\"{issue['id']}\"]')].every(n=>n.dataset.active==='true')")
                if selector == ".category-issue-bubble":
                    browser("wait", ".category-bubble-tooltip:not([data-state=closed])")
                    assert evaluate("document.querySelector('.category-bubble-tooltip:not([data-state=closed]) > strong').textContent") == expected[issue["id"]]["title"]
                check_category_return(target, expected[issue["id"]]["title"], "escape")
        if evaluate("Boolean(document.querySelector('.category-bubble-tooltip:not([data-state=closed])'))"):
            browser("press", "Escape")
            wait("!document.querySelector('.category-bubble-tooltip:not([data-state=closed])')")
        browser("press", "Escape")
        wait("!document.querySelector('[role=dialog]')")
    browser("set", "viewport", "1440", "1000")
    (output / "bubble-check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def check_bubble_tooltips(base, output):
    """Hover and keyboard reveal the matching issue beside its bubble at both widths."""
    output.mkdir(parents=True, exist_ok=True)
    report = {}
    for game, category in (("dinner-date", "controls_input"), ("ostranauts", "ui_ux")):
        for width, height in ((1379, 986), (390, 844)):
            browser("set", "viewport", str(width), str(height))
            browser("open", base + "?game=" + game)
            browser("wait", ".neutral-category-row")
            browser("click", f'.neutral-category-row[data-category="{category}"]')
            browser("wait", ".category-issue-bubble")
            wait("[...document.querySelector('.category-detail-dialog').getAnimations()].every(a=>a.playState==='finished')")
            assert evaluate("document.activeElement === document.querySelector('.category-detail-dialog [data-slot=dialog-title]')")
            assert not evaluate("Boolean(document.querySelector('.category-bubble-tooltip'))"), "Opening a category must not open a tooltip"
            browser("press", "Tab")
            browser("wait", ".category-bubble-tooltip:not([data-state=closed])")
            assert evaluate("document.activeElement.matches('.category-issue-bubble')")
            browser("press", "Escape")
            wait("!document.querySelector('.category-bubble-tooltip:not([data-state=closed])')")
            assert not evaluate("Boolean(document.querySelector('.category-bubble-caption,.category-issue-bubble title'))")
            # A small, rightmost bubble exercises both unreadable-label and viewport-edge cases.
            issue = evaluate("""[...document.querySelectorAll('.category-issue-bubble')]
                .filter(n=>Number(n.dataset.reviewCount)<=3)
                .sort((a,b)=>b.getBoundingClientRect().right-a.getBoundingClientRect().right)
                .map(n=>({id:n.dataset.issueId,count:n.dataset.reviewCount,
                    title:document.querySelector(`.category-issue-row[data-issue-id="${n.dataset.issueId}"] .category-issue-name`).textContent}))[0]""")
            target = f'.category-issue-bubble[data-issue-id="{issue["id"]}"]'
            browser("scrollintoview", target)
            browser("hover", target)
            browser("wait", ".category-bubble-tooltip:not([data-state=closed])")
            wait("[...document.querySelector('.category-bubble-tooltip:not([data-state=closed])').getAnimations()].every(a=>a.playState==='finished')")
            assert evaluate("document.querySelector('.category-bubble-tooltip:not([data-state=closed]) > strong').textContent") == issue['title']
            assert issue['count'] in evaluate("document.querySelector('.category-bubble-tooltip:not([data-state=closed]) > span').textContent")
            placement = evaluate(f"""(() => {{
                const b=document.querySelector({json.dumps(target)}).getBoundingClientRect(),
                    card=document.querySelector('.category-bubble-tooltip:not([data-state=closed])'), t=card.getBoundingClientRect();
                return {{visible:card.contains(document.elementFromPoint(t.left+t.width/2,t.top+t.height/2)),
                    inside:t.left>=0&&t.right<=innerWidth&&t.top>=0&&t.bottom<=innerHeight,
                    near:Math.max(0,b.left-t.right,t.left-b.right)<=16 && Math.max(0,b.top-t.bottom,t.top-b.bottom)<=32}};
            }})()""")
            assert placement == {"visible": True, "inside": True, "near": True}, placement
            layout()
            browser("screenshot", str(output / f"{game}-{width}.png"))
            away = evaluate("(() => { const r=document.querySelector('.category-detail-dialog').getBoundingClientRect(); return {x:r.left+8,y:r.top+8}; })()")
            browser("mouse", "move", str(round(away['x'])), str(round(away['y'])))
            browser("mouse", "move", str(round(away['x'])+8), str(round(away['y'])))
            browser("mouse", "move", str(round(away['x'])+12), str(round(away['y'])+70))
            browser("mouse", "move", str(round(away['x'])+16), str(round(away['y'])+75))
            wait("!document.querySelector('.category-bubble-tooltip:not([data-state=closed])')")
            browser("focus", target)
            browser("wait", ".category-bubble-tooltip:not([data-state=closed])")
            browser("press", "Escape")
            wait("!document.querySelector('.category-bubble-tooltip:not([data-state=closed])')")
            assert evaluate("Boolean(document.querySelector('.category-detail-dialog'))")
            check_category_return(target, issue['title'], "button")
            browser("press", "Escape")
            wait("!document.querySelector('[role=dialog]')")
            report[f"{game}-{width}"] = {**placement, "title": issue['title'], "initially_hidden": True, "keyboard_tab": True, "hover_and_focus": True, "return": True}
            print(f"Tooltip, dismissal and return passed: {game} at {width}px", flush=True)
    browser("set", "viewport", "1440", "1000")
    browser("open", base)
    browser("wait", ".neutral-category-row")
    (output / 'tooltip-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    writes = []

    class ReadOnlyHandler(app.Handler):
        def do_POST(self):
            writes.append(self.path)
            self.api_error("READ_ONLY_QA", "Browser QA must not write or call models", 405)

    with ExitStack() as stack:
        stack.enter_context(patch.object(app.p, "deepseek", side_effect=AssertionError("model called")))
        stack.enter_context(patch.object(app, "collect", side_effect=AssertionError("collection called")))
        server = ThreadingHTTPServer(("127.0.0.1", 0), ReadOnlyHandler)
        stack.enter_context(patch.object(app, "PORT", server.server_port))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}/"
        try:
            # The Windows daemon must not inherit captured output pipes on first launch.
            subprocess.run([BROWSER, "--session", SESSION, "open", base + "?page=tracking"], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
            browser("set", "viewport", "1440", "1000")
            browser("wait", ".tracking-facts")
            assert "56 个问题证据不足" in evaluate("document.body.innerText")
            report = {"navigation": shell()}
            assert not evaluate("Boolean(document.querySelector('.tracking-refresh,.tracking-event-form,.tracking-schedule-form,.tracking-inline-form,.tracking-tabs,.tracking-history-form,.tracking-chart,.tracking-comparison-lines'))")
            assert evaluate("[...document.querySelectorAll('[aria-label=数据视角] option')].map(e=>e.value)") == ["0", "1", "2"]
            browser("click", ".tracking-selection > summary")
            assert evaluate("document.querySelectorAll('.tracking-selection select').length") == 3
            assert evaluate("document.querySelector('[aria-label=基准时点]').value") != evaluate("document.querySelector('[aria-label=对比时点]').value")
            browser("click", ".tracking-selection > summary")
            browser("select", '[aria-label="数据视角"]', "2")
            wait("document.querySelector('.tracking-facts')?.textContent.includes('91')")
            assert "人为分配" in evaluate("document.body.innerText")
            assert "非真实趋势" in evaluate("document.querySelector('.tracking-notice').innerText")
            browser("click", ".tracking-facts tbody tr:first-child button")
            browser("wait", ".tracking-card-evidence")
            assert "规则置信分" in evaluate("document.querySelector('.tracking-change-card').innerText")
            browser("click", ".tracking-card-evidence")
            wait("document.querySelectorAll('.tracking-evidence-side article').length === 10")
            assert evaluate("[...document.querySelectorAll('.tracking-evidence-side')].map(e=>e.querySelectorAll('article').length)") == [5, 5]
            report["desktop"] = layout()
            report["evidence_sides"] = evaluate("[...document.querySelectorAll('.tracking-evidence-side h4')].map(e=>e.innerText)")
            browser("click", ".tracking-evidence-side:first-child article:first-of-type > details > summary")
            assert evaluate("Boolean(document.querySelector('.tracking-evidence-side:first-child article:first-of-type > details[open] blockquote'))")
            first = evaluate("document.querySelector('.tracking-evidence-side:first-child article strong').textContent")
            browser("click", ".tracking-evidence-side:first-child .tracking-pagination button:last-child")
            wait(f"document.querySelector('.tracking-evidence-side:first-child article strong')?.textContent !== {json.dumps(first)}")
            browser("wait", ".tracking-evidence-side:first-child article")
            browser("scroll", "up", "10000")
            browser("screenshot", str(output / "tracking-desktop.png"), "--full")
            browser("screenshot", str(output / "tracking-overview.png"))
            browser("set", "viewport", "390", "844")
            report["mobile"] = layout()
            print("Tracking rules, A/B evidence and mobile layout passed", flush=True)
            browser("screenshot", str(output / "tracking-mobile.png"), "--full")
            browser("click", '[aria-label="打开导航"]')
            wait("document.querySelector('.sidebar.open')?.getBoundingClientRect().left === 0")
            browser("click", ".library-nav button:first-child")
            browser("wait", ".overview-charts")
            assert not evaluate("Boolean(document.querySelector('.sidebar.open'))")
            assert evaluate("document.querySelector('.mobile-menu').getAttribute('aria-expanded')") == "false"
            report["mobile_navigation"] = shell()
            browser("set", "viewport", "1440", "1000")
            assert evaluate("document.querySelectorAll('.overview-charts > .overview-panel').length") == 3
            assert evaluate("[...document.querySelectorAll('.overview-charts h2')].map(e=>e.textContent)") == [
                "整体推荐情况", "反馈类别分布", "评论发表时间"]
            report["overview"] = shell()
            browser("screenshot", str(output / "overview-desktop.png"), "--full")
            report["category_bubbles"] = check_category_bubbles(base, ROOT / "runtime/category-bubbles")
            report["dialog_return"] = check_dialog_return(base, output / "dialog-return")
            report["bubble_tooltips"] = check_bubble_tooltips(base, output / "bubble-tooltips")

            browser("click", ".library-nav button:nth-child(2)")
            wait("document.querySelectorAll('.decision-brief').length === 3")
            assert evaluate("document.querySelectorAll('.decision-validation').length") == 3
            assert evaluate("document.querySelectorAll('.decision-review button').length") == 9
            assert evaluate("document.querySelectorAll('[aria-label=评审原因]').length") == 3
            assert not evaluate("Boolean(document.querySelector('.decision-followup,.decisions-page form,.decision-validation input,.decision-validation textarea,.decision-validation select'))")
            assert "已上线" not in evaluate("document.querySelector('.decision-intro').textContent")
            assert "有结果" not in evaluate("document.querySelector('.decision-intro').textContent")
            browser("click", ".decision-brief:first-child .decision-evidence > summary")
            assert evaluate("document.querySelectorAll('.decision-evidence[open] blockquote').length") > 0
            report["decisions"] = shell()
            browser("screenshot", str(output / "decisions-desktop.png"), "--full")

            for query, page, selector in [
                ("?page=issues", None, ".overview-charts"),
                ("?page=evidence", None, ".overview-charts"),
                ("?page=ask&from=tracking", "tracking", ".tracking-facts"),
                ("?page=ask&from=evidence", None, ".overview-charts"),
            ]:
                browser("open", base + query)
                browser("wait", selector)
                assert evaluate("new URLSearchParams(location.search).get('page')") == page
                shell()
            report["legacy_routes"] = "issues / evidence → overview; ask → valid source page"
            browser("click", ".library-nav button:nth-child(3)")
            browser("wait", ".tracking-facts")
            browser("select", '[aria-label="数据视角"]', "1")
            wait("document.querySelector('.tracking-facts')?.textContent.includes('68')")
            assert "48 个问题证据不足" in evaluate("document.body.innerText")
            report["timepoint_replay"] = layout()

            browser("click", ".library-game:nth-child(2)")
            wait("document.querySelector('.overview-eyebrow')?.textContent.includes('OSTRANAUTS')")
            assert evaluate("document.querySelector('[data-metric=collected]').dataset.count") == "3271"
            report["ostranauts"] = shell()
            browser("click", ".library-nav button:nth-child(3)")
            wait("document.body.innerText.includes('第二次采集')")
            assert not evaluate("Boolean(document.querySelector('.tracking-facts'))")
            report["single_snapshot"] = "waiting for second collection"
            browser("click", ".library-game:first-child")
            wait("document.querySelector('.overview-eyebrow')?.textContent.includes('DINNER DATE')")
            assert evaluate("document.querySelector('[data-metric=collected]').dataset.count") == "830"
            browser("click", ".library-nav button:nth-child(3)")
            browser("wait", ".tracking-facts")
            assert "56 个问题证据不足" in evaluate("document.body.innerText")
            assert evaluate("document.querySelector('[aria-label=数据视角]').value") == "0"
            report["game_switch_back"] = shell()
            report["errors"] = browser("errors")
            assert not report["errors"].get("errors"), report["errors"]
            assert not writes, writes
            report["write_requests"] = writes
            (output / "browser-check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False), flush=True)
        except Exception:
            print(json.dumps(evaluate("({url:location.href,text:document.body.innerText.slice(0,3000)})"), ensure_ascii=False), flush=True)
            browser("screenshot", str(output / "browser-failure.png"), "--full")
            raise
        finally:
            try:
                browser("close")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    run(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "runtime/tracking-browser")

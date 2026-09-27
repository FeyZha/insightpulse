"""Browser -> real HTTP -> domain -> controlled UI. --live uses configured DeepSeek."""
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from unittest.mock import patch
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app
import scripts.check_tracking_browser as browser_tools
from scripts.test_analysis_ui import simulated
from scripts.test_tracking_views import replay

browser_tools.SESSION = f"analysis-ui-qa-{os.getpid()}"
browser, evaluate, wait = browser_tools.browser, browser_tools.evaluate, browser_tools.wait


def main():
    live = "--live" in sys.argv
    app.load_env()
    if live and not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("BLOCKED: no DeepSeek credentials")
    state = app.tracking_history() if live else replay()
    report = {"model": "real DeepSeek" if live else "SIMULATED", "checks": []}
    with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
        stack.enter_context(patch.object(app, "DB", Path(directory) / "analysis.sqlite3"))
        app.init_db()
        stack.enter_context(patch.object(app, "tracking_history", return_value=state))
        if not live:
            stack.enter_context(patch.object(app.p, "deepseek", side_effect=simulated([{"name": "compare"}], comparison=True)))
            stack.enter_context(patch.dict(os.environ, {"DEEPSEEK_API_KEY": "SIMULATED"}))
        server = ThreadingHTTPServer(("127.0.0.1", 0), browser_tools.ReplayHandler)
        stack.enter_context(patch.object(app, "PORT", server.server_port))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        def saved():
            turn_id = evaluate("localStorage.getItem('insightpulse-analysis-turn')")
            with urlopen(url+"/api/analysis/turns/"+turn_id) as response:
                return json.load(response)["data"][-1]
        def send(question, number):
            browser("fill", '[aria-label="输入分析问题"]', question)
            browser("click", '[aria-label="发送消息"]')
            wait(f"document.querySelectorAll('.analysis-answer').length==={number} && !document.querySelector('.tool-loading')")
            browser("snapshot", "-i")
            r = saved()
            assert r["mode"] == "model", r["warnings"]
            report["checks"].append({"question": question, "mode": r["mode"], "filters": r["context"]["filters"],
                "blocks": r["blocks"], "plan": r["plan"], "summary": r["summary"]["text"], "model_calls": r["model_calls"]})
            return r
        def layout():
            data = evaluate("({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,answerWidths:[...document.querySelectorAll('.analysis-answer')].map(e=>({width:e.clientWidth,scroll:e.scrollWidth})),errors:[...document.querySelectorAll('.tool-error,[role=alert]')].map(e=>e.textContent)})")
            assert data["scrollWidth"] <= data["width"], data
            assert all(a["scroll"] <= a["width"]+2 for a in data["answerWidths"]), data
            assert not data["errors"], data
            return data
        try:
            subprocess.run([browser_tools.BROWSER, "--session", browser_tools.SESSION, "open", url+"/?page=ask"],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
            browser("set", "viewport", "1440", "1000")
            browser("wait", '[aria-label="输入分析问题"]')
            assert evaluate("document.querySelector('.chat-page').getBoundingClientRect().width") > 700
            assert evaluate("document.querySelector('.page-content').hidden") is True
            browser("snapshot", "-i")
            first = send("现在大家主要在抱怨什么？", 1)
            if live:
                assert any(b["type"] == "issues" for b in first["blocks"]), first["blocks"]
                browser("focus", '.analysis-evidence-entry > summary'); browser("press", 'Enter')
                browser("wait", ".analysis-evidence article")
                assert evaluate("document.querySelector('.analysis-evidence').textContent.includes('评论 #')")
                browser("focus", '.analysis-evidence-entry > summary'); browser("press", 'Enter')
            second = send("更新前后，哪些反馈变化最大？", 2)
            assert second["blocks"][0]["type"] == "range"
            picker = '.analysis-answer:last-child .analysis-range'  # Messages wrap the tool; use the sole range module.
            picker = '.analysis-answer .analysis-range'
            browser("select", picker+' select[name=before]', state["snapshots"][0]["id"])
            browser("select", picker+' select[name=after]', state["snapshots"][-1]["id"])
            browser("click", picker+' button[type=submit]')
            wait("document.querySelectorAll('.analysis-answer').length===3 && !document.querySelector('.tool-loading')")
            compared = saved()
            assert compared["mode"] == "model" and any(b["type"] == "comparison" for b in compared["blocks"]), compared
            sources = next(d["source_breakdown"] for d in compared["results"].values() if d["kind"] == "comparison")
            assert sources["after_total"] == len(state["snapshots"][-1]["reviews"])
            assert sources["summary"] in evaluate("[...document.querySelectorAll('.analysis-answer')].at(-1).textContent")
            report["checks"].append({"comparison": compared["summary"], "samples": compared["context"]["samples"], "model_calls": compared["model_calls"]})
            report["desktop"] = layout()
            browser("screenshot", str(ROOT / "runtime/analysis-desktop.png"))
            if live:
                checked = send("卡顿真的改善了吗？", 4)
                assert "issue_ids" in checked["context"]["filters"], checked["plan"]
                assert all(row["category"] == "performance" for data in checked["results"].values() for row in data.get("items", [])), checked["plan"]
                chinese = send("只看中文评论", 5)
                assert chinese["context"]["filters"]["language"] == "chinese"
                assert chinese["context"]["filters"]["before"] == checked["context"]["filters"]["before"]
                assert chinese["analysis_question"] == checked["analysis_question"]
                frozen = first["id"]
                with urlopen(url+"/api/analysis/turns/"+frozen) as response:
                    assert json.load(response)["data"][-1] == first
            browser("set", "viewport", "390", "844")
            report["mobile"] = layout()
            browser("screenshot", str(ROOT / "runtime/analysis-mobile.png"))
            count = len(report["checks"])
            browser("reload")
            browser("wait", '.analysis-answer')
            assert evaluate("document.querySelectorAll('.analysis-answer').length") == (5 if live else 3)
            report["restored"] = True
            errors = browser("errors")
            report["browser_errors"] = errors
            assert not errors.get("errors"), errors
            print(json.dumps({"model": report["model"], "checks": count, "restored": True, "desktop": report["desktop"], "mobile": report["mobile"]}, ensure_ascii=True))
        except Exception:
            browser("screenshot", str(ROOT / "runtime/analysis-failure.png"))
            print(json.dumps(evaluate("({scrollY,chat:document.querySelector('.chat-page')?.getBoundingClientRect().toJSON(),summary:document.querySelector('.analysis-evidence-entry summary')?.getBoundingClientRect().toJSON()})")))
            raise
        finally:
            (ROOT / "runtime" / ("analysis-live-browser.json" if live else "analysis-simulated-browser.json")).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            browser("close")
            server.shutdown(); server.server_close(); thread.join(2)


if __name__ == "__main__":
    main()

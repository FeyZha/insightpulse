"""Read-only UI acceptance. Fixture exists only in this ephemeral test server."""
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import app
import tracking
from scripts.test_issue_tracking import scenario
from scripts.check_tracking_browser import browser,evaluate,wait,layout,BROWSER,SESSION


def run():
    output=ROOT/"runtime/tracking-validation/browser"
    output.mkdir(parents=True,exist_ok=True)
    build=ROOT/"runtime/ostranauts-full-build"
    assert (build/"index.html").is_file(), "Build into runtime/ostranauts-full-build first"
    original=app.product_state
    fixtures=False
    state=dict(scenario(1,2,10,10),game="tracking-fixture")
    for snap in state["snapshots"]:
        snap.pop("tracking_basis")
    state=tracking.prepare_reanalysis(state,state,{"label":"TEST FIXTURE fixed definitions","files":[{"path":"fixture"}]})
    for snap in state["snapshots"]:
        snap["label"]="TEST FIXTURE / "+snap["id"]
        snap["demo"]=True
    def product(game):
        if fixtures and game=="ostranauts":
            return dict(state,game=game,snapshots=state["snapshots"][:1])
        return state if fixtures and game=="dinner-date" else original(game)
    class Handler(app.Handler):
        def do_POST(self):
            if fixtures and self.path == "/api/tracking/analyze":
                body=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                return self.ok(app.tracking_views.comparison(state, {"before":body["before"],"after":body["after"]}))
            self.api_error("READ_ONLY_QA","Real model operations are disabled in UI checks",405)
        def serve_frontend(self,path):
            file=(build/path.lstrip("/")).resolve()
            if not file.is_relative_to(build.resolve()): raise ValueError("Invalid path")
            if not file.is_file(): file=build/"index.html"
            mime=mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            self.send(file.read_bytes(),mime=mime+"; charset=utf-8" if mime.startswith(("text/","application/javascript")) else mime)
    report={}
    with ExitStack() as stack:
        stack.enter_context(patch.object(app,"product_state",side_effect=product))
        stack.enter_context(patch.object(app.p,"deepseek",side_effect=AssertionError("model called")))
        stack.enter_context(patch.object(app,"collect",side_effect=AssertionError("collection called")))
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        stack.enter_context(patch.object(app,"PORT",server.server_port))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f"http://127.0.0.1:{server.server_port}/?page=tracking"
        try:
            subprocess.run([BROWSER,"--session",SESSION,"open",url],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=45)
            for width,height in ((1440,1000),(390,844)):
                browser("set","viewport",str(width),str(height))
                browser("open",url)
                browser("wait", ".tracking-analyze")
                browser("wait", ".tracking-comparison-chart")
                assert not evaluate("Boolean(document.querySelector('.tracking-attention,.tracking-demo-picker'))")
                report[f"real-{width}"]=layout()
                browser("screenshot",str(output/f"real-{width}.png"))
                if width==390:
                    browser("click",".mobile-menu")
                    wait("document.querySelector('.sidebar.open') && document.querySelector('.sidebar').getAnimations().every(a=>a.playState==='finished')")
                browser("click",'.library-games > button:nth-child(2)')
                browser("wait", ".neutral-category-row")
                if width==390:
                    browser("click", ".mobile-menu")
                    wait("document.querySelector('.sidebar.open') && document.querySelector('.sidebar').getAnimations().every(a=>a.playState==='finished')")
                tab=evaluate("[...document.querySelectorAll('.library-nav > button')].findIndex(b=>b.textContent.includes('变化追踪'))+1")
                browser("click", f".library-nav > button:nth-child({tab})")
                other=tracking.catalog(app.tracking_views.tracking_state(ROOT,original("ostranauts")))
                if other["status"]=="ready":
                    browser("wait", ".tracking-comparison-chart")
                    assert str(other["snapshots"][-1]["analyzed"]) in evaluate("document.querySelector('.tracking-chart-legend').textContent")
                else:
                    wait("document.querySelector('.tracking-page')?.textContent.includes('等待第二次采集')")
                    assert not evaluate("Boolean(document.querySelector('.tracking-comparison-chart,.tracking-periods'))")
                report[f"waiting-{width}"]=layout()
                browser("screenshot",str(output/f"waiting-{width}.png"))
                print(f"Real data and game switch passed at {width}px",flush=True)
            fixtures=True
            for width,height in ((1440,1000),(390,844)):
                browser("set","viewport",str(width),str(height))
                browser("open",url+"&game=dinner-date")
                browser("wait",".tracking-analyze")
                browser("wait", ".tracking-comparison-chart")
                browser("click",".tracking-analyze")
                browser("wait",".tracking-comparison-chart")
                assert '基于历史原文的统一重分析' in evaluate("document.querySelector('.tracking-page').textContent")
                assert '10.0' in evaluate("document.querySelector('.tracking-comparison-chart').textContent")
                assert evaluate("[...document.querySelectorAll('.tracking-chart-row circle')].map(e=>e.getAttribute('cx'))")==['50%', '100%']
                assert evaluate("document.querySelector('.tracking-chart-row').getAttribute('aria-label')").startswith('游戏崩溃，A 1 条，10.0%；B 2 条，20.0%')
                browser("scrollintoview",".tracking-section-head")
                browser("screenshot",str(output/f"chart-{width}.png"))
                browser("click",".tracking-chart-row")
                wait("document.querySelectorAll('.tracking-quote').length===3")
                assert evaluate("[...document.querySelectorAll('.tracking-evidence-side')].map(e=>e.querySelectorAll('.tracking-quote').length)")==[1,2]
                browser("click",".tracking-quote details > summary")
                assert evaluate("[...document.querySelectorAll('.tracking-evidence-side')].every(e=>{const r=e.getBoundingClientRect();return r.left>=0 && r.right<=innerWidth && e.scrollWidth<=e.clientWidth})")
                report[f"fixture-{width}"]=layout()
                browser("screenshot",str(output/f"fixture-{width}.png"))
                browser("select",'select[aria-label="上次保存时点"]',"b")
                wait("document.querySelector('.tracking-page')?.textContent.includes('A 应早于 B')")
                assert not evaluate("Boolean(document.querySelector('.tracking-comparison-chart,.tracking-quote'))")
                print(f"Fixture chart, evidence and selection passed at {width}px",flush=True)
                browser("open",url+"&game=ostranauts")
                wait("document.querySelector('.tracking-page')?.textContent.includes('等待第二次采集')")
                assert not evaluate("Boolean(document.querySelector('.tracking-comparison-chart,.tracking-quote'))")
                report[f"single-snapshot-{width}"]=layout()
            errors=browser("errors")
            assert not errors.get("errors"),errors
            report["console_errors"]=errors
            (output/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        except Exception:
            print(evaluate("({url:location.href,text:document.body.innerText.slice(-3500)})"),flush=True)
            browser("screenshot",str(output/"failure.png"))
            raise
        finally:
            browser("close")
            server.shutdown();server.server_close();thread.join(timeout=2)
    return report

if __name__=="__main__":run()

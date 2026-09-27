"""Browser check using real read-only sources and in-memory review/outcome writes."""
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
from pathlib import Path
import subprocess
import threading
from unittest.mock import patch
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import app
from scripts.check_tracking_browser import browser,evaluate,wait,layout,BROWSER,SESSION

def run():
    saved={}
    original=app.cache_get
    output=ROOT/'runtime/decision-followup-validation'
    output.mkdir(parents=True,exist_ok=True)
    with ExitStack() as stack:
        stack.enter_context(patch.dict('os.environ', {'INSIGHTPULSE_FRONTEND_DIR':'runtime/change-focus-build'}))
        stack.enter_context(patch.object(app,'cache_get',side_effect=lambda key:saved[key] if key in saved else original(key)))
        stack.enter_context(patch.object(app,'cache_put',side_effect=lambda key,value:saved.update({key:value})))
        stack.enter_context(patch.object(app.p,'deepseek',side_effect=AssertionError('model called')))
        stack.enter_context(patch.object(app,'collect',side_effect=AssertionError('collection called')))
        server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        stack.enter_context(patch.object(app,'PORT',server.server_port))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{server.server_port}/?page=decisions&game=dinner-date'
        try:
            subprocess.run([BROWSER,'--session',SESSION,'open',url],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            for width,height in ((1440,1000),(390,844)):
                browser('set','viewport',str(width),str(height));browser('open',url)
                browser('wait','.decision-comparison-value')
                assert evaluate("document.querySelectorAll('.decision-comparison-value').length")==3
                assert '34.0% → 15.5%，差 -18.46 个百分点' in evaluate("document.querySelector('.decision-sample-summary').textContent")
                browser('click','.decision-comparison .text-action')
                browser('wait','.tracking-quote')
                wait("document.querySelectorAll('.tracking-evidence-side').length===2")
                layout();browser('screenshot',str(output/f'evidence-{width}.png'))
                if not evaluate("Boolean(document.querySelector('.decision-outcome'))"):
                    browser('click','.decision-review button:first-child');browser('wait','.decision-outcome')
                browser('click','.decision-outcome summary')
                browser('fill','.decision-outcome textarea',f'TEST ONLY {width} action')
                browser('click','.decision-outcome button[type=submit]')
                wait("document.querySelector('.decision-outcome summary')?.textContent.includes('已保存')")
                browser('open',url);browser('wait','.decision-outcome');browser('click','.decision-outcome summary')
                assert evaluate("document.querySelector('.decision-outcome textarea').value")==f'TEST ONLY {width} action'
                layout();browser('screenshot',str(output/f'outcome-{width}.png'))
                browser('open',url.replace('dinner-date','ostranauts'))
                browser('wait','.decisions-page')
                assert not evaluate("Boolean(document.querySelector('.tracking-quote,.decision-outcome'))")
                wait("!document.querySelector('.decisions-page')?.textContent.includes('正在读取样本对照')")
                assert not evaluate("document.querySelector('.decisions-page').textContent.includes('TEST ONLY')")
                layout()
                print(f'{width}px: comparison, evidence, isolated save/reload and game isolation passed',flush=True)
            assert not browser('errors').get('errors')
        finally:
            browser('close');server.shutdown();server.server_close();thread.join(timeout=2)
if __name__=='__main__':run()


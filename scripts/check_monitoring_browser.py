"""Read-only acceptance of the paused demo; no models, collection or review writes."""
import subprocess,sys,json
from pathlib import Path
from urllib.request import urlopen
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.check_tracking_browser import browser,evaluate,wait,layout,BROWSER,SESSION
BASE=sys.argv[1].rstrip('/') if len(sys.argv)>1 else 'http://127.0.0.1:8771'
OUT=ROOT/'runtime/change-focus-validation';OUT.mkdir(exist_ok=True)
for game in ['ostranauts','dinner-date']:
 data=json.load(urlopen(BASE+'/api/tracking/monitoring?game='+game))['data']
 assert data['latest']['comparable']
subprocess.run([BROWSER,'--session',SESSION,'open',BASE+'/?page=tracking&game=ostranauts'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
 for width,height in [(1440,1000),(390,844)]:
  browser('set','viewport',str(width),str(height))
  for game in ['ostranauts','dinner-date']:
   browser('open',BASE+'/?page=overview&game='+game)
   browser('wait','.neutral-category-row')
   layout()
   category='controls_input' if game=='dinner-date' else 'ui_ux'
   browser('click',f'.neutral-category-row[data-category="{category}"]')
   browser('wait','.category-issue-row')
   first=evaluate("document.querySelector('.category-issue-row').dataset.issueId")
   browser('click',f'.category-issue-row[data-issue-id="{first}"]')
   browser('wait','.detail-capsule li')
   browser('click','.detail-manifestation > summary')
   browser('wait','.detail-manifestation[open] .evidence-card')
   layout()
   browser('open',BASE+'/?page=decisions&game='+game)
   browser('wait','.decision-comparison-value')
   assert evaluate("document.querySelectorAll('.decision-brief').length")==3
   browser('click','.decision-comparison .text-action')
   browser('wait','.tracking-quote')
   layout()
   browser('open',BASE+'/?page=tracking&game='+game)
   browser('wait','.change-row')
   assert evaluate("document.querySelectorAll('.change-row').length")==12
   assert evaluate("document.querySelectorAll('.monitoring-matrix').length")==0
   layout();browser('screenshot',str(OUT/f'{game}-{width}.png'))
   browser('click','.change-footer button')
   assert evaluate("document.querySelectorAll('.change-row').length")==36
   browser('click','.change-row:nth-child(2)')
   browser('wait','.tracking-quote')
   wait("document.querySelectorAll('.recharts-line-curve').length===1")
   layout()
   browser('click','.monitoring-history summary')
   browser('click','.monitoring-history button:first-child')
   browser('wait','.tracking-quote')
   layout()
   browser('click','.monitoring-close')
   browser('fill','.monitoring-toolbar input','zzzz-not-found')
   assert evaluate("document.querySelectorAll('.change-row').length")==0
  print('PASS',width,flush=True)
 print(browser('errors'),flush=True)
except Exception:
 browser('screenshot',str(OUT/'failure.png'))
 print(evaluate("({url:location.href,text:document.body.innerText.slice(-1800)})"),flush=True)
 print(browser('errors'),flush=True)
 raise
finally: browser('close')

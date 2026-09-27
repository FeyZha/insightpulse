"""Offline fixed-definition fixture demo. Writes only the explicitly selected report."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import app
import tracking
from scripts.test_issue_tracking import scenario


def report():
    real={}
    for game in app.GAMES:
        state=app.tracking_views.tracking_state(ROOT,app.product_state(game))
        catalog=tracking.catalog(state)
        real[game]={k:catalog[k] for k in ("status","selection","reasons","snapshots")}
    examples=[]
    for args in ((10,20,100,200),(0,2,10,10),(2,0,10,10),(1,2,10,10)):
        result=tracking.compare(scenario(*args),"a","b")
        examples.append(dict(label="人为构造测试 fixture，非真实玩家记录",result=result))
    return dict(comparison_version=tracking.COMPARISON_VERSION,real=real,fixtures=examples)

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    text=json.dumps(report(),ensure_ascii=False,indent=2)
    if args.output:
        destination=args.output.resolve()
        allowed=(ROOT/"runtime"/"tracking-validation").resolve()
        if not destination.is_relative_to(allowed):
            parser.error("报告仅可写入 runtime/tracking-validation，禁止混入真实快照或分析缓存")
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_text(text,encoding="utf-8")
        print(destination)
    else:
        print(text)

"""Offline regression checks; no model credentials or network calls required.
Run: python test_pipeline.py
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
from urllib.error import HTTPError

import app
import evaluation
import pipeline as p


def review(rid, created, text="The game crashes when loading a save."):
    return dict(id=str(rid), text=text, created=created, updated=created, language="english",
                recommended=True, steam_purchase=True, url=f"https://steamcommunity.com/profiles/123/recommended/553850/")


def rejects(fn):
    try:
        fn()
    except (ValueError, KeyError):
        return
    raise AssertionError("Invalid data accepted")


event = 1700000000
reviews = [review(i, event-1000+i) for i in range(40)] + [review(i, event+i) for i in range(40,80)]
snapshot = dict(reviews=reviews+[reviews[0]], coverage="bounded_recent_sample", captured_at=p.utc(event+90000),
                earliest_created=event-90000, app_id=553850)
snapshot["reviews"] += [dict(review("edited", event), updated=event+1), review("blank",event," "),
                        review("long",event,"x"*12001),review("outside",event+86400)]
plan = p.prepare(snapshot, event, 1, 400)
assert len(plan["selected"]) == 80 and plan["window_covered"]
assert plan["eligible"] == {"before":40,"after":40}
assert plan["excluded"] == {"edited":1,"empty":1,"too_long":1,"outside":1}
assert p.prepare(snapshot,event,1,12)["selected"] == p.prepare(snapshot,event,1,12)["selected"]
assert len(p.prepare(snapshot,event,1,12)["selected"]) == 12
assert not p.prepare(dict(snapshot,captured_at=p.utc(event)),event,1,12)["window_covered"]
rejects(lambda:p.prepare(snapshot,event,0,12))
assert [len(b) for b in p.review_batches(reviews[:12],5)]==[5,5,2]
assert [r['id'] for b in p.review_batches(reviews,20) for r in b]==[r['id'] for r in reviews]
assert [len(b) for b in p.review_batches([dict(r,text='abcd') for r in reviews[:5]],20,9)]==[2,2,1]
assert list(p.review_batches([],20))==[]
rejects(lambda:list(p.review_batches(reviews,0)))
rejects(lambda:list(p.review_batches(reviews,True)))
issue = {"aspect":"Stability","title":"读取存档时崩溃","evidence":"crashes when loading a save","severity":"high"}
raw = json.dumps({"reviews":[{"id":"0","issues":[issue,issue]}]})
assert len(p.validate_extraction(raw,[reviews[0]])["0"]) == 1
rejects(lambda:p.validate_extraction(raw,[reviews[1]]))
rejects(lambda:p.validate_extraction('{"reviews":[]}',[reviews[0]]))
rejects(lambda:p.validate_extraction(json.dumps({"reviews":[{"id":"0","issues":[dict(issue,evidence="fabricated quote")]}]}),[reviews[0]]))
opinion = dict(issue,id="0:0",review_id="0")
assert p.validate_groups(json.dumps({"groups":[{"title":"读取存档时崩溃","aspect":"Stability","opinion_ids":["0:0"]}]}),[opinion])
rejects(lambda:p.validate_groups('{"groups":[]}',[opinion]))
rejects(lambda:p.validate_groups(json.dumps({"groups":[{"title":"错误合并","aspect":"Network","opinion_ids":["0:0"]}]}),[opinion]))
rejects(lambda:p.validate_groups(json.dumps({"groups":[{"title":"重复成员","aspect":"Stability","opinion_ids":["0:0","0:0"]}]}),[opinion]))

# Whitespace repair restores a unique original span, never punctuation or meaning.
assert p.align_evidence("crashes after updates", "It crashes  after\r\nupdates.") == "crashes  after\r\nupdates"
assert p.align_evidence("更新 后崩溃", "更新\t后崩溃") == "更新\t后崩溃"
rejects(lambda:p.align_evidence("a b", "a  b; a\tb"))
rejects(lambda:p.align_evidence("crashes often", "does not crash often"))
rejects(lambda:p.align_evidence("crashes. Often", "crashes! Often"))
rejects(lambda:p.align_evidence("ab", "a b"))
rejects(lambda:p.partition_extraction(json.dumps({"reviews":[{"id":"0","issues":[]},{"id":"0","issues":[]}]}),reviews[:2]))
rejects(lambda:p.partition_extraction(json.dumps({"reviews":[{"id":[],"issues":[]}]}),reviews[:1]))
rejects(lambda:p.partition_extraction('{"reviews":[{"id":"unknown","issues":[]}]}',reviews[:1]))
rejects(lambda:p.partition_extraction('{"reviews":null}',reviews[:1]))

# Missing IDs are not empty complaints. Reordered valid rows survive even if retry fails.
for retry_mode in ("recover", "omit", "error", "unknown"):
    requested, records = [], []
    def missing_model(prompt, payload, validator, audit, attempts=2):
        requested.append([r["id"] for r in payload])
        if len(requested) == 1:
            rows = [{"id": r["id"], "issues": [issue]} for r in reversed(payload) if r["id"] != "2"]
        else:
            assert attempts == 1
            if retry_mode == "error":
                raise ValueError("DeepSeek HTTP 503")
            rows = ([{"id": "2", "issues": []}] if retry_mode == "recover" else
                    [{"id": "unknown", "issues": []}] if retry_mode == "unknown" else [])
        content = json.dumps({"reviews": rows})
        result = validator(content)
        audit({"status": "passed", "content": content})
        return result
    with patch.object(p, "deepseek", missing_model):
        good, failed = p.extract_batch("json", reviews[:5], records.append)
    assert requested == [["0", "1", "2", "3", "4"], ["2"]]
    assert records[0]["review_failures"] == {"2": "模型未返回该评论 ID"}
    assert all(good[rid] == [issue] for rid in ("0", "1", "3", "4"))
    assert set(good) | set(failed) == {"0", "1", "2", "3", "4"}
    assert not set(good) & set(failed)
    assert (good.get("2") == [] and not failed) if retry_mode == "recover" else set(failed) == {"2"}

# One invalid quote cannot erase the other four valid reviews or re-bill them.
requests=[]
def isolated_model(prompt,payload,validator,audit,attempts=2):
    requests.append([r["id"] for r in payload])
    content=json.dumps({"reviews":[{"id":r["id"],"issues":[dict(issue,evidence="fabricated") if r["id"]=="2" else issue]} for r in payload]})
    result=validator(content)
    audit({"status":"passed","seconds":0,"content":content})
    return result
with patch.object(p,"deepseek",isolated_model):
    good,failed=p.extract_batch("json",reviews[:5],lambda _:None)
assert set(good)=={"0","1","3","4"} and set(failed)=={"2"}
assert requests==[["0","1","2","3","4"],["2"]]

requests=[]
audit=[]
def recovering_model(prompt,payload,validator,record,attempts=2):
    requests.append(payload)
    content=json.dumps({"reviews":[{"id":r["id"],"issues":[dict(issue,evidence="fabricated") if r["id"]=="2" and len(payload)>1 else issue]} for r in payload]})
    result=validator(content)
    record({"status":"passed","content":content,"attempt":1,"seconds":0,"usage":{"total_tokens":20}})
    return result
with patch.object(p,"deepseek",recovering_model):
    good,failed=p.extract_batch("json",reviews[:5],audit.append)
assert len(good)==5 and not failed and audit[0]["review_failures"]=={"2":"证据不是唯一可定位的原文片段"}
assert len(requests)==2

# A whole echoed batch is one retry request, not twenty single-review requests.
requests=[]
def echoed_batch_model(prompt,payload,validator,audit,attempts=2):
    requests.append([r['id'] for r in payload])
    content=json.dumps({'reviews':payload if len(requests)==1 else [{'id':r['id'],'issues':[]} for r in payload]})
    output=validator(content)
    audit({'status':'passed','content':content})
    return output
with patch.object(p,'deepseek',echoed_batch_model):
    good,failed=p.extract_batch('json',reviews[:20],lambda _:None)
assert len(good)==20 and not failed and len(requests)==2
assert requests[0]==requests[1]

requests=[]
def grouped_model(prompt,payload,validator,audit):
    aspects={o["aspect"] for o in payload}
    assert len(aspects)==1
    requests.append(payload)
    return validator(json.dumps({"groups":[{"title":"待调查问题","aspect":next(iter(aspects)),"opinion_ids":[o["id"] for o in payload]}]}))
with patch.object(p,"deepseek",grouped_model):
    separated=p.group_opinions("json",[opinion,dict(opinion,id="1:0",aspect="Content")],lambda _:None)
assert len(requests)==2 and len(separated)==2
rejects(lambda:p.group_opinions("json",[dict(opinion,aspect="Invalid")],lambda _:None))

run = dict(id="offline-test",dataset="synthetic",title="测试更新",app_id=553850,plan=plan,status="ready",model="test-double",
           extractions={r["id"]:[] for r in reviews},model_extractions={"0":[issue]},
           opinions=[opinion,dict(opinion,id="0:1"),dict(opinion,id="40:0",review_id="40")],
           groups=[dict(id="group",title="读取存档时崩溃",aspect="Stability",opinion_ids=["0:0","0:1","40:0"])],
           failures={},decisions={},removed_opinions=[],manual_reviews=[],edits=[],calls=[],
           prompt_version=p.PROMPT_VERSION,group_version=p.GROUP_VERSION)
stats=p.metrics(run)
assert stats["issues"][0]["counts"] == {"before":1,"after":1}
assert stats["issues"][0]["delta_pp"] == 0
run["removed_opinions"]=["0:0","0:1"]
assert p.metrics(run)["issues"][0]["counts"]["before"]==0
assert p.metrics(run)["issues"][0]["trend"]=="此前未观察到"
rejects(lambda:app.report(run))
run["decisions"]={"group":{"status":"confirmed","note":"Check save loading"}}
assert "0 → 后 1" in app.report(run)
assert "不代表全部玩家" in app.report(run)
gold=evaluation.prepare_gold(snapshot)
assert len(gold["reviews"])==60
assert sum(r["split"]=="dev" for r in gold["reviews"])==20
assert all(r["expected"] is None for r in gold["reviews"])
assert evaluation.score(gold,run,"holdout")["status"]=="incomplete"
one_gold={"reviews":[dict(id="0",split="holdout",text=reviews[0]["text"],text_hash=p.fingerprint(reviews[0]["text"]),
                          expected=[dict(issue,issue="存档崩溃")],reviewer="test fixture")]}
assert evaluation.score(one_gold,run,"holdout")["micro_aspect"]["f1"]==1
assert evaluation.score(one_gold,dict(run,model_extractions={}),"holdout")["micro_aspect"]["recall"]==0

@contextmanager
def response(content, finish="stop"):
    import io
    yield io.StringIO(json.dumps({"choices":[{"finish_reason":finish,"message":{"content":content}}],
                                 "usage":{"total_tokens":20},"model":"test-double"}))

with patch.dict(os.environ,{"DEEPSEEK_API_KEY":"test-only-not-a-key","DEEPSEEK_MODEL":"deepseek-v4-flash"}):
    calls=[]
    with patch.object(p,"urlopen",lambda *a,**kw:response(raw)):
        assert p.deepseek("json",[],lambda c:p.validate_extraction(c,[reviews[0]]),calls.append)
    assert len(calls)==1 and calls[0]["status"]=="passed"
    calls=[]
    with patch.object(p,"urlopen",lambda *a,**kw:response(raw,"length")),patch.object(p.time,"sleep",lambda _:None):
        rejects(lambda:p.deepseek("json",[],lambda c:{},calls.append))
    assert len(calls)==2 and all(c["status"]=="failed" for c in calls)
    calls=[]
    with patch.object(p,"urlopen",side_effect=HTTPError("test",401,"Unauthorized",{},None)):
        rejects(lambda:p.deepseek("json",[],lambda c:{},calls.append))
    assert len(calls)==1

with tempfile.TemporaryDirectory() as folder, patch.object(app,"DB",Path(folder)/"test.sqlite3"):
    app.init_db();app.save(run)
    loaded=app.get_run(run["id"])
    app.manual_review(loaded,{"review_id":"40","issues":[]})
    loaded=app.get_run(run["id"])
    assert "group" not in loaded["decisions"]
    assert loaded["model_extractions"]==run["model_extractions"]
    assert loaded["extractions"]["40"]==[]
    assert loaded["manual_reviews"]==["40"]
    assert p.metrics(loaded)["issues"][0]["counts"]["after"]==0
    fresh=dict(run,id="model-workflow",status="draft",extractions={},model_extractions={},opinions=[],groups=[],
               decisions={},manual_reviews=[],removed_opinions=[],edits=[],calls=[],failures={},cache_hits=0,
               extract_prompt=p.EXTRACT_PROMPT,group_prompt=p.GROUP_PROMPT)
    fresh["plan"]=p.prepare(snapshot,event,1,12)
    def fake_deepseek(prompt,payload,validator,audit):
        if prompt==p.EXTRACT_PROMPT:
            content=json.dumps({"reviews":[{"id":r["id"],"issues":[issue]} for r in payload]})
        else:
            content=json.dumps({"groups":[{"title":"读取存档时崩溃","aspect":"Stability","opinion_ids":[o["id"] for o in payload]}]})
        output=validator(content)
        audit({"status":"passed","seconds":0,"model":"test-double","attempt":1,"usage":{"total_tokens":10}})
        return output
    with patch.object(p,"deepseek",fake_deepseek):
        app.JOB_LOCK.acquire();app.analyze(fresh)
    loaded=app.get_run("model-workflow")
    assert loaded["status"]=="ready" and len(loaded["model_extractions"])==12
    assert p.metrics(loaded)["issues"][0]["counts"]=={"before":6,"after":6}
    assert len(loaded["calls"])==3
print("PASS: data boundaries, sampling, evidence validation, grouping integrity, deduped metrics, export gates, independent eval, bounded retries, manual persistence")

from scripts import benchmark_batches as benchmark
from scripts.check_semantic_controls import check as check_controls
from scripts.score_submitted_review import presence
submitted=[{'id':'a','judgment':'有具体投诉'},{'id':'b','judgment':'无具体投诉'},{'id':'c','judgment':'不确定'}]
presence_result=presence(submitted,{'a':[issue],'b':[],'c':[issue]})
assert presence_result['assessed']==2 and presence_result['accuracy']==1 and presence_result['excluded_uncertain_ids']==['c']
assert presence(submitted,{'a':[issue]})['status']=='incomplete'
control=[{'id':'c','expected_count':0,'expected_aspects':[]}]
assert check_controls(control,{'c':[]})['passed']==1
assert check_controls(control,{})['passed']==0
assert check_controls(control,{'c':[issue]})['passed']==0
critical=[{'id':'c','expected_count':1,'expected_aspects':['Stability'],'required_severity':'critical'}]
assert check_controls(critical,{'c':[issue]})['passed']==0
priced={'model':'deepseek-v4-flash','started_at':'2026-09-09T17:00:00+00:00',
        'usage':{'prompt_tokens':1000,'completion_tokens':100,'prompt_cache_hit_tokens':800}}
assert abs(benchmark.cost(priced)-0.0001156)<1e-12
assert benchmark.cost(dict(priced,started_at='2026-09-09T02:00:00+00:00'))==2*benchmark.cost(priced)
assert benchmark.cost(priced,True)>benchmark.cost(priced)
with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'DEEPSEEK_API_KEY':'test-only-not-a-key'}):
    original_dir=Path.cwd()
    try:
        os.chdir(folder)
        Path('evaluation').mkdir()
        Path('source.json').write_text(json.dumps({'id':'fixture','extract_prompt':p.EXTRACT_PROMPT,'plan':{'selected':reviews[:5]}}),encoding='utf-8')
        with patch.object(benchmark,'load_env',lambda:None),patch.object(p,'deepseek',recovering_model),patch.object(benchmark.sys,'argv',['benchmark','source.json','--sizes','5','10','--rounds','2']):
            benchmark.main()
        artifact=json.loads(next(Path('evaluation').glob('batch-benchmark-*.json')).read_text(encoding='utf-8'))
        assert artifact['status']=='complete' and len(artifact['runs'])==4
        assert all(r['summary']['passed']==5 and r['summary']['retry_requests']==1 for r in artifact['runs'])
    finally:
        os.chdir(original_dir)

from scripts.replay_processing import replay
saved=Path(__file__).resolve().parent/'runtime/exports/InsightPulse-64830772184d88b0-20260909T172228931196Z.json'
if saved.exists():
    replayed=replay(json.loads(saved.read_text(encoding='utf-8')))
    assert replayed['strict_batch_passed']==110 and replayed['repaired_passed']==120
    assert len(replayed['evidence_repairs'])==2 and replayed['new_api_requests']==0
    print('PASS: saved live-response replay, 110 -> 120; two whitespace alignments; no network')

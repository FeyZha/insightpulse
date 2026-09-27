"""Approved Ostranauts full run; resumable extraction, original files remain immutable."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import app
import data_flow_v2 as flow
import pipeline
import tracking

FOLDER=ROOT/'runtime/ostranauts-full-test'

def save(path,value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
    temporary.replace(path)

def extract():
    app.load_env()
    pre=json.loads((FOLDER/'preflight.json').read_text(encoding='utf-8'))
    product=json.loads(app.chatbi_source('ostranauts').read_text(encoding='utf-8'))
    paths=([Path(p) for p in json.loads((FOLDER/'sources.json').read_text(encoding='utf-8'))['paths']]
           if (FOLDER/'sources.json').exists() else [Path(product['source']['file']),Path(pre['current_path'])])
    raws=[json.loads(p.read_text(encoding='utf-8')) for p in paths]
    import hashlib
    assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]==[pre['historical_sha256'],pre['current_sha256']]
    dataset=flow.load_snapshots(raws)
    original=app.product_state('ostranauts')
    checkpoint=FOLDER/'extractions.json'
    contract=pipeline.fingerprint([pre['historical_sha256'],pre['current_sha256'],tracking.MODEL,flow.OBSERVATION_PROMPT])
    state=json.loads(checkpoint.read_text(encoding='utf-8')) if checkpoint.exists() else dict(contract=contract,cache=original['cache'],calls=[],failures={})
    assert state['contract']==contract
    versions=dataset['review_versions']
    excluded={vid:'empty_text' if not row['text'].strip() else 'overlong_text' for vid,row in versions.items() if not row['text'].strip() or len(row['text'])>12000}
    pending=[vid for vid,row in versions.items() if vid not in excluded and tracking._cache_key(row) not in state['cache']]
    save(FOLDER/'sources.json',dict(paths=[str(p) for p in paths],excluded=excluded,model=tracking.MODEL))
    print(f'pending={len(pending)} excluded={len(excluded)} reused={len(state["cache"])}',flush=True)
    batch_size = 1 if state["failures"] else 10
    batches=[pending[i:i+batch_size] for i in range(0,len(pending),batch_size)]
    def batch(ids):
        calls=[]
        _,failures,cache=flow.extract_versions(dataset,ids,tracking.MODEL,audit=calls.append)
        return calls,failures,cache
    # ponytail: four requests at once; reduce workers if provider throttles.
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(batch,ids) for ids in batches]
        for future in as_completed(futures):
            calls,failures,cache=future.result()
            state['cache'].update(cache);state['calls'].extend(calls);state['failures'].update(failures)
            state['failures']={vid:error for vid,error in state['failures'].items() if tracking._cache_key(versions[vid]) not in state['cache']}
            save(checkpoint,state)
            print(f'extracted_cache={len(state["cache"])} failures={len(state["failures"])} calls={len(state["calls"])}',flush=True)
    if state['failures']:
        raise ValueError(f'{len(state["failures"])} failed versions; checkpoint saved')
    print('EXTRACTION COMPLETE',flush=True)



def group():
    import os
    import vector_issue_experiment as vector
    from scripts.regroup_ostranauts_issues import group_all
    app.load_env()
    sources=json.loads((FOLDER/'sources.json').read_text(encoding='utf-8'))
    source=Path(sources['paths'][0])
    dataset=flow.load_snapshots([json.loads(source.read_text(encoding='utf-8'))])
    state=json.loads((FOLDER/'extractions.json').read_text(encoding='utf-8'))
    records=[]
    for version in dataset['review_versions'].values():
        if not version['text'].strip():
            continue
        value=state['cache'].get(tracking._cache_key(version))
        if value is None:
            raise ValueError('Historical extraction incomplete')
        records.append(dict(version,**value))
    observations=vector.observations_from_records(records,dataset)
    save(FOLDER/'historical-observations.json',dict(records=records,observations=observations))
    checkpoint=FOLDER/'historical-issues.json'
    prior=json.loads(checkpoint.read_text(encoding='utf-8')).get('api_calls',[]) if checkpoint.exists() else []
    receipts={(c.get('prompt_hash'),c.get('input_hash'),c.get('model')):c['content'] for c in prior if c.get('status')=='passed' and c.get('content')}
    request=pipeline.deepseek
    def resume(prompt,payload,validator,audit,**kwargs):
        key=(pipeline.fingerprint(prompt),pipeline.fingerprint(payload),kwargs.get('model'))
        if key in receipts:
            return validator(receipts[key])
        print(f"group request rows={len(payload)} cached={len(receipts)}",flush=True)
        return request(prompt,payload,validator,audit,**kwargs)
    from unittest.mock import patch
    with patch.object(pipeline,'deepseek',side_effect=resume):
        grouped,issues=group_all(observations,os.environ.get('DEEPSEEK_ISSUE_MODEL','deepseek-v4-pro'),source=source,checkpoint=checkpoint)
    save(FOLDER/'historical-analysis.json',dict(records=records,observations=observations,issues=issues,issue_group=dict(version=flow.ISSUE_GROUP_VERSION,model=grouped['model'])))
    print(f'GROUPING COMPLETE observations={len(observations)} issues={len(issues)}',flush=True)


def compare():
    from copy import deepcopy
    import tempfile
    app.load_env()
    source=json.loads((FOLDER/'sources.json').read_text(encoding='utf-8'))
    grouped=json.loads((FOLDER/'historical-analysis.json').read_text(encoding='utf-8'))
    extracted=json.loads((FOLDER/'extractions.json').read_text(encoding='utf-8'))
    state=deepcopy(app.product_state('ostranauts'))
    with tempfile.TemporaryDirectory() as directory:
        data=Path(directory)/'data';data.mkdir()
        for name in source['paths']:
            path=Path(name);(data/path.name).write_bytes(path.read_bytes())
        state['snapshots']=tracking._snapshots(directory,state,1022980)
    state['cache']=extracted['cache']
    state['issues']={row['issue_id']:{k:row[k] for k in ('issue_id','category','title','canonical_description')} for row in grouped['issues']}
    state['assignments']={}
    rows={r['observation_id']:r for r in grouped['observations']}
    for issue in grouped['issues']:
        for oid in issue['observation_ids']:
            row=rows[oid]
            state['assignments'][tracking._signature(row['review_id'],row)]=issue['issue_id']
    state['analysis_version']=pipeline.fingerprint([flow.PROMPT_VERSION,state['issues']])[:20]
    state['failures']={}
    for snap in state['snapshots']:
        snap['excluded_empty_reviews']={rid:v['review_version_id'] for rid,v in snap['reviews'].items() if not v['text'].strip()}
    derived=tracking.prepare_reanalysis(state,state,dict(label='Ostranauts 历史全量有效原文建立的冻结问题目录',files=[dict(path=str(FOLDER/'historical-analysis.json'),sha256=__import__('hashlib').sha256((FOLDER/'historical-analysis.json').read_bytes()).hexdigest())]))
    checkpoint=FOLDER/'tracking-analysis.json'
    if checkpoint.exists():
        saved=json.loads(checkpoint.read_text(encoding='utf-8'))
        assert saved['issues']==derived['issues'] and tracking._originals(saved)==tracking._originals(derived)
        derived=saved
    try:
        tracking.complete_reanalysis(derived)
    finally:
        save(checkpoint,derived)
    result=tracking.compare(derived,*[snap['id'] for snap in derived['snapshots']])
    save(FOLDER/'comparison.json',result)
    assert result['comparable'],result['reasons']
    print('COMPARISON COMPLETE',len(result['issues']),flush=True)


def publish():
    import hashlib
    import shutil
    from collections import Counter
    from datetime import datetime,timezone
    import vector_issue_experiment as vector
    state=json.loads((FOLDER/'tracking-analysis.json').read_text(encoding='utf-8'))
    result=tracking.compare(state,*[snap['id'] for snap in state['snapshots']])
    assert result['comparable'],result['reasons']
    sources=json.loads((FOLDER/'sources.json').read_text(encoding='utf-8'))
    folder=app.game_config('ostranauts')['folder']
    current=Path(sources['paths'][1]);destination=folder/current.name
    if destination.exists():
        assert destination.read_bytes()==current.read_bytes()
    else:
        shutil.copyfile(current,destination)
    raw=json.loads(current.read_text(encoding='utf-8'));dataset=flow.load_snapshots([raw])
    records=[dict(v,**state['cache'][tracking._cache_key(v)]) for v in dataset['review_versions'].values() if v['text'].strip()]
    observations=vector.observations_from_records(records,dataset)
    issues=[]
    for definition in state['issues'].values():
        members=[r for r in observations if state['assignments'].get(tracking._signature(r['review_id'],r))==definition['issue_id']]
        if members:
            issues.append(dict(definition,observation_ids=[r['observation_id'] for r in members],review_ids=sorted({r['review_id'] for r in members})))
    stamp=datetime.now(timezone.utc).isoformat()
    product=dict(schema_version='ostranauts-product-v1',created_at=stamp,processing_version=flow.PROCESSING_VERSION,prompt_version=flow.PROMPT_VERSION,model=state['model'],game='ostranauts',
        source=dict(file=str(destination),snapshot_id=raw['snapshot']['snapshot_id'],observed_at=raw['snapshot']['observed_at'],sha256=hashlib.sha256(current.read_bytes()).hexdigest()),
        records=records,observations=observations,issues=issues,category_observation_counts=dict(Counter(r['category'] for r in observations)),category_issue_counts=dict(Counter(r['category'] for r in issues)),
        summary=dict(reviews=len(raw['reviews']),analyzed_reviews=len(records),none_reviews=sum(not r['observations'] for r in records),failed_reviews=0,observations=len(observations),final_issues=len(issues)),
        issue_group_version=flow.ISSUE_GROUP_VERSION,issue_group=dict(version=flow.ISSUE_GROUP_VERSION,mode='frozen_historical_definitions'),quality_filters={})
    path=folder/'product-zzz-full-20260927.json'
    original=app.product_state('ostranauts')
    assert tracking._originals(state)==tracking._originals(original)
    tracking.save_reanalysis(ROOT,state)
    save(path,product)
    print('PUBLISHED',path,flush=True)


def translate():
    import review_translation
    app.load_env()
    sources=json.loads((FOLDER/'sources.json').read_text(encoding='utf-8'))
    cache=json.loads((FOLDER/'extractions.json').read_text(encoding='utf-8'))['cache']
    items=[]
    for path in sources['paths']:
        for row in json.loads(Path(path).read_text(encoding='utf-8'))['reviews']:
            if not row['text'].strip():
                continue
            items.append(dict(text=row['text'],language=row['language']))
            for obs in cache.get(tracking._cache_key(row),{}).get('observations',[]):
                for key in ('evidence','change_evidence'):
                    if obs[key]:
                        items.append(dict(text=obs[key],language=row['language'],context=row['text']))
    result=review_translation.translate(ROOT,items,progress=lambda message:print(message,flush=True))
    save(FOLDER/'translation-result.json',result)
    print('TRANSLATION',result,flush=True)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=['extract','group','compare','publish','translate'],default='extract')
    args=parser.parse_args()
    {'extract':extract,'group':group,'compare':compare,'publish':publish,'translate':translate}[args.stage]()

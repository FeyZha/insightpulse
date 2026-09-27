import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {demoFetch} from '../src/lib/demo-api.ts';

const requests=[];
globalThis.fetch=async path=>{
  assert.ok(path==='/demo-data.json'||/^\/fixtures\/[a-f0-9]+\.json$/.test(path),'Only bundled static files may be fetched');
  requests.push(path);
  return Response.json(JSON.parse(await readFile(new URL('../public'+path,import.meta.url),'utf8')));
};
const storage=new Map();
globalThis.localStorage={getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value)};
const call=async(path,body)=>demoFetch(path,body?{method:'POST',body:JSON.stringify(body)}:{});
for(const game of ['dinner-date','ostranauts']){
  const overview=(await (await call('/api/chatbi/overview?game='+game)).json()).data;
  assert.ok(overview.global.issues.length);
  const decisions=(await (await call('/api/chatbi/decisions?game='+game)).json()).data;
  if(decisions.decisions.length){
    const id=decisions.decisions[0].id;
    assert.equal((await call('/api/chatbi/decisions/review',{game,decision_id:id,status:'modified',reason:''})).status,422);
    const edited=(await (await call('/api/chatbi/decisions/review',{game,decision_id:id,status:'accepted',reason:''})).json()).data;
    assert.equal(edited.decisions[0].review.status,'accepted');
    const saved=(await (await call('/api/chatbi/decisions?game='+game)).json()).data;
    assert.equal(saved.decisions[0].review.status,'accepted');
  }
}
const index=JSON.parse(await readFile(new URL('../public/demo-data.json',import.meta.url),'utf8'));
const path=Object.keys(index).find(p=>p.includes('/monitoring/evidence?'));
assert.equal((await call(path+'&page_offset=-1')).status,422);
assert.equal((await call('/api/chatbi/decisions/generate',{game:'dinner-date'})).status,422);
assert.equal((await call('/api/nonexistent')).status,404);
assert.ok(requests.length<20,'Load selected routes, not the complete dataset');
console.log('Demo: both games, lazy fixture loading, local review persistence, validation and no model requests OK');

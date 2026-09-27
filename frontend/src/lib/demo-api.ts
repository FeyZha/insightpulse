type Json = Record<string, any>
let fixture: Promise<Record<string, string>> | undefined
const key = (path: string) => {
  const url = new URL(path, 'https://demo.local');
  url.searchParams.delete('limit'); url.searchParams.delete('page_offset');
  if (url.pathname === '/api/tracking/monitoring/evidence') url.searchParams.delete('side');
  if (!url.searchParams.has('game')) url.searchParams.set('game', 'dinner-date');
  url.searchParams.sort(); return url.pathname + '?' + url.searchParams;
}
const fail = (message: string, status = 422) => Response.json({error:{message}}, {status});
export async function demoFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const data = await (fixture ??= fetch('/demo-data.json').then(async r => {
    if (!r.ok) throw new Error('演示数据加载失败');
    return Object.fromEntries(Object.entries(await r.json()).map(([k,v]) => [key(k),v as string]));
  }));
  if (options.signal?.aborted) throw new DOMException('Aborted','AbortError');
  if (path === '/api/state') return Response.json({token:'local-demo'});
  const body = options.body ? JSON.parse(String(options.body)) : {};
  const game = body.game || new URL(path,'https://demo.local').searchParams.get('game') || 'dinner-date';
  const decisionKey = key('/api/chatbi/decisions?game='+game);
  const load = async (route: string): Promise<Json | undefined> => {
    const file = data[route];
    if (!file) return undefined;
    const response = await fetch(file, {signal:options.signal});
    if (!response.ok) throw new Error('演示数据加载失败，请重试');
    return response.json();
  };
  if (options.method === 'POST') {
    if (!['/api/chatbi/decisions/review','/api/chatbi/decisions/outcome'].includes(path)) return fail('此操作没有预制结果，公开演示不调用模型。');
    const current = JSON.parse(localStorage.getItem('insightpulse-demo:'+game) || JSON.stringify(await load(decisionKey)));
    const brief = current.decisions.find((item: Json) => item.id === body.decision_id);
    if (!brief) return fail('未找到决策',404);
    if (path.endsWith('/review')) {
      if (!['pending','accepted','modified','rejected'].includes(body.status) || ['modified','rejected'].includes(body.status) && !body.reason?.trim()) return fail('请填写有效的评审状态和原因');
      brief.review = {...brief.review,status:body.status,reason:body.reason || ''};
    } else {
      if (!['accepted','modified'].includes(brief.review.status) || !body.final_action?.trim() || !['planned','shipped','cancelled'].includes(body.execution?.status)) return fail('请先采纳建议并填写有效行动');
      if (body.execution.status === 'shipped' && !Number.isFinite(Date.parse(body.execution.shipped_at))) return fail('请填写有效上线时间');
      brief.review.final_action = body.final_action;
      brief.execution = body.execution;
      brief.validation = {...brief.validation,...body.validation};
      brief.outcome_tracking.updated_at = new Date().toISOString();
    }
    localStorage.setItem('insightpulse-demo:'+game,JSON.stringify(current));
    return Response.json({data:current});
  }
  let value = await load(key(path));
  if (key(path) === decisionKey) value = JSON.parse(localStorage.getItem('insightpulse-demo:'+game) || JSON.stringify(value));
  if (!value) return fail('此内容尚未包含在预制数据中',404);
  if (path.includes('/evidence?')) {
    const params = new URL(path,'https://demo.local').searchParams;
    const offset = Number(params.get('page_offset') || 0), limit = Number(params.get('limit') || 5);
    if (!Number.isInteger(offset) || offset < 0 || !Number.isInteger(limit) || limit < 1 || limit > 50) return fail('分页参数无效');
    value = {...value,items:value.items.slice(offset,offset+limit)};
  }
  return Response.json({data:value});
}
